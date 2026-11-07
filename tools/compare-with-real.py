import re
import sys
import json
import argparse
import functools
from pathlib import Path
from collections import Counter
from difflib import SequenceMatcher
from importlib.util import spec_from_file_location, module_from_spec

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
WAIVERS_FILE = TOOLS_DIR.parent / "apis/java-handwritten.yaml"
WAIVER_SECTION = "java-handwritten"
WAIVER_KINDS = ("signature", "field", "constructor", "require", "inner")
WAIVER_KEYS = ("kind", "text", "count", "reason")
NOISE_FILE = TOOLS_DIR.parent / "apis/java-unused-imports.yaml"
NOISE_SECTION = "java-unused-imports"
NOISE_KIND = "require"
NOISE_TEXT_FORM = "require ausente: import <fqcn>;"

LANGUAGE = "java"
ROLE = "main"
MEMBER_KINDS = ("signature", "constructor", "inner", "field")
RATIO_DIGITS = 4
DETAIL_WIDTH = 100

STATE_EQUAL = "igual"
STATE_NO_GAPS = "sem gaps"
STATE_NO_GAPS_WAIVED = "sem gaps (com dispensa)"
STATE_NO_GAPS_NOISE = "sem gaps (so ruido)"
STATE_GAPS = "com gaps"
STATE_NOT_GENERATED = "nao gerado"
STATE_ABSENT = "ausente no real"
STATE_ERROR = "erro"

_IMPORT_LINE = re.compile(r"^import .*;$")
_NOISE_TEXT = re.compile(r"^require ausente: import [A-Za-z_]\w*(\.[A-Za-z_]\w*)+;$")
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


def checkWaiverEntry(resource: str, index: int, entry) -> dict:
    where = f"{resource}[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: dispensa deve ser um mapa {{kind, text, count, reason}}, veio {entry!r}")

    unknown = [key for key in entry if key not in WAIVER_KEYS]
    if unknown:
        raise ValueError(f"{where}.{unknown[0]}: chave desconhecida, esperado uma de {list(WAIVER_KEYS)}")

    if entry.get("kind") not in WAIVER_KINDS:
        raise ValueError(f"{where}.kind invalido: {entry.get('kind')!r}, esperado um de {list(WAIVER_KINDS)}")

    for key in ("text", "reason"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where}.{key} invalido: {value!r}, esperado texto nao vazio")

    count = entry.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"{where}.count invalido: {count!r}, esperado inteiro >= 1")
    return {"kind": entry["kind"], "text": entry["text"], "count": count, "reason": entry["reason"]}


def checkWaiverEntries(resource: str, entries, section: str = WAIVER_SECTION) -> list[dict]:
    if not isinstance(entries, list):
        raise ValueError(f"{resource}: {section} deve ser uma lista de dispensas, veio {entries!r}")

    checked = [checkWaiverEntry(resource=resource, index=index, entry=entry) for index, entry in enumerate(entries)]
    seen = set()
    for entry in checked:
        key = (entry["kind"], entry["text"])
        if key in seen:
            raise ValueError(f"{resource}: dispensa duplicada {key[0]} {key[1]!r}; some no count em vez de repetir")
        seen.add(key)
    return checked


def loadWaivers(path, section: str = WAIVER_SECTION) -> dict[str, list[dict]]:
    """Ausente é sem dispensas; arquivo malformado é erro, nunca dispensa parcial."""
    path = Path(path)
    if not path.is_file():
        return {}

    extractSchema = loadTool("extract-schema")
    try:
        data = extractSchema.loadFast(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as error:
        raise ValueError(f"{path.name}: {section} ilegivel: {error}") from error

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise ValueError(f"{section}: esperado um mapa recurso -> lista de dispensas, veio {data!r}")

    extractSchema.checkKnownResources(section, data)
    return {
        resource: checkWaiverEntries(resource=resource, entries=entries, section=section)
        for resource, entries in data.items()
    }


def checkNoiseEntry(resource: str, index: int, entry: dict) -> None:
    where = f"{resource}[{index}]"
    if entry["kind"] != NOISE_KIND:
        raise ValueError(f"{where}.kind invalido: {entry['kind']!r}, ruido so aceita {NOISE_KIND!r}")

    if not _NOISE_TEXT.match(entry["text"]):
        raise ValueError(
            f"{where}.text invalido: {entry['text']!r}, esperado {NOISE_TEXT_FORM!r} sem curinga nem static"
        )


def loadNoise(path) -> dict[str, list[dict]]:
    """Mesmo arquivo e mesma regra das dispensas, só que de import declarado e nunca usado no real."""
    noise = loadWaivers(path, section=NOISE_SECTION)
    for resource, entries in noise.items():
        for index, entry in enumerate(entries):
            checkNoiseEntry(resource=resource, index=index, entry=entry)
    return noise


def waiveResource(item: dict, entries: list[dict], key: str) -> tuple[dict, list[dict]]:
    """Multiconjunto: cada entrada dispensa o gap exato (kind, texto) até `count` vezes."""
    budget = {(entry["kind"], entry["text"]): entry["count"] for entry in entries}
    reasons = {(entry["kind"], entry["text"]): entry["reason"] for entry in entries}
    remaining, waived = [], []
    for gap in item["gaps"]:
        gapKey = tuple(gap)
        if budget.get(gapKey, 0) == 0:
            remaining.append(gap)
            continue

        budget[gapKey] -= 1
        waived.append({"kind": gapKey[0], "text": gapKey[1], "reason": reasons[gapKey]})

    obsolete = [
        {
            "resource": item["resource"],
            "kind": entry["kind"],
            "text": entry["text"],
            "count": entry["count"],
            "consumed": entry["count"] - budget[(entry["kind"], entry["text"])],
            "reason": entry["reason"],
        }
        for entry in entries
        if budget[(entry["kind"], entry["text"])] > 0
    ]
    settled = {**item, "gaps": remaining, key: waived}
    return {**settled, "state": settledState(settled)}, obsolete


def settledState(item: dict) -> str:
    """Dispensa à mão pesa mais: o estado de só ruído exige que nenhum gap tenha precisado dela."""
    if item["gaps"]:
        return item["state"]

    if item.get("waived"):
        return STATE_NO_GAPS_WAIVED
    return STATE_NO_GAPS_NOISE if item.get("noiseWaived") else item["state"]


def applyWaivers(
    results: list[dict],
    waivers: dict[str, list[dict]],
    key: str = "waived",
) -> tuple[list[dict], list[dict]]:
    """Só recurso medido nesta corrida cobra obsolescência: corrida parcial não falha por quem não comparou."""
    applied, obsolete = [], []
    for item in results:
        entries = waivers.get(item["resource"])
        if not entries or item["similarity"] is None:
            applied.append(item)
            continue

        waivedItem, unused = waiveResource(item=item, entries=entries, key=key)
        applied.append(waivedItem)
        obsolete.extend(unused)
    return applied, obsolete


def applyNoise(results: list[dict], noise: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    return applyWaivers(results=results, waivers=noise, key="noiseWaived")


def sortKey(entry: dict) -> tuple:
    similarity = 2.0 if entry["similarity"] is None else entry["similarity"]
    return (-entry["gapCount"], similarity, entry["resource"])


def buildReport(results: list[dict], sha: str, obsolete: list[dict] = (), obsoleteNoise: list[dict] = ()) -> dict:
    resources = [
        {
            "resource": result["resource"],
            "state": result["state"],
            "detail": result["detail"],
            "gaps": [list(gap) for gap in result["gaps"]],
            "gapCount": len(result["gaps"]),
            "waived": result.get("waived", []),
            "waivedCount": len(result.get("waived", [])),
            "noiseWaived": result.get("noiseWaived", []),
            "noiseWaivedCount": len(result.get("noiseWaived", [])),
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
        "zeroGaps": sum(
            1 for entry in compared
            if entry["gapCount"] == 0 and entry["waivedCount"] == 0 and entry["noiseWaivedCount"] == 0
        ),
        "zeroGapsWithWaiver": sum(1 for entry in compared if entry["gapCount"] == 0 and entry["waivedCount"] > 0),
        "zeroGapsWithNoise": sum(
            1 for entry in compared
            if entry["gapCount"] == 0 and entry["waivedCount"] == 0 and entry["noiseWaivedCount"] > 0
        ),
        "waivedTotal": sum(entry["waivedCount"] for entry in resources),
        "noiseWaivedTotal": sum(entry["noiseWaivedCount"] for entry in resources),
        "gapsTotal": sum(entry["gapCount"] for entry in resources),
        "obsoleteWaivers": list(obsolete),
        "obsoleteNoise": list(obsoleteNoise),
        "identical": sum(1 for entry in resources if entry["state"] == STATE_EQUAL),
        "meanSimilarity": round(mean, RATIO_DIGITS),
        "gapsByKind": dict(sorted(byKind.items(), key=lambda item: (-item[1], item[0]))),
        "resources": resources,
    }


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def waiverSummary(report: dict) -> list[str]:
    if not report["waivedTotal"] and not report["obsoleteWaivers"]:
        return []

    return [
        f"[INFO] dispensa de membros escritos à mão: {report['zeroGapsWithWaiver']} sem gaps só pela dispensa; "
        f"{report['waivedTotal']} gaps dispensados; {report['gapsTotal']} gaps restantes"
    ]


def noiseSummary(report: dict) -> list[str]:
    if not report["noiseWaivedTotal"] and not report["obsoleteNoise"]:
        return []

    return [
        f"[INFO] dispensa de imports sem uso no real: {report['zeroGapsWithNoise']} sem gaps so pelo ruido; "
        f"{report['noiseWaivedTotal']} gaps dispensados"
    ]


def noiseDetails(report: dict) -> list[str]:
    lines = []
    noisyEntries = [entry for entry in report["resources"] if entry["noiseWaived"]]
    if noisyEntries:
        lines += ["", f"[INFO] gaps dispensados (import sem uso no real; ver {NOISE_FILE.name}):"]
    for entry in noisyEntries:
        lines += [f"  {entry['resource']}  {waived['text']}  [{waived['reason']}]" for waived in entry["noiseWaived"]]
    lines += [
        f"[ERROR] dispensa obsoleta de import sem uso em {item['resource']} [{item['kind']}] {item['text']} "
        f"(count {item['count']}, consumida {item['consumed']}): remova ou reduza a entrada em {NOISE_FILE.name}"
        for item in report["obsoleteNoise"]
    ]
    return lines


def waiverDetails(report: dict) -> list[str]:
    lines = []
    waivedEntries = [entry for entry in report["resources"] if entry["waived"]]
    if waivedEntries:
        lines += ["", f"[INFO] gaps dispensados (escritos à mão no real; ver {WAIVERS_FILE.name}):"]
    for entry in waivedEntries:
        lines += [
            f"  {entry['resource']}  {waived['text']}  [{waived['reason']}]" for waived in entry["waived"]
        ]
    lines += [
        f"[ERROR] dispensa obsoleta em {item['resource']} [{item['kind']}] {item['text']} "
        f"(count {item['count']}, consumida {item['consumed']}): remova ou reduza a entrada em {WAIVERS_FILE.name}"
        for item in report["obsoleteWaivers"]
    ]
    return lines


def render(report: dict) -> str:
    lines = [
        f"[INFO] gerado x real (sdk-{LANGUAGE} @ {report['sha']})",
        f"[INFO] {report['compared']} comparados de {report['total']}; {report['zeroGaps']} com 0 gaps; "
        f"{report['identical']} idênticos; similaridade média {percent(report['meanSimilarity'])}",
    ]
    lines += waiverSummary(report)
    lines += noiseSummary(report)
    lines.append("")
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
    lines += waiverDetails(report)
    lines += noiseDetails(report)
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
    waivers = parser.add_mutually_exclusive_group()
    waivers.add_argument(
        "--waivers",
        dest="waiversPath",
        default=None,
        help="dispensas dos membros escritos à mão (padrão: apis/java-handwritten.yaml; ausente = sem dispensas)",
    )
    waivers.add_argument(
        "--no-waivers",
        dest="noWaivers",
        action="store_true",
        help="mostra os gaps brutos, sem dispensas",
    )
    parser.add_argument(
        "--noise",
        dest="noisePath",
        default=None,
        help="dispensas dos imports sem uso no real (padrão: apis/java-unused-imports.yaml; ausente = sem dispensas)",
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

    if args.waiversPath and not Path(args.waiversPath).is_file():
        return (None, f"--waivers: {args.waiversPath} não existe")

    if args.noisePath and not Path(args.noisePath).is_file():
        return (None, f"--noise: {args.noisePath} não existe")

    if args.noisePath and args.noWaivers:
        return (None, "--noise: incompatível com --no-waivers, que desliga as duas dispensas")

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

    try:
        waivers = {} if args.noWaivers else loadWaivers(args.waiversPath or WAIVERS_FILE)
        noise = {} if args.noWaivers else loadNoise(args.noisePath or NOISE_FILE)
    except ValueError as error:
        emit(f"[ERROR] {error}")
        return 2

    requested = list(dict.fromkeys(args.resources)) or loadTool("parity-report").schemaResources()
    results = [
        measureResource(resource=name, generatedRoot=Path(args.generated), sourceRoot=sourceRoot)
        for name in requested
    ]
    results, obsolete = applyWaivers(results=results, waivers=waivers)
    results, obsoleteNoise = applyNoise(results=results, noise=noise)
    report = buildReport(
        results=results,
        sha=loadTool("make-replica").sourceSha(sourceRoot),
        obsolete=obsolete,
        obsoleteNoise=obsoleteNoise,
    )

    emit(render(report))
    jsonCode = writeJson(report, args.jsonPath)
    if jsonCode:
        return jsonCode

    # lista de dispensas só encolhe: entrada que não consome tudo falha a corrida
    return 1 if obsolete or obsoleteNoise else 0


if __name__ == "__main__":
    sys.exit(main())
