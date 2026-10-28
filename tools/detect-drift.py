import sys
import yaml
import argparse
import importlib.util
import subprocess
from pathlib import Path

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
SCHEMAS_DIR = "apis/schemas"
SPEC_FILE = "apis/spec-v2.openapi.yaml"
DOCS_KEY = "x-sdk-docs"

EXIT_IN_SYNC = 0
EXIT_DRIFTED = 1
EXIT_ERROR = 2
EXIT_NOT_APPLIED = 3


def loadFast(text: str):
    """libyaml quando disponivel: a spec tem 7.131 linhas e o parser puro custa 138 ms."""
    return yaml.load(text, Loader=SafeLoader)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def extract(resource: str, root: str | None) -> tuple[int, str]:
    command = [sys.executable, str(TOOLS_DIR / "extract-schema.py"), resource]
    command += ["--from", root] if root else []
    result = subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT)
    return (result.returncode, result.stdout if result.returncode == 0 else result.stdout + result.stderr)


def loadApplySchema():
    spec = importlib.util.spec_from_file_location("apply_schema", TOOLS_DIR / "apply-schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extractOperations(resource: str, root: str | None) -> tuple[int, dict]:
    command = [sys.executable, str(TOOLS_DIR / "extract-schema.py"), resource, "--operations"]
    command += ["--from", root] if root else []
    result = subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT)
    if result.returncode != 0:
        return (result.returncode, {"output": result.stdout + result.stderr})
    return (0, loadFast(result.stdout) or {})


def operationFlags(entry: dict) -> dict:
    """So o que o apply-schema grava na spec: flags com suporte no template e a entidade do Log."""
    tracked = loadApplySchema()
    known = tracked.SUPPORTED_FLAGS | tracked.VALUED_FLAGS
    return {key: value for key, value in (entry or {}).items() if key in known}


def compareOperations(resource: str, fromPython: dict, fromSpec: dict) -> list[str]:
    """Operacao nova, removida ou renomeada no sdk-python muda o que o Java deve emitir."""
    differences = []
    expected, declared = operationFlags(fromPython), operationFlags(fromSpec)
    for flag in sorted(set(expected) | set(declared)):
        if flag not in declared:
            differences.append(f"{resource}.{flag}: o sdk-python exporta, a spec nao declara")
        elif flag not in expected:
            differences.append(f"{resource}.{flag}: a spec declara, o sdk-python nao exporta mais")
        elif str(expected[flag]) != str(declared[flag]):
            differences.append(f"{resource}.{flag}: {declared[flag]} → {expected[flag]}")
    return differences


def schemasOf(document: dict) -> dict:
    return ((document or {}).get("components") or {}).get("schemas") or {}


def flattenDocs(docs: dict | None) -> dict:
    """`log` aninha as operacoes do Log: `log.get` e `get` sao docstrings diferentes."""
    flat = {}
    for name, entry in (docs or {}).items():
        if name != "log":
            flat[name] = entry
            continue
        flat.update({f"log.{inner}": value for inner, value in flattenDocs(entry).items()})
    return flat


def compareDocs(resource: str, fromPython: dict | None, fromSpec: dict | None) -> list[str]:
    """Um docstring que muda no sdk-python muda o Javadoc que o gerador emite."""
    differences = []
    expected, declared = flattenDocs(fromPython), flattenDocs(fromSpec)
    for operation in sorted(set(expected) | set(declared)):
        prefix = f"{resource}.x-sdk-docs.{operation}"
        if operation not in declared:
            differences.append(f"{prefix}: o sdk-python documenta, o schema versionado nao tem")
            continue
        if operation not in expected:
            differences.append(f"{prefix}: o schema versionado documenta, o sdk-python nao documenta mais")
            continue
        changed = [key for key in sorted(set(expected[operation]) | set(declared[operation]))
                   if expected[operation].get(key) != declared[operation].get(key)]
        if changed:
            differences.append(f"{prefix}: docstring mudou ({', '.join(changed)})")
    return differences


def compare(extracted: dict, applied: dict) -> list[str]:
    """Diferenças por schema e por campo, em linha, para caber no corpo de uma PR."""
    differences = []
    fromPython, fromSpec = schemasOf(extracted), schemasOf(applied)

    for name in sorted(set(fromPython) | set(fromSpec)):
        if name not in fromSpec:
            differences.append(f"schema novo no sdk-python: {name}")
            continue
        if name not in fromPython:
            differences.append(f"schema ausente no sdk-python: {name}")
            continue

        pythonProps = (fromPython[name] or {}).get("properties") or {}
        specProps = (fromSpec[name] or {}).get("properties") or {}
        for field in sorted(set(pythonProps) | set(specProps)):
            if field not in specProps:
                differences.append(f"{name}.{field}: campo novo no sdk-python")
                continue
            if field not in pythonProps:
                differences.append(f"{name}.{field}: campo saiu do sdk-python")
                continue
            if pythonProps[field] != specProps[field]:
                differences.append(f"{name}.{field}: {specProps[field]} → {pythonProps[field]}")

        for key in sorted(set(fromPython[name] or {}) | set(fromSpec[name] or {})):
            if key in ("properties", DOCS_KEY):
                continue
            if (fromPython[name] or {}).get(key) != (fromSpec[name] or {}).get(key):
                differences.append(f"{name}.{key}: "
                                   f"{(fromSpec[name] or {}).get(key)} → {(fromPython[name] or {}).get(key)}")

        differences += compareDocs(name, (fromPython[name] or {}).get(DOCS_KEY),
                                  (fromSpec[name] or {}).get(DOCS_KEY))

    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara o schema versionado com o sdk-python atual")
    parser.add_argument("resource", help="nome da classe, ex: Invoice")
    parser.add_argument("--from", dest="root", default=None, help="raiz do SDK Python")
    parser.add_argument("--schemas", default=SCHEMAS_DIR, help=f"diretório dos schemas (padrão: {SCHEMAS_DIR})")
    parser.add_argument("--spec", default=SPEC_FILE, help=f"spec com as flags de operação (padrão: {SPEC_FILE})")
    args = parser.parse_args()

    applied = REPO_ROOT / args.schemas / f"{args.resource.lower()}.yaml"
    if not applied.is_file():
        emit(f"[INFO] {args.resource} não tem schema versionado em {args.schemas} — nada a comparar")
        return EXIT_NOT_APPLIED

    code, output = extract(args.resource, args.root)
    if code != 0:
        emit(f"[ERROR] extração falhou para {args.resource}")
        for line in output.splitlines():
            emit(f"    {line}")
        return EXIT_ERROR

    differences = compare(loadFast(output), loadFast(applied.read_text(encoding="utf-8")))

    code, operations = extractOperations(args.resource, args.root)
    if code != 0:
        emit(f"[ERROR] operações não derivaveis para {args.resource}")
        for line in operations["output"].splitlines():
            emit(f"    {line}")
        return EXIT_ERROR
    spec = loadFast((REPO_ROOT / args.spec).read_text(encoding="utf-8"))
    specEntry = (schemasOf(spec).get(args.resource) or {})
    differences += compareOperations(args.resource, operations, specEntry)
    if not differences:
        emit(f"[OK] {args.resource}: schema versionado em sincronia com o sdk-python")
        return EXIT_IN_SYNC

    emit(f"[ERROR] {args.resource}: {len(differences)} divergência(s) entre o sdk-python e {applied}")
    for difference in differences:
        emit(f"    {difference}")
    return EXIT_DRIFTED


def guarded() -> int:
    """Exit 1 significa defasagem para o workflow; falha inesperada nao pode ser lida assim."""
    try:
        return main()
    except Exception as error:
        emit(f"[ERROR] comparação falhou: {type(error).__name__}: {error}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(guarded())
