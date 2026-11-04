import re
import sys
import json
import argparse
import functools
from pathlib import Path
from collections import Counter
from difflib import SequenceMatcher
from importlib.util import spec_from_file_location, module_from_spec

TOOLS_DIR = Path(__file__).resolve().parent

LANGUAGE = "java"
ROLE = "main"
MEMBER_KINDS = ("signature", "constructor", "inner", "field")
RATIO_DIGITS = 4
DETAIL_WIDTH = 100

STATE_EQUAL = "igual"
STATE_NO_GAPS = "sem gaps"
STATE_GAPS = "com gaps"
STATE_NOT_GENERATED = "nao gerado"
STATE_ABSENT = "ausente no real"
STATE_ERROR = "erro"

_IMPORT_LINE = re.compile(r"^import .*;$")
_JAVADOC = re.compile(r"/\*\*.*?\*/", re.DOTALL)
_DOC_STAR = re.compile(r"^\s*\*+")


@functools.cache
def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# com o leitor do pipe fechado (| head) a corrida segue com o código real
emit = loadTool("make-replica").emit


def gapsOf(realSource: str, generatedSource: str, filePath: str) -> list[tuple[str, str]]:
    assertGenerated = loadTool("assert-generated")
    contract = {kind: [] for kind in assertGenerated.CONTRACT_KINDS}
    for kind, value in loadTool("derive-contract").extractJava(realSource):
        contract[kind].append(value)

    issues, _, _ = assertGenerated.checkContract(
        source=generatedSource,
        contract=contract,
        filePath=filePath,
        waivers=None,
        language=LANGUAGE,
    )
    return [(issue.message.split(" ", 1)[0], issue.message) for issue in issues]


def codeLines(source: str) -> list[str]:
    stripped = (line.strip() for line in loadTool("derive-contract").withoutNoise(source, keepStrings=True))
    return [line for line in stripped if line]


def similarityOf(realSource: str, generatedSource: str) -> float:
    """Só o código conta: comentários `//`, Javadoc e espaço, tab e CRLF ficam de fora de propósito.

    O Javadoc é medido à parte, em `docEqual`."""
    matcher = SequenceMatcher(None, codeLines(realSource), codeLines(generatedSource), autojunk=False)
    return round(matcher.ratio(), RATIO_DIGITS)


def sameRelativeOrder(real: list, generated: list) -> bool:
    common = set(real) & set(generated)
    return [item for item in real if item in common] == [item for item in generated if item in common]


def importsOf(realSource: str, generatedSource: str) -> dict:
    real = [line for line in codeLines(realSource) if _IMPORT_LINE.match(line)]
    generated = [line for line in codeLines(generatedSource) if _IMPORT_LINE.match(line)]
    return {
        "missing": sorted(set(real) - set(generated)),
        "extra": sorted(set(generated) - set(real)),
        "inOrder": sameRelativeOrder(real, generated),
    }


def membersOf(source: str) -> list[tuple[str, str]]:
    entries = loadTool("derive-contract").extractJava(source)
    return [entry for entry in entries if entry[0] in MEMBER_KINDS]


def memberOrderEqualOf(realSource: str, generatedSource: str) -> bool:
    return sameRelativeOrder(membersOf(realSource), membersOf(generatedSource))


def docBlocksOf(source: str) -> list[str]:
    blocks = []
    for block in _JAVADOC.findall(source):
        lines = (_DOC_STAR.sub("", line) for line in block[3:-2].splitlines())
        blocks.append(" ".join(" ".join(lines).split()))
    return blocks


def docEqualOf(realSource: str, generatedSource: str) -> float:
    """Os blocos são comparados em sequência, sem âncora ao membro.

    Texto idêntico em membros diferentes não é detectado."""
    real = docBlocksOf(realSource)
    generated = docBlocksOf(generatedSource)
    if not real and not generated:
        return 1.0

    matcher = SequenceMatcher(None, real, generated, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return round(matched / max(len(real), len(generated)), RATIO_DIGITS)


def unmeasured(resource: str, state: str, detail: str = "") -> dict:
    return {
        "resource": resource,
        "state": state,
        "detail": detail,
        "gaps": [],
        "similarity": None,
        "importsMissing": None,
        "importsExtra": None,
        "importsInOrder": None,
        "memberOrderEqual": None,
        "docEqual": None,
    }


def measureSources(resource: str, realSource: str, generatedSource: str, filePath: str) -> dict:
    gaps = gapsOf(realSource=realSource, generatedSource=generatedSource, filePath=filePath)
    imports = importsOf(realSource=realSource, generatedSource=generatedSource)
    measured = {
        **unmeasured(resource=resource, state=STATE_GAPS),
        "gaps": gaps,
        "similarity": similarityOf(realSource=realSource, generatedSource=generatedSource),
        "importsMissing": imports["missing"],
        "importsExtra": imports["extra"],
        "importsInOrder": imports["inOrder"],
        "memberOrderEqual": memberOrderEqualOf(realSource=realSource, generatedSource=generatedSource),
        "docEqual": docEqualOf(realSource=realSource, generatedSource=generatedSource),
    }
    if gaps:
        return measured

    identical = (
        measured["similarity"] == 1.0
        and not imports["missing"]
        and not imports["extra"]
        and measured["memberOrderEqual"]
        and measured["docEqual"] == 1.0
    )
    return {**measured, "state": STATE_EQUAL if identical else STATE_NO_GAPS}


def readText(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def measureResource(resource: str, generatedRoot: Path, sourceRoot: Path) -> dict:
    deriveContract = loadTool("derive-contract")
    generatedPath = deriveContract.sourcePath(generatedRoot, resource, LANGUAGE, ROLE)
    realPath = deriveContract.sourcePath(sourceRoot, resource, LANGUAGE, ROLE)
    try:
        if not generatedPath.is_file():
            return unmeasured(resource=resource, state=STATE_NOT_GENERATED)
        if not realPath.is_file():
            return unmeasured(resource=resource, state=STATE_ABSENT)

        generatedSource = readText(generatedPath)
        realSource = readText(realPath)
    except OSError as error:
        side = "gerado" if Path(error.filename or "") in (generatedPath, generatedPath.parent) else "real"
        return unmeasured(
            resource=resource,
            state=STATE_ERROR,
            detail=f"{side}: {error.strerror or error}: {Path(error.filename or '').name}",
        )
    return measureSources(
        resource=resource,
        realSource=realSource,
        generatedSource=generatedSource,
        filePath=str(generatedPath),
    )


def sortKey(entry: dict) -> tuple:
    similarity = 2.0 if entry["similarity"] is None else entry["similarity"]
    return (-entry["gapCount"], similarity, entry["resource"])


def buildReport(results: list[dict], sha: str) -> dict:
    resources = [
        {
            "resource": result["resource"],
            "state": result["state"],
            "detail": result["detail"],
            "gaps": [list(gap) for gap in result["gaps"]],
            "gapCount": len(result["gaps"]),
            "similarity": result["similarity"],
            "importsMissing": result["importsMissing"],
            "importsExtra": result["importsExtra"],
            "importsInOrder": result["importsInOrder"],
            "memberOrderEqual": result["memberOrderEqual"],
            "docEqual": result["docEqual"],
        }
        for result in results
    ]
    resources.sort(key=sortKey)

    compared = [entry for entry in resources if entry["similarity"] is not None]
    byKind = Counter(kind for entry in resources for kind, _ in entry["gaps"])
    mean = sum(entry["similarity"] for entry in compared) / len(compared) if compared else 0.0

    return {
        "sha": sha,
        "total": len(resources),
        "compared": len(compared),
        "zeroGaps": sum(1 for entry in compared if entry["gapCount"] == 0),
        "identical": sum(1 for entry in resources if entry["state"] == STATE_EQUAL),
        "meanSimilarity": round(mean, RATIO_DIGITS),
        "gapsByKind": dict(sorted(byKind.items(), key=lambda item: (-item[1], item[0]))),
        "resources": resources,
    }


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def render(report: dict) -> str:
    lines = [
        f"[INFO] gerado x real (sdk-{LANGUAGE} @ {report['sha']})",
        f"[INFO] {report['compared']} comparados de {report['total']}; {report['zeroGaps']} com 0 gaps; "
        f"{report['identical']} idênticos; similaridade média {percent(report['meanSimilarity'])}",
        "",
    ]
    width = max((len(entry["resource"]) for entry in report["resources"]), default=0)
    width = max(width, len("recurso"))
    lines.append(f"  {'recurso':<{width}}  {'gaps':>4}  {'similar.':>8}  estado")
    for entry in report["resources"]:
        label = entry["state"]
        if label == STATE_ERROR and entry["detail"]:
            label = f"{label} ({entry['detail'][:DETAIL_WIDTH]})"
        lines.append(
            f"  {entry['resource']:<{width}}  {entry['gapCount']:>4}  {percent(entry['similarity']):>8}  {label}"
        )

    lines += ["", "[INFO] gaps por tipo:"]
    lines += [f"  {kind:<12}  {count:>4}" for kind, count in report["gapsByKind"].items()]
    return "\n".join(lines)


def parseArguments(argv: list[str] | None):
    parser = argparse.ArgumentParser(description="Compara o Java gerado pelo make replicate com o sdk-java real")
    parser.add_argument("resources", nargs="*", help="recursos a comparar (padrão: todos os schemas)")
    parser.add_argument("--generated", required=True, help="árvore gerada (saída do make replicate)")
    parser.add_argument(
        "--source",
        default=None,
        help="raiz do sdk-java real (padrão: SDK_JAVA ou _references/sdk-java)",
    )
    parser.add_argument("--json", dest="jsonPath", default=None, help="grava o relatório em JSON neste arquivo")
    return parser.parse_args(argv)


def validateArguments(args) -> tuple[Path | None, str]:
    """Raiz do real resolvida, ou a mensagem do primeiro uso inválido."""
    placeGenerated = loadTool("place-generated")
    mainDirectory = Path(args.generated) / Path(placeGenerated.LAYOUTS[LANGUAGE][0][2]).parent
    if not mainDirectory.is_dir():
        return (None, f"--generated: {mainDirectory} não existe")

    sourceRoot = loadTool("derive-contract").referenceRepo(LANGUAGE, args.source)
    if sourceRoot is None:
        return (None, f"sdk-{LANGUAGE} real ausente — passe --source, defina SDK_JAVA ou rode `make clone-sdk-ref`")

    if args.jsonPath and Path(args.jsonPath).is_dir():
        return (None, f"--json: {args.jsonPath} é um diretório")

    if args.jsonPath and not Path(args.jsonPath).resolve().parent.is_dir():
        return (None, f"--json: o diretório de {args.jsonPath} não existe")

    known = loadTool("parity-report").schemaResources()
    unknown = [name for name in args.resources if name not in known]
    if unknown:
        return (None, f"recurso(s) sem schema em apis/schemas: {', '.join(unknown)}")
    return (Path(sourceRoot), "")


def writeJson(report: dict, jsonPath: str | None) -> int:
    if not jsonPath:
        return 0
    try:
        Path(jsonPath).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as error:
        # a medição terminou, mas o artefato pedido não foi produzido
        emit(f"[ERROR] --json: {error}")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parseArguments(argv)
    sourceRoot, problem = validateArguments(args)
    if problem:
        emit(f"[ERROR] {problem}")
        return 2

    requested = list(dict.fromkeys(args.resources)) or loadTool("parity-report").schemaResources()
    results = [
        measureResource(resource=name, generatedRoot=Path(args.generated), sourceRoot=sourceRoot)
        for name in requested
    ]
    report = buildReport(results=results, sha=loadTool("make-replica").sourceSha(sourceRoot))

    emit(render(report))
    return writeJson(report, args.jsonPath)


if __name__ == "__main__":
    sys.exit(main())
