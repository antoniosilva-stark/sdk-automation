import re
import sys
import json
import argparse
import functools
from collections import Counter
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
CLASSES_FILE = TOOLS_DIR.parent / "apis/gap-classes.yaml"
CLASSES_SECTION = "gap-classes"
CLASS_KEYS = ("kind", "text", "class")
VALID_CLASSES = ("tem-fonte", "sem-fonte", "muda-api-publica", "bloqueado-por-dependencia")
SEM_CLASSIFICAR = "sem-classificar"
ALL_CLASSES = tuple(sorted((*VALID_CLASSES, SEM_CLASSIFICAR)))
LANGUAGE = "java"
ROLE = "main"
MARKER_START = "<!-- sdk-automation:gaps:start -->"
MARKER_END = "<!-- sdk-automation:gaps:end -->"
MAX_ITEMS = 50
MAX_TEXT = 300
SLICE_KEYS = {"resource": str, "measured": bool, "gaps": list, "waived": list}
_UNSAFE = re.compile(r"[^A-Za-z0-9 _.,()<>?\[\]|:;=\"-]")
_UNSAFE_PROSE = re.compile(r"[^A-Za-z0-9 _.,:-]")


@functools.cache
def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classOf(classes: dict, resource: str, kind: str, text: str) -> str:
    return classes.get(resource, {}).get((kind, text), SEM_CLASSIFICAR)


def checkClassEntry(resource: str, index: int, entry) -> tuple[tuple[str, str], str]:
    where = f"{resource}[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: entrada deve ser um mapa {{kind, text, class}}, veio {entry!r}")

    unknown = [key for key in entry if key not in CLASS_KEYS]
    if unknown:
        raise ValueError(f"{where}.{unknown[0]}: chave desconhecida, esperado uma de {list(CLASS_KEYS)}")

    kinds = loadTool("compare-with-real").WAIVER_KINDS
    if entry.get("kind") not in kinds:
        raise ValueError(f"{where}.kind invalido: {entry.get('kind')!r}, esperado um de {list(kinds)}")

    text = entry.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{where}.text invalido: {text!r}, esperado texto nao vazio")

    if entry.get("class") not in VALID_CLASSES:
        raise ValueError(f"{where}.class invalido: {entry.get('class')!r}, esperado um de {list(VALID_CLASSES)}")
    return (entry["kind"], text), entry["class"]


def checkClassEntries(resource: str, entries) -> dict[tuple[str, str], str]:
    if not isinstance(entries, list):
        raise ValueError(f"{resource}: {CLASSES_SECTION} deve ser uma lista de entradas, veio {entries!r}")

    classByGap = {}
    for index, entry in enumerate(entries):
        key, value = checkClassEntry(resource=resource, index=index, entry=entry)
        if key in classByGap:
            raise ValueError(f"{resource}[{index}]: entrada duplicada {key[0]} {key[1]!r}")
        classByGap[key] = value
    return classByGap


def loadClasses(path) -> dict[str, dict[tuple[str, str], str]]:
    path = Path(path)
    extractSchema = loadTool("extract-schema")
    try:
        data = extractSchema.loadFast(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as error:
        raise ValueError(f"{path.name}: {CLASSES_SECTION} ilegivel: {error}") from error

    if not isinstance(data, dict):
        raise ValueError(f"{CLASSES_SECTION}: esperado um mapa recurso -> lista de entradas, veio {data!r}")

    extractSchema.checkKnownResources(CLASSES_SECTION, data)
    return {resource: checkClassEntries(resource=resource, entries=entries) for resource, entries in data.items()}


def extrasOf(realSource: str, generatedSource: str) -> dict:
    compare = loadTool("compare-with-real")
    missing = Counter(compare.membersOf(realSource))
    members = []
    for entry in sorted(compare.membersOf(generatedSource)):
        if missing[entry] > 0:
            missing[entry] -= 1
            continue

        members.append({"kind": entry[0], "text": entry[1]})
    return {"members": members, "imports": compare.importsOf(realSource, generatedSource)["extra"]}


def buildSlice(entry: dict, classes: dict, sha: str, extras: dict | None) -> dict:
    resource = entry["resource"]
    gaps = [
        {
            "key": f"{resource} | {kind} | {text}",
            "kind": kind,
            "text": text,
            "class": classOf(classes=classes, resource=resource, kind=kind, text=text),
        }
        for kind, text in sorted(tuple(gap) for gap in entry["gaps"])
    ]
    waived = [
        {
            "key": f"{resource} | {item['kind']} | {item['text']}",
            "kind": item["kind"],
            "text": item["text"],
            "reason": item["reason"],
        }
        for item in sorted(entry["waived"], key=lambda item: (item["kind"], item["text"]))
    ]
    return {
        "resource": resource,
        "state": entry["state"],
        "detail": entry["detail"],
        "sha": sha,
        "measured": entry["similarity"] is not None,
        "similarity": entry["similarity"],
        "gaps": gaps,
        "waived": waived,
        "extras": extras,
    }


def consolidate(slices: list[dict], classes: dict) -> dict:
    resources = [item["resource"] for item in slices]
    for resource in sorted(set(resources)):
        if resources.count(resource) > 1:
            raise ValueError(f"fatia duplicada: {resource}")

    ordered = sorted(slices, key=lambda item: item["resource"])
    byClass = Counter({name: 0 for name in ALL_CLASSES})
    byClass.update(gap["class"] for item in ordered for gap in item["gaps"])
    withExtras = [item["extras"] for item in ordered if item["extras"] is not None]
    return {
        "resources": len(ordered),
        "measured": sum(1 for item in ordered if item["measured"]),
        "gapsTotal": sum(len(item["gaps"]) for item in ordered),
        "waivedTotal": sum(len(item["waived"]) for item in ordered),
        "byClass": dict(sorted(byClass.items())),
        "extras": {
            "members": sum(len(extras["members"]) for extras in withExtras),
            "imports": sum(len(extras["imports"]) for extras in withExtras),
        },
        "obsoleteClasses": sorted(obsoleteKeys(slices=ordered, classes=classes)),
        "slices": ordered,
    }


def obsoleteKeys(slices: list[dict], classes: dict) -> list[str]:
    obsolete = []
    for item in slices:
        if not item["measured"]:
            continue

        present = {(gap["kind"], gap["text"]) for gap in item["gaps"]}
        obsolete.extend(
            f"{item['resource']} | {kind} | {text}"
            for kind, text in classes.get(item["resource"], {})
            if (kind, text) not in present
        )
    return obsolete


def safeText(text) -> str:
    return _truncated(_UNSAFE.sub("_", str(text)))


def safeProse(text) -> str:
    return _truncated(_UNSAFE_PROSE.sub("_", str(text)))


def _truncated(cleaned: str) -> str:
    if len(cleaned) <= MAX_TEXT:
        return cleaned
    return cleaned[:MAX_TEXT] + "..."


def renderSection(slice: dict) -> str:
    lines = [
        MARKER_START,
        "### Nao gerado / completar a mao",
        f"Recurso {safeProse(slice['resource'])} medido contra o sdk-java real em {safeProse(slice['sha'])}; "
        "so informa o revisor, nao altera o gate.",
    ]
    if not slice["measured"]:
        lines.append(f"nao medido: {safeProse(slice['state'])}")
        return "\n".join([*lines, MARKER_END])

    lines.extend(countLines(slice))
    lines.extend(itemBlock(slice))
    return "\n".join([*lines, MARKER_END])


def countLines(slice: dict) -> list[str]:
    gaps = slice["gaps"]
    counts = Counter(gap["class"] for gap in gaps)
    breakdown = ", ".join(f"{safeProse(name)} {counts[name]}" for name in ALL_CLASSES)
    lines = [
        f"Gaps: {len(gaps)} ({breakdown})",
        f"Dispensados (escritos a mao no real): {len(slice['waived'])}",
    ]
    extras = slice["extras"]
    if extras is not None:
        lines.append(f"INFO extras, fora da soma: membros {len(extras['members'])}, imports {len(extras['imports'])}")
    if not gaps:
        lines.append("Nenhum gap contra o sdk-java real.")
    return lines


def itemBlock(slice: dict) -> list[str]:
    resource = slice["resource"]
    gaps = sorted(slice["gaps"], key=lambda gap: (gap["class"], gap["kind"], gap["text"]))
    extras = slice["extras"] or {"members": [], "imports": []}
    extraKeys = [f"{resource} | {member['kind']} | {member['text']}" for member in extras["members"]]
    extraKeys += [f"{resource} | import | {text}" for text in extras["imports"]]

    lines = []
    lines.extend(limited(title="gaps", entries=[(gap["class"], gap["key"]) for gap in gaps]))
    lines.extend(limited(title="dispensados", entries=[(None, item["key"]) for item in slice["waived"]]))
    lines.extend(limited(title="extras a mais", entries=[(None, key) for key in extraKeys]))
    if not lines:
        return []
    return ["```", *lines, "```"]


def limited(title: str, entries: list[tuple[str | None, str]]) -> list[str]:
    if not entries:
        return []
    lines, current = [f"{title}:"], None
    for group, key in entries[:MAX_ITEMS]:
        if group is not None and group != current:
            current = group
            lines.append(f"[{safeText(group)}]")
        lines.append(safeText(key))
    if len(entries) > MAX_ITEMS:
        lines.append(f"... +{len(entries) - MAX_ITEMS} omitidos")
    return lines


def mergeBody(body: str, section: str) -> str:
    if not (section.startswith(MARKER_START) and section.endswith(MARKER_END)):
        raise ValueError("secao deve comecar e terminar com os marcadores")
    if section.count(MARKER_START) != 1 or section.count(MARKER_END) != 1:
        raise ValueError("secao deve ter exatamente um par de marcadores")

    starts = body.count(MARKER_START)
    ends = body.count(MARKER_END)
    if starts == 0 and ends == 0:
        return f"{body.rstrip()}\n\n{section}\n" if body.strip() else f"{section}\n"
    if starts != 1 or ends != 1:
        raise ValueError(f"corpo com marcadores invalidos: {starts} de inicio e {ends} de fim, esperado um par")

    start = body.index(MARKER_START)
    end = body.index(MARKER_END)
    if end < start:
        raise ValueError("corpo com marcadores invalidos: fim antes do inicio")
    return body[:start] + section + body[end + len(MARKER_END):]


def checkSlice(item, where: str) -> dict:
    if not isinstance(item, dict):
        raise ValueError(f"{where}: fatia deve ser um mapa, veio {type(item).__name__}")
    for key, kind in SLICE_KEYS.items():
        if not isinstance(item.get(key), kind):
            raise ValueError(f"{where}: campo {key} ausente ou invalido, esperado {kind.__name__}")
    return item


def readJson(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def writeJson(data: dict, path: str) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def writeText(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        loadTool("make-replica").silenceStdout()


def runSlice(args) -> int:
    report = readJson(args.report)
    if not isinstance(report, dict) or not isinstance(report.get("resources"), list) or "sha" not in report:
        raise ValueError(f"--report: {args.report} sem sha/resources, nao e o JSON do comparador")

    entries = [entry for entry in report["resources"] if entry["resource"] == args.resource]
    if not entries:
        raise ValueError(f"--resource: {args.resource} ausente no relatorio {args.report}")

    deriveContract = loadTool("derive-contract")
    sourceRoot = deriveContract.referenceRepo(LANGUAGE, args.source)
    if sourceRoot is None:
        raise ValueError(f"--source: sdk-{LANGUAGE} real ausente em {args.source or 'SDK_JAVA/_references'}")

    generatedPath = deriveContract.sourcePath(Path(args.generated), args.resource, LANGUAGE, ROLE)
    realPath = deriveContract.sourcePath(Path(sourceRoot), args.resource, LANGUAGE, ROLE)
    extras = None
    if generatedPath.is_file() and realPath.is_file():
        readText = loadTool("compare-with-real").readText
        extras = extrasOf(realSource=readText(realPath), generatedSource=readText(generatedPath))

    writeJson(
        data=buildSlice(
            entry=entries[0],
            classes=loadClasses(args.classes),
            sha=report["sha"],
            extras=extras,
        ),
        path=args.out,
    )
    return 0


def runConsolidate(args) -> int:
    directory = Path(args.slices)
    if not directory.is_dir():
        raise ValueError(f"--slices: {directory} nao e um diretorio")
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"--slices: nenhuma fatia *.json em {directory}")
    slices = [checkSlice(item=readJson(str(path)), where=path.name) for path in paths]
    writeJson(data=consolidate(slices=slices, classes=loadClasses(args.classes)), path=args.out)
    return 0


def runSection(args) -> int:
    writeText(renderSection(slice=checkSlice(item=readJson(args.slice), where=Path(args.slice).name)) + "\n")
    return 0


def runMergeBody(args) -> int:
    body = Path(args.body).read_text(encoding="utf-8")
    section = Path(args.section).read_text(encoding="utf-8").rstrip("\n")
    writeText(mergeBody(body=body, section=section))
    return 0


def parseArguments(argv):
    parser = argparse.ArgumentParser(description="Gaps de paridade contra o sdk-java real, para o corpo da PR")
    commands = parser.add_subparsers(dest="command", required=True)

    sliceParser = commands.add_parser("slice", help="fatia JSON de um recurso")
    sliceParser.add_argument("--report", required=True, help="JSON do comparador")
    sliceParser.add_argument("--resource", required=True)
    sliceParser.add_argument("--generated", required=True, help="arvore gerada")
    sliceParser.add_argument("--source", default=None, help="raiz do sdk-java real")
    sliceParser.add_argument("--out", required=True)

    consolidateParser = commands.add_parser("consolidate", help="consolida as fatias de um diretorio")
    consolidateParser.add_argument("--slices", required=True)
    consolidateParser.add_argument("--out", required=True)

    sectionParser = commands.add_parser("section", help="imprime a secao da PR de uma fatia")
    sectionParser.add_argument("--slice", required=True)

    mergeParser = commands.add_parser("merge-body", help="mescla a secao no corpo da PR")
    mergeParser.add_argument("--body", required=True)
    mergeParser.add_argument("--section", required=True)

    for sub in (sliceParser, consolidateParser):
        sub.add_argument("--classes", default=str(CLASSES_FILE), help="classes dos gaps (padrao: apis/gap-classes.yaml)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parseArguments(argv)
    handlers = {
        "slice": runSlice,
        "consolidate": runConsolidate,
        "section": runSection,
        "merge-body": runMergeBody,
    }
    try:
        return handlers[args.command](args)
    except (ValueError, OSError) as error:
        loadTool("make-replica").emit(f"[ERROR] {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
