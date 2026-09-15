import re
import sys
import yaml
import argparse
import subprocess
from pathlib import Path

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

TOOLS_DIR = Path(__file__).resolve().parent
SPEC_FILE = "apis/spec-v2.openapi.yaml"
SCHEMAS_DIR = "apis/schemas"
CREATE_SUFFIX = "Create"

SUPPORTED_FLAGS = frozenset({
    "x-sdk-create", "x-sdk-get", "x-sdk-query", "x-sdk-page", "x-sdk-log",
    "x-sdk-delete", "x-sdk-update", "x-sdk-pdf", "x-sdk-cancel", "x-sdk-data-only",
})

SUPPRESSED = {
    "x-sdk-put": "Rest.put nao existe no sdk-java",
}


def loadFast(text: str):
    """libyaml quando disponivel: a spec tem 7.131 linhas e o parser puro custa 138 ms."""
    return yaml.load(text, Loader=SafeLoader)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def runExtractor(resource: str, root: str | None, *extra: str) -> tuple[int, str]:
    command = [sys.executable, str(TOOLS_DIR / "extract-schema.py"), resource, *extra]
    if root:
        command.extend(["--from", root])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        for line in (result.stdout + result.stderr).splitlines():
            if line.strip():
                emit(f"    {line}")
    return (result.returncode, result.stdout)


def schemaBlock(lines: list[str], name: str) -> tuple[int, int] | None:
    start = None
    for index, line in enumerate(lines):
        if start is None and line.rstrip("\n") == f"    {name}:":
            start = index
            continue
        if start is not None and line[:5].strip() and not line.startswith("     "):
            return (start, index)
        if start is not None and line.startswith("    ") and not line.startswith("     ") and line.strip():
            return (start, index)
    if start is not None:
        return (start, len(lines))
    return None


def kebab(resource: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", resource).lower()


def operationBlock(verb: str, operationId: str, resource: str, withId: bool, body: bool) -> list[str]:
    lines = [f"    {verb}:", f"      operationId: {operationId}", "      tags:", f"      - {resource}s"]

    if withId:
        lines.extend([
            "      parameters:",
            "      - name: id",
            "        in: path",
            "        required: true",
            "        schema:",
            "          type: string",
        ])

    if body:
        lines.extend([
            "      requestBody:",
            "        required: true",
            "        content:",
            "          application/json:",
            "            schema:",
            "              type: array",
            "              items:",
            f"                $ref: '#/components/schemas/{resource}{CREATE_SUFFIX}'",
        ])

    lines.extend([
        "      responses:",
        "        '200':",
        "          description: Success",
        "          content:",
        "            application/json:",
        "              schema:",
        f"                $ref: '#/components/schemas/{resource}'",
        "        '401':",
        "          description: Unauthorized",
        "        '500':",
        "          description: Server error",
    ])
    return lines


def pathsBlock(resource: str, flags: list[str]) -> list[str]:
    """Paths derivados das flags: o que a spec declara reflete o que o Python expõe."""
    collection, single = [], []

    if "x-sdk-create" in flags:
        collection.extend(operationBlock("post", f"create{resource}", resource, False, True))
    if "x-sdk-query" in flags or "x-sdk-page" in flags:
        collection.extend(operationBlock("get", f"query{resource}", resource, False, False))
    if "x-sdk-get" in flags:
        single.extend(operationBlock("get", f"get{resource}", resource, True, False))
    if "x-sdk-update" in flags:
        single.extend(operationBlock("patch", f"update{resource}", resource, True, False))
    if "x-sdk-delete" in flags or "x-sdk-cancel" in flags:
        single.extend(operationBlock("delete", f"delete{resource}", resource, True, False))

    lines = []
    if collection:
        lines.append(f"  /{kebab(resource)}:")
        lines.extend(collection)
    if single:
        lines.append(f"  /{kebab(resource)}/{{id}}:")
        lines.extend(single)
    return [f"{line}\n" for line in lines]


def refBlock(name: str, relativePath: str, flags: list[str]) -> list[str]:
    lines = [f"    {name}:\n"]
    lines.extend(f"      {flag}: true\n" for flag in flags)
    lines.append(f"      $ref: {relativePath}#/components/schemas/{name}\n")
    return lines


def insertUnder(lines: list[str], header: str, block: list[str]) -> list[str] | None:
    """Insere logo abaixo da chave, aceitando tanto `chave:` quanto `chave: {}`."""
    index = next(
        (number for number, line in enumerate(lines) if line.rstrip("\n") in (header, f"{header} {{}}")),
        None,
    )
    if index is None:
        return None

    return lines[:index] + [f"{header}\n"] + block + lines[index + 1 :]


def insertSchemas(lines: list[str], resource: str, relativePath: str, flags: list[str]) -> list[str] | None:
    block = refBlock(resource, relativePath, flags) + refBlock(f"{resource}{CREATE_SUFFIX}", relativePath, [])
    return insertUnder(lines, "  schemas:", block)


def insertPaths(lines: list[str], resource: str, flags: list[str]) -> list[str] | None:
    return insertUnder(lines, "paths:", pathsBlock(resource, flags))


def applyToSpec(specPath: Path, resource: str, relativePath: str, flags: list[str], declared: bool) -> str | None:
    lines = specPath.read_text(encoding="utf-8").splitlines(keepends=True)

    if not declared:
        updated = insertPaths(lines, resource, flags)
        if updated is None:
            return None
        updated = insertSchemas(updated, resource, relativePath, flags)
        return "".join(updated) if updated else None

    for name, ownFlags in ((f"{resource}{CREATE_SUFFIX}", []), (resource, flags)):
        span = schemaBlock(lines, name)
        if span is None:
            return None
        start, end = span
        lines[start:end] = refBlock(name, relativePath, ownFlags)

    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aplica na spec o schema extraido do SDK Python")
    parser.add_argument("resource", help="nome da classe, ex: DictKey")
    parser.add_argument("--from", dest="root", default=None, help="raiz do SDK Python")
    parser.add_argument("--spec", default=SPEC_FILE, help=f"spec a alterar (padrão: {SPEC_FILE})")
    parser.add_argument("--schemas", default=SCHEMAS_DIR, help=f"diretório dos schemas (padrão: {SCHEMAS_DIR})")
    args = parser.parse_args()

    specPath = Path(args.spec)
    schemasDir = Path(args.schemas)
    if not specPath.is_file():
        emit(f"[ERROR] spec não encontrada: {specPath}")
        return 2
    if not schemasDir.is_dir():
        emit(f"[ERROR] diretório de schemas não encontrado: {schemasDir}")
        return 2

    try:
        spec = loadFast(specPath.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        emit(f"[ERROR] spec não é YAML válido, nada foi alterado: {error}")
        return 2

    declared = args.resource in (((spec or {}).get("components") or {}).get("schemas") or {})

    code, document = runExtractor(args.resource, args.root)
    if code != 0:
        emit(f"[ERROR] extração falhou para {args.resource}, nada foi alterado")
        return 1

    code, rendered = runExtractor(args.resource, args.root, "--operations")
    if code != 0:
        emit(f"[ERROR] operações não derivaveis para {args.resource}, nada foi alterado")
        return 1

    derived = loadFast(rendered) or {}
    flags = sorted(flag for flag in derived if flag in SUPPORTED_FLAGS)
    for flag in sorted(flag for flag in derived if flag not in SUPPORTED_FLAGS):
        emit(f"[WARN] {flag} suprimido: {SUPPRESSED.get(flag, 'sem suporte no template')}")

    if not flags:
        emit(f"[ERROR] nenhuma operação suportada para {args.resource}, nada foi alterado")
        return 1

    target = schemasDir / f"{args.resource.lower()}.yaml"
    relativePath = f"./{schemasDir.name}/{target.name}"
    updated = applyToSpec(specPath, args.resource, relativePath, flags, declared)
    if updated is None:
        emit(f"[ERROR] bloco de schema não localizado para {args.resource}, nada foi alterado")
        return 1

    try:
        loadFast(updated)
    except yaml.YAMLError as error:
        emit(f"[ERROR] resultado não é YAML válido, nada foi alterado: {error}")
        return 2

    target.write_text(document, encoding="utf-8")
    specPath.write_text(updated, encoding="utf-8")

    emit(f"[OK] {target} gravado")
    action = "enriquecido" if declared else "criado"
    emit(f"[OK] {args.resource} {action} na spec: {', '.join(flags)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
