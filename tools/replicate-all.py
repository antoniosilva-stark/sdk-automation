import os
import re
import sys
import json
import heapq
import shutil
import argparse
import tempfile
import functools
from pathlib import Path
from collections import Counter
from importlib.util import spec_from_file_location, module_from_spec

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent

LANGUAGE = "java"
BUILD_TIMEOUT_SECONDS = 600
EXIT_EXISTS = 4
MAVEN_ERROR_LINES = 5
GATE_GAP_LINES = 3
DETAIL_WIDTH = 100

STATE_GENERATED = "gerado"
STATE_UNSUPPORTED = "formato nao suportado"
STATE_GATE = "reprovado no gate"
STATE_EXISTS = "existe"
STATE_ERROR = "erro"
STATE_COMPILE = "erro de compilação"
STATE_DEPENDENCY = "dependência ausente"

SKIPPED_COMPILE = "(--skip-compile)"

_BUILT_LINE = re.compile(r"^\[OK\] \S+ construído e verificado em ", re.MULTILINE)
_UNSUPPORTED_LINE = re.compile(r"^\[ERROR\] formato nao suportado: (.+)$", re.MULTILINE)
_GATE_LINE = re.compile(r"^\s*\[ERROR\] assert-generated \S+ \(exit", re.MULTILINE)
_GAP_LINE = re.compile(r"^\s*\[ERROR\] .*? CONTRACT_GAP (\w+) (.+)$", re.MULTILINE)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


@functools.cache
def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schemaResources() -> list[str]:
    return loadTool("parity-report").schemaResources()


def referencePattern(resources: list[str]) -> re.Pattern:
    return loadTool("make-replica").referencePattern(resources)


def referencedNames(source: str, pattern: re.Pattern) -> list[str]:
    return loadTool("make-replica").referencedNames(source, pattern)


def dependenciesOf(source: Path, resources: list[str]) -> dict[str, list[str]]:
    """Derivadas do código do sdk-java real: é o que a compilação exige que já esteja no alvo."""
    replica = loadTool("make-replica")
    pattern = replica.referencePattern(resources)
    dependencies = {}
    for name in resources:
        path = source / replica.MAIN_PACKAGE / f"{name}.java"
        names = replica.referencedNames(replica.readText(path), pattern) if path.is_file() else []
        dependencies[name] = [dependency for dependency in names if dependency != name]
    return dependencies


def topologicalOrder(dependencies: dict[str, list[str]], requested: list[str]) -> tuple[list[str], list[str]]:
    """Kahn com fila de prioridade: o empate sai em ordem alfabética, então a ordem é determinística."""
    wanted = set(requested)
    blockers = {}
    dependents = {name: [] for name in wanted}
    for name in wanted:
        needed = [dependency for dependency in dependencies[name] if dependency in wanted]
        blockers[name] = len(needed)
        for dependency in needed:
            dependents[dependency].append(name)

    ready = [name for name, count in blockers.items() if count == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        name = heapq.heappop(ready)
        order.append(name)
        for dependent in dependents[name]:
            blockers[dependent] -= 1
            if blockers[dependent] == 0:
                heapq.heappush(ready, dependent)
    # inclui quem apenas depende de um ciclo: o contador deles também nunca zera
    return (order, sorted(name for name in wanted if blockers[name] > 0))


def outcome(state: str, detail: str = "") -> dict:
    return {"state": state, "detail": detail, "compiled": None}


def gateDetail(output: str) -> str:
    gaps = _GAP_LINE.findall(output)
    if gaps:
        return "; ".join(f"{kind} {text}" for kind, text in gaps[:GATE_GAP_LINES])
    errors = [line.strip() for line in output.splitlines() if line.lstrip().startswith("[ERROR]") and not _GATE_LINE.match(line)]
    return errors[0] if errors else ""


def classifyBuild(code: int, output: str) -> dict:
    """Estado do build-resource a partir do código de saída e do texto; só o texto distingue as falhas de exit 1."""
    if code == EXIT_EXISTS:
        return outcome(STATE_EXISTS)
    if code == 0 and _BUILT_LINE.search(output):
        return outcome(STATE_GENERATED)
    parity = loadTool("parity-report")
    parsed = parity.parseGaps(output)
    if parsed["state"] == parity.STATE_BLOCKED:
        return outcome(parity.STATE_BLOCKED, parsed["detail"])
    unsupported = _UNSUPPORTED_LINE.search(output)
    if unsupported:
        return outcome(STATE_UNSUPPORTED, unsupported.group(1))
    if _GATE_LINE.search(output):
        return outcome(STATE_GATE, gateDetail(output))
    return outcome(STATE_ERROR, parsed["detail"])


def copyStaging(staging: Path, out: Path) -> tuple[list[Path], str | None]:
    files = sorted(path for path in staging.rglob("*") if path.is_file())
    if not files:
        return ([], "o build não deixou arquivos no staging")
    targets = [out / path.relative_to(staging) for path in files]
    for target in targets:
        if target.exists():
            return ([], f"arquivo já existe: {target.relative_to(out)}")
    copied = []
    for source, target in zip(files, targets):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        except OSError as error:
            removeAll(copied)
            return ([], f"falha ao copiar {target.relative_to(out)}: {error}")
        copied.append(target)
    return (copied, None)


def removeAll(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def mavenErrors(output: str) -> list[str]:
    lines = [line.strip() for line in output.splitlines() if line.lstrip().startswith("[ERROR]")]
    return lines[:MAVEN_ERROR_LINES] or output.strip().splitlines()[-1:]


def compileTree(config: dict) -> tuple[bool, list[str]]:
    code, output = loadTool("make-replica").compileBase(config["out"], config["mavenArguments"], config["runner"])
    return (True, []) if code == 0 else (False, mavenErrors(output))


def buildResource(resource: str, staging: Path, advisory: bool, config: dict) -> tuple[int, str]:
    command = [
        sys.executable, str(TOOLS_DIR / "build-resource.py"), resource,
        "--lang", LANGUAGE,
        "--into", str(staging),
    ]
    command += ["--advisory"] if advisory else []
    # SDK_JAVA só no filho: a existência é medida contra o alvo, e o ambiente do processo não pode mudar
    return config["runner"](command, REPO_ROOT, BUILD_TIMEOUT_SECONDS, env={"SDK_JAVA": str(config["out"])})


def placeAndCompile(staging: Path, dependencies: list[str], config: dict) -> dict:
    out = config["out"]
    copied, problem = copyStaging(staging=staging, out=out)
    if problem:
        return outcome(STATE_ERROR, problem)
    if config["skipCompile"]:
        return outcome(STATE_GENERATED)

    compiled, errors = compileTree(config)
    if compiled:
        return {**outcome(STATE_GENERATED), "compiled": True}
    # o alvo volta a compilar: os próximos recursos dependem disso
    removeAll(copied)
    missing = [dependency for dependency in dependencies if not (out / loadTool("make-replica").MAIN_PACKAGE / f"{dependency}.java").is_file()]
    if missing:
        detail = ", ".join(missing) + (f"; mvn: {errors[0]}" if errors else "")
        return {**outcome(STATE_DEPENDENCY, detail), "compiled": False}
    return {**outcome(STATE_COMPILE, "\n".join(errors)), "compiled": False}


def replicateOne(resource: str, dependencies: list[str], advisory: bool, config: dict) -> dict:
    staging = Path(tempfile.mkdtemp(prefix="replicate-staging-"))
    try:
        code, output = buildResource(resource, staging, advisory, config)
        result = classifyBuild(code=code, output=output)
        if result["state"] != STATE_GENERATED:
            return result
        return placeAndCompile(staging, dependencies, config)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def replicateAll(order: list[str], dependencies: dict[str, list[str]], config: dict) -> list[dict]:
    entries = []
    for index, resource in enumerate(order, start=1):
        strict = replicateOne(resource, dependencies[resource], False, config)
        emit(f"[INFO] [{index}/{len(order)}] {resource}: {stateLabel(strict)}")
        entries.append({
            "order": index,
            "resource": resource,
            "deps": dependencies[resource],
            "strict": strict,
            "advisory": None,
        })
    # segunda passada na mesma ordem: o que a guarda de formato barrou, sem a guarda
    for entry in entries:
        if entry["strict"]["state"] != STATE_UNSUPPORTED:
            continue
        entry["advisory"] = replicateOne(entry["resource"], entry["deps"], True, config)
        emit(f"[INFO] {entry['resource']} (advisory): {stateLabel(entry['advisory'])}")
    return entries


def integrate(source: Path, config: dict, pattern: re.Pattern) -> dict:
    replica = loadTool("make-replica")
    real = source / replica.GSON_ADAPTERS
    if not real.is_file():
        return {"state": "pulada", "detail": "origem sem GsonAdapters"}
    mainPackage = config["out"] / replica.MAIN_PACKAGE
    missing = [name for name in referencedNames(replica.readText(real), pattern) if not (mainPackage / f"{name}.java").is_file()]
    if missing:
        return {"state": "pulada", "detail": f"faltam {', '.join(missing)}"}
    if config["skipCompile"]:
        return {"state": "pulada", "detail": SKIPPED_COMPILE}

    target = config["out"] / replica.GSON_ADAPTERS
    adapted = target.read_bytes()
    target.write_bytes(real.read_bytes())
    compiled = False
    try:
        compiled, errors = compileTree(config)
    finally:
        # também em Ctrl-C ou exceção: o alvo não pode ficar com o GsonAdapters real que não compila
        if not compiled:
            target.write_bytes(adapted)
    if compiled:
        return {"state": "ok", "detail": ""}
    return {"state": "falhou", "detail": "\n".join(errors)}


def buildReport(entries: list[dict], integration: dict, sha: str, out: Path, skipCompile: bool) -> dict:
    byState = Counter(entry["strict"]["state"] for entry in entries)
    return {
        "sha": sha,
        "out": str(out),
        "skipCompile": skipCompile,
        "total": len(entries),
        "generated": byState[STATE_GENERATED],
        "advisoryGenerated": sum(1 for entry in entries if (entry["advisory"] or {}).get("state") == STATE_GENERATED),
        "byState": dict(sorted(byState.items(), key=lambda item: (-item[1], item[0]))),
        "resources": entries,
        "integration": integration,
    }


def stateLabel(result: dict | None) -> str:
    if result is None:
        return "-"
    if result["state"] == loadTool("parity-report").STATE_BLOCKED and result["detail"]:
        return f"{result['state']} ({result['detail']})"
    return result["state"]


def shortDetail(entry: dict) -> str:
    for result in (entry["strict"], entry["advisory"]):
        detail = result["detail"] if result else ""
        if detail and stateLabel(result) == result["state"]:
            return detail.splitlines()[0][:DETAIL_WIDTH]
    return ""


def integrationLabel(integration: dict) -> str:
    state, detail = integration["state"], integration["detail"]
    if not detail:
        return state
    if detail == SKIPPED_COMPILE:
        return f"{state} {detail}"
    return f"{state}: {detail.splitlines()[0]}"


def render(report: dict) -> str:
    entries = report["resources"]
    # compiled null em todo recurso gerado = a compilação foi pulada
    compiling = "gerados (compilação pulada)" if report["skipCompile"] else "gerados e compilando"
    lines = [
        f"[INFO] replica sdk-{LANGUAGE} @ {report['sha']}",
        f"[INFO] {report['generated']} de {report['total']} {compiling} (strict),"
        f" +{report['advisoryGenerated']} via advisory",
        "",
    ]
    nameWidth = max((len(entry["resource"]) for entry in entries), default=0)
    strictWidth = max((len(stateLabel(entry["strict"])) for entry in entries), default=0)
    advisoryWidth = max((len(stateLabel(entry["advisory"])) for entry in entries), default=0)
    for entry in entries:
        lines.append(
            f"  {entry['order']:>3}  {entry['resource']:<{nameWidth}}  {stateLabel(entry['strict']):<{strictWidth}}"
            f"  {stateLabel(entry['advisory']):<{advisoryWidth}}  {shortDetail(entry)}".rstrip()
        )

    lines += ["", "[INFO] por estado (strict):"]
    lines += [f"  {state:<{strictWidth}}  {count:>4}" for state, count in report["byState"].items()]
    integration = report["integration"]
    lines += ["", f"[INFO] integração GsonAdapters real: {integrationLabel(integration)}"]
    lines += [f"    {line}" for line in integration["detail"].splitlines()[1:]]
    return "\n".join(lines)


def resolveKeepingLeaf(path: str) -> Path:
    # só o pai é resolvido: o validate do make-replica precisa ainda enxergar um symlink no próprio --out
    given = Path(path)
    return given.parent.resolve() / given.name


def jsonIssue(path: Path | None) -> str | None:
    if path is None:
        return None
    if path.is_dir():
        return f"--json: {path} é um diretório"
    if not path.parent.is_dir():
        return f"--json: o diretório de {path} não existe"
    if not os.access(path.parent, os.W_OK):
        return f"--json: o diretório de {path} não é gravável"
    return None


def usageIssue(args, out: Path, jsonPath: Path | None, source: Path | None, known: list[str], runner) -> str | None:
    issue = loadTool("make-replica").validate(out, source, needsMaven=runner is None and not args.skipCompile)
    if issue:
        return issue
    if runner is None and not shutil.which("npx"):
        return "npx ausente — necessário para o gerador"
    issue = jsonIssue(jsonPath)
    if issue:
        return issue
    unknown = [name for name in args.resources if name not in known]
    if unknown:
        return f"recurso(s) sem schema em apis/schemas: {', '.join(unknown)}"
    return None


def parseArguments(argv: list[str] | None):
    parser = argparse.ArgumentParser(description="Replica os recursos Java da spec num alvo sem eles e mede o que o pipeline gera")
    parser.add_argument("resources", nargs="*", help="recursos a replicar (padrão: todos os schemas)")
    parser.add_argument("--out", required=True, help="diretório da réplica (inexistente ou vazio); fica ao final")
    parser.add_argument("--from", dest="source", default=None, help="raiz do sdk-java (padrão: SDK_JAVA ou _references/sdk-java)")
    parser.add_argument("--json", dest="jsonPath", default=None, help="grava o relatório em JSON neste arquivo")
    parser.add_argument("--skip-compile", dest="skipCompile", action="store_true", help="não roda o mvn test-compile")
    parser.add_argument("--maven-arg", dest="mavenArguments", action="append", default=[], help="repassado ao mvn antes do goal (repetível)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, runner=None, source: Path | None = None,
         resources: list[str] | None = None, sha: str | None = None) -> int:
    args = parseArguments(argv)
    replica = loadTool("make-replica")
    # resolvido uma vez: o filho roda em outro cwd e um caminho relativo apontaria para outro lugar
    out = resolveKeepingLeaf(args.out)
    jsonPath = resolveKeepingLeaf(args.jsonPath) if args.jsonPath else None
    source = Path(source) if source else loadTool("derive-contract").referenceRepo(LANGUAGE, args.source)
    known = resources if resources is not None else schemaResources()

    issue = usageIssue(args, out, jsonPath, source, known, runner)
    if issue:
        emit(f"[ERROR] {issue}")
        return 2
    dependencies = dependenciesOf(source, known)
    order, stuck = topologicalOrder(dependencies=dependencies, requested=list(dict.fromkeys(args.resources)) or known)
    if stuck:
        emit(f"[ERROR] ciclo de dependência (ou dependente de ciclo) entre recursos: {', '.join(stuck)}")
        return 2

    runner = runner or replica.runCommand
    sha = sha or replica.sourceSha(source)
    baseArguments = ["--out", str(out), *[f"--maven-arg={argument}" for argument in args.mavenArguments]]
    baseArguments += ["--skip-compile"] if args.skipCompile else []
    # a base é o sdk-java sem os 42, mesmo quando só alguns são replicados: o resto não pode mascarar dependência
    if replica.main(baseArguments, runner=runner, source=source, resources=known, sha=sha) != 0:
        emit("[ERROR] base de replicação falhou")
        return 1

    config = {"out": out, "runner": runner, "mavenArguments": args.mavenArguments, "skipCompile": args.skipCompile}
    entries = replicateAll(order, dependencies, config)
    integration = integrate(source, config, referencePattern(known))
    report = buildReport(entries, integration, sha, out, args.skipCompile)
    emit(render(report))
    if not jsonPath:
        return 0
    try:
        jsonPath.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as error:
        # exit 2: a medição terminou, mas o artefato pedido não foi produzido
        emit(f"[ERROR] falha ao gravar --json: {error}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
