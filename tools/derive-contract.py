import os
import re
import sys
import argparse
import subprocess
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

TOOLS_DIR = Path(__file__).resolve().parent
REFERENCES = Path("_references")
EXIT_ABSENT_UPSTREAM = 3

MARKERS = {
    "java": "src/main/java/com/starkbank",
    "node": "sdk",
}

_JAVA_IMPORT = re.compile(r"^import (?:static )?[\w.]+;$")
_JAVA_DECLARATION = re.compile(r"^public (?:final )?(?:abstract )?class \w+(?: extends [\w.<>]+)?(?: implements [\w.<>, ]+)?")
_JAVA_INNER = re.compile(r"^public (?:final )?static class \w+(?: extends [\w.<>]+)?")
_JAVA_FIELD = re.compile(r"^(?:public|static) [\w.<>\[\],? ]+ ?\w+\s*(?:=[^;]+)?;$")
_JAVA_SIGNATURE = re.compile(r"^public static [\w.<>\[\],? ]+ [\w.]+\(")
_JAVA_CONSTRUCTOR = re.compile(r"^public (\w+)\(")

_NODE_REQUIRE = re.compile(r"^const [\w{}, ]+ = require\(")
_NODE_DECLARATION = re.compile(r"^class \w+(?: extends \w+)?")
_NODE_EXPORT = re.compile(r"^exports\.\w+ = ")
_TS_SIGNATURE = re.compile(r"^export function \w+\(")
_TS_NAMESPACE = re.compile(r"^export namespace \w+")
_TS_INNER = re.compile(r"^export class \w+")


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def referenceRepo(language: str, explicit: str | None = None) -> Path | None:
    """Raiz do repositório de referência: explícita, depois SDK_<LANG>, depois _references/."""
    marker = MARKERS[language]
    fromEnv = os.environ.get(f"SDK_{language.upper()}")

    if explicit:
        candidates = [Path(explicit)]
    else:
        candidates = ([Path(fromEnv)] if fromEnv else []) + [REFERENCES / f"sdk-{language}"]

    for candidate in candidates:
        if (candidate / marker).is_dir():
            return candidate
    return None


def sourcePath(repo: Path, resource: str, language: str, role: str) -> Path | None:
    placeGenerated = loadTool("place-generated")
    for name, _, target in placeGenerated.LAYOUTS.get(language, []):
        if name != role:
            continue
        relative = target.format(Resource=resource, resource=placeGenerated.varName(resource))
        return repo / relative
    return None


def joinContinuations(source: str) -> list[str]:
    """Junta assinatura quebrada em várias linhas até fechar o parêntese."""
    joined, buffer, depth = [], "", 0
    for raw in source.splitlines():
        line = raw.strip()
        if depth:
            buffer += " " + line
        else:
            buffer = line
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            joined.append(re.sub(r"\s+([),])", r"\1", " ".join(buffer.split())))
            depth = 0
    return joined


def headOf(line: str) -> str:
    """Corpo fora, declaração dentro: em JS o `{` também abre destructuring."""
    return re.sub(r"\s*\{\s*$", "", line).rstrip(";").strip()


def signatureOf(line: str) -> str:
    return headOf(line).replace(" throws Exception", "")


def extractJava(source: str) -> list[tuple[str, str]]:
    entries = []
    for line in joinContinuations(source):
        if _JAVA_IMPORT.match(line):
            entries.append(("require", line))
        elif _JAVA_INNER.match(line):
            entries.append(("inner", headOf(line)))
        elif _JAVA_DECLARATION.match(line):
            entries.append(("declaration", headOf(line)))
        elif _JAVA_SIGNATURE.match(line):
            entries.append(("signature", signatureOf(line)))
        elif _JAVA_CONSTRUCTOR.match(line):
            entries.append(("constructor", headOf(line)))
        elif _JAVA_FIELD.match(line):
            entries.append(("field", line))
    return entries


def extractNode(source: str) -> list[tuple[str, str]]:
    entries = []
    for line in joinContinuations(source):
        if _NODE_REQUIRE.match(line):
            entries.append(("require", headOf(line)))
        elif _NODE_DECLARATION.match(line):
            entries.append(("declaration", headOf(line)))
        elif _NODE_EXPORT.match(line):
            entries.append(("export", headOf(line)))
        elif _TS_SIGNATURE.match(line):
            entries.append(("signature", headOf(line)))
        elif _TS_NAMESPACE.match(line):
            entries.append(("namespace", headOf(line)))
        elif _TS_INNER.match(line):
            entries.append(("inner", headOf(line)))
    return entries


EXTRACTORS = {
    "java": extractJava,
    "node": extractNode,
}


def repoSha(repo: Path) -> str:
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "desconhecido"


def render(resource: str, language: str, role: str, relative: str,
           entries: list[tuple[str, str]], sha: str = "desconhecido") -> str:
    lines = [f"# source:    {relative}", f"# sha:       starkbank/sdk-{language}@{sha}",
             f"# derived:   {resource} ({language}, papel {role})", ""]
    for kind, value in entries:
        padding = " " * max(1, 12 - len(kind))
        lines.append(f"{kind}{padding}{value}" if kind == "field" and value.startswith("static") else f"{kind} {value}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Deriva o contrato a partir do SDK real")
    parser.add_argument("resource", help="nome do recurso, ex: Invoice")
    parser.add_argument("--lang", required=True, help=f"linguagem ({', '.join(sorted(EXTRACTORS))})")
    parser.add_argument("--role", default="main", help="papel do artefato, ex: main, test, impl, barrel, types")
    parser.add_argument("--from", dest="repo", default=None, help="raiz do SDK de referência")
    parser.add_argument("--out", help="arquivo de saída (padrão: stdout)")
    args = parser.parse_args()

    if args.lang not in EXTRACTORS:
        emit(f"[ERROR] linguagem sem extrator: {args.lang}")
        emit(f"[INFO] disponíveis: {', '.join(sorted(EXTRACTORS))}")
        return 2

    repo = referenceRepo(args.lang, args.repo)
    if repo is None:
        emit(f"[ERROR] referência de {args.lang} não resolvida — rode `make clone-sdk-ref` ou passe --from")
        return 2

    source = sourcePath(repo, args.resource, args.lang, args.role)
    if source is None:
        emit(f"[ERROR] papel sem layout em {args.lang}: {args.role}")
        return 2
    if not source.is_file():
        emit(f"[INFO] {args.resource} não existe em {source} — recurso novo neste SDK")
        return EXIT_ABSENT_UPSTREAM

    entries = EXTRACTORS[args.lang](source.read_text(encoding="utf-8"))
    if not entries:
        emit(f"[ERROR] nada extraído de {source}")
        return 1

    document = render(args.resource, args.lang, args.role, str(source), entries, repoSha(repo))
    if args.out:
        Path(args.out).write_text(document, encoding="utf-8")
        emit(f"[OK] {args.resource}: {len(entries)} entrada(s) → {args.out}")
        return 0

    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    sys.exit(main())
