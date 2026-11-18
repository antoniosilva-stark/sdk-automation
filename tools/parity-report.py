import re
import sys
import json
import shutil
import argparse
import tempfile
import subprocess
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from importlib.util import spec_from_file_location, module_from_spec

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
SCHEMAS_DIR = REPO_ROOT / "apis/schemas"

LANGUAGE = "java"
DEFAULT_JOBS = 2
BUILD_TIMEOUT_SECONDS = 600
EXIT_TIMEOUT = 124

STATE_PASSES = "passa"
STATE_FAILS = "reprova"
STATE_BLOCKED = "barrado pelo lint-spec"
STATE_ABSENT = "ausente no SDK"
STATE_ERROR = "erro"

_GAP_LINE = re.compile(r"^\s*\[ERROR\] .*? CONTRACT_GAP (\w+) (.+)$")
_PARITY_LINE = re.compile(r"paridade medida pelo gate estrito: (\d+) passaram, (\d+) reprovaram")
_LINT_BLOCKED = re.compile(r"^\[ERROR\] lint-spec \(exit")
_LINT_CODE = re.compile(r"^\s*\[ERROR\] \S+ ([A-Z][A-Z_]+) ")
_ERROR_LINE = re.compile(r"^\s*\[ERROR\] ")
_ABSENT_LINE = re.compile(r"\[INFO\] \S+ não existe no sdk-")
_BUILT_LINE = re.compile(r"^\[OK\] \S+ construído e verificado em ")


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schemaResources() -> list[str]:
    """Nome da classe lido do schema: o arquivo é minúsculo e não há separador para inferir o UpperCamelCase.

    O arquivo também traz o `<Recurso>Create`; o recurso é a chave que casa com o nome do arquivo."""
    loadFast = loadTool("build-resource").loadFast
    names = []
    for path in sorted(SCHEMAS_DIR.glob("*.yaml")):
        schemas = (loadFast(path.read_text(encoding="utf-8")).get("components") or {}).get("schemas") or {}
        names.extend(name for name in schemas if name.lower() == path.stem)
    return names


def parseGaps(output: str) -> dict:
    """Estado e gaps de contrato a partir da saída do build-resource, sem olhar o código de saída."""
    lines = output.splitlines()
    gaps = [(match.group(1), match.group(2)) for match in map(_GAP_LINE.match, lines) if match]
    parity = next((match for match in map(_PARITY_LINE.search, lines) if match), None)

    if parity:
        failed = int(parity.group(2)) > 0
        return {"state": STATE_FAILS if failed else STATE_PASSES, "gaps": gaps, "detail": ""}

    # o aviso de ausência sai ao anunciar a intenção, antes dos passos que ainda podem falhar:
    # só a linha final de sucesso torna o recurso ausente um estado benigno
    announcedAbsent = any(_ABSENT_LINE.search(line) for line in lines)
    if announcedAbsent and any(_BUILT_LINE.match(line) for line in lines):
        return {"state": STATE_ABSENT, "gaps": [], "detail": ""}

    errors = [line.strip() for line in lines if _ERROR_LINE.match(line)]
    if any(_LINT_BLOCKED.match(line) for line in lines):
        codes = [match.group(1) for match in map(_LINT_CODE.match, lines) if match]
        return {"state": STATE_BLOCKED, "gaps": [], "detail": codes[0] if codes else ""}

    return {"state": STATE_ERROR, "gaps": [], "detail": errors[-1] if errors else "sem saída"}


def aggregate(results: list[dict]) -> dict:
    byKind = Counter(kind for result in results for kind, _ in result["gaps"])
    resources = [
        {
            "resource": result["resource"],
            "state": result["state"],
            "gaps": len(result["gaps"]),
            "detail": result["detail"],
            "gapDetails": [list(gap) for gap in result["gaps"]],
        }
        for result in results
    ]
    resources.sort(key=lambda entry: (-entry["gaps"], entry["resource"]))

    return {
        "passing": sum(1 for result in results if result["state"] == STATE_PASSES),
        "total": len(results),
        "byKind": dict(sorted(byKind.items(), key=lambda item: (-item[1], item[0]))),
        "resources": resources,
    }


def stateLabel(entry: dict) -> str:
    if entry["state"] in (STATE_BLOCKED, STATE_ERROR) and entry["detail"]:
        return f"{entry['state']} ({entry['detail']})"
    return entry["state"]


def render(report: dict) -> str:
    lines = [
        f"[INFO] sdk-{LANGUAGE} @ {report['sha']}",
        f"[INFO] {report['passing']} de {report['total']} passam o gate estrito",
        "",
    ]
    width = max((len(entry["resource"]) for entry in report["resources"]), default=0)
    for entry in report["resources"]:
        lines.append(f"  {entry['resource']:<{width}}  {entry['gaps']:>4}  {stateLabel(entry)}")

    lines += ["", "[INFO] gaps por tipo:"]
    lines += [f"  {kind:<12}  {count:>4}" for kind, count in report["byKind"].items()]
    return "\n".join(lines)


def runBuild(command: list[str], timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return (EXIT_TIMEOUT, f"[ERROR] build-resource excedeu {timeout}s")
    except OSError as error:
        return (EXIT_TIMEOUT, f"[ERROR] build-resource não executou: {error}")
    return (result.returncode, result.stdout + result.stderr)


def measure(resource: str, scratch: Path, runner) -> dict:
    # --into é um diretório descartável: recurso ausente no SDK é colocado lá, e o clone nunca é alterado
    command = [
        sys.executable, str(TOOLS_DIR / "build-resource.py"), resource,
        "--lang", LANGUAGE,
        "--into", str(scratch),
        "--advisory",
    ]
    _, output = runner(command, BUILD_TIMEOUT_SECONDS)
    return {"resource": resource, **parseGaps(output)}


def measureAll(resources: list[str], scratch: Path, runner, jobs: int) -> list[dict]:
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(lambda resource: measure(resource, scratch, runner), resources))


def parseArguments(argv: list[str] | None):
    parser = argparse.ArgumentParser(description="Placar de paridade do Java gerado contra o sdk-java")
    parser.add_argument("resources", nargs="*", help="recursos a medir (padrão: todos os schemas)")
    parser.add_argument("--json", dest="jsonPath", default=None, help="grava o relatório em JSON neste arquivo")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS, help=f"gerações simultâneas, cada uma sobe uma JVM (padrão {DEFAULT_JOBS})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, runner=None, javaRoot: Path | None = None, sha: str | None = None) -> int:
    args = parseArguments(argv)
    buildResource = loadTool("build-resource")

    if args.jobs < 1:
        emit("[ERROR] --jobs deve ser >= 1")
        return 2

    javaRoot = javaRoot or loadTool("derive-contract").referenceRepo(LANGUAGE)
    if javaRoot is None or not Path(javaRoot).is_dir():
        emit(f"[ERROR] sdk-{LANGUAGE} ausente em _references/ — rode `make clone-sdk-ref` ou defina SDK_JAVA")
        return 2

    if runner is None and not shutil.which("npx"):
        emit("[ERROR] npx ausente — necessário para o gerador")
        return 2

    if args.jsonPath and not Path(args.jsonPath).resolve().parent.is_dir():
        emit(f"[ERROR] --json: o diretório de {args.jsonPath} não existe")
        return 2

    known = schemaResources()
    unknown = [name for name in args.resources if name not in known]
    if unknown:
        emit(f"[ERROR] recurso(s) sem schema em apis/schemas: {', '.join(unknown)}")
        return 2

    requested = list(dict.fromkeys(args.resources)) or known

    scratch = Path(tempfile.mkdtemp(prefix="parity-into-"))
    try:
        results = measureAll(requested, scratch, runner or runBuild, args.jobs)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    report = {"sha": sha or buildResource.referenceSha(LANGUAGE), **aggregate(results)}
    emit(render(report))
    if args.jsonPath:
        Path(args.jsonPath).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
