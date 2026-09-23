import re
import sys
import json
import yaml
import argparse
import subprocess
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
JAVA_TEMPLATE = REPO_ROOT / "templates/java/model.mustache"
MIN_READ_PROPS = 3

_SECTION = re.compile(r"\{\{#vendorExtensions\.(x-sdk-[a-z]+)\}\}")


def loadFast(text: str):
    """libyaml quando disponivel: a spec tem 7.131 linhas e o parser puro custa 138 ms."""
    return yaml.load(text, Loader=SafeLoader)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DATA_ONLY = "x-sdk-data-only"


def templateFlags(template: Path = JAVA_TEMPLATE) -> set[str]:
    """Seções do template, mais o modo data-only, que é ausência de seção."""
    return set(_SECTION.findall(template.read_text(encoding="utf-8"))) | {DATA_ONLY}


def readProps(resource: str, root: Path) -> int:
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "extract-schema.py"), resource, "--from", str(root)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return 0

    document = loadFast(result.stdout) or {}
    schemas = (document.get("components") or {}).get("schemas") or {}
    return len((schemas.get(resource, {}).get("properties") or {}))


def buildReport(pythonRoot: Path, supported: set[str]) -> dict:
    extractSchema = loadTool("extract-schema")
    listGaps = loadTool("list-gaps")

    canonical, _, withoutClass = listGaps.pythonModules(pythonRoot)

    fullParity, withPending, outOfReach = [], [], []
    for resource in sorted(canonical):
        operations = extractSchema.declaredOperations(pythonRoot, resource)
        covered = sorted(set(operations["flags"]) & supported)
        pending = sorted(set(operations["flags"]) - supported) + operations["unsupported"]
        props = readProps(resource, pythonRoot)

        if props < MIN_READ_PROPS or not covered:
            outOfReach.append({"resource": resource, "reason": "schema" if props < MIN_READ_PROPS else "sem operação suportada"})
            continue
        if pending:
            withPending.append({"resource": resource, "pending": pending})
            continue
        fullParity.append(resource)

    outOfReach.extend({"resource": name, "reason": "sem classe de recurso"} for name in sorted(withoutClass))

    return {
        "total": len(canonical) + len(withoutClass),
        "fullParity": fullParity,
        "withPending": withPending,
        "outOfReach": outOfReach,
    }


def reportHuman(report: dict) -> None:
    total = report["total"]
    reachable = len(report["fullParity"]) + len(report["withPending"])
    emit(f"[INFO] {reachable} de {total} recurso(s) ao alcance da automação")
    emit(f"[INFO] {len(report['fullParity'])} com paridade total, "
         f"{len(report['withPending'])} com pendência declarada")

    emit("")
    emit(f"[OK] paridade total ({len(report['fullParity'])}):")
    emit(f"  {', '.join(report['fullParity'])}")

    emit("")
    emit(f"[INFO] geráveis com pendência ({len(report['withPending'])}):")
    for entry in report["withPending"]:
        emit(f"  {entry['resource']} — falta {', '.join(entry['pending'])}")

    emit("")
    emit(f"[WARN] fora de alcance ({len(report['outOfReach'])}):")
    for entry in report["outOfReach"]:
        emit(f"  {entry['resource']} — {entry['reason']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mede quantos recursos do SDK Python a automação alcança")
    parser.add_argument("--python", dest="pythonRoot", default=None, help="raiz do SDK Python")
    parser.add_argument("--json", action="store_true", help="saída em JSON")
    args = parser.parse_args()

    extractSchema = loadTool("extract-schema")
    pythonRoot = extractSchema.resolveRoot(args.pythonRoot)
    issue = extractSchema.rootIssue(pythonRoot)
    if issue:
        emit(f"[ERROR] {issue}")
        return 2

    applySchema = loadTool("apply-schema")
    report = buildReport(pythonRoot, templateFlags() - set(applySchema.SUPPRESSED))
    if args.json:
        emit(json.dumps(report))
        return 0

    reportHuman(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
