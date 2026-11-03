import os
import re
import sys
import shutil
import signal
import argparse
import functools
import subprocess
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

TOOLS_DIR = Path(__file__).resolve().parent

LANGUAGE = "java"
MAIN_PACKAGE = Path("src/main/java/com/starkbank")
TEST_ROOT = Path("src/test/java")
GSON_ADAPTERS = MAIN_PACKAGE / "utils/GsonAdapters.java"
COMPILE_TIMEOUT_SECONDS = 600
EXIT_TIMEOUT = 124
TAIL_LINES = 20
KILL_DRAIN_SECONDS = 5


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
    return re.compile(r"\b(?:" + "|".join(re.escape(name) for name in resources) + r")\b")


def readText(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def referencedNames(source: str, pattern: re.Pattern) -> list[str]:
    """Só código conta: comentário, Javadoc e conteúdo de string que citam o recurso não quebram a compilação."""
    code = "\n".join(loadTool("derive-contract").withoutNoise(source))
    return sorted(set(pattern.findall(code)))


def adaptGsonAdapters(source: str, resources: list[str]) -> str:
    # por instrução, não por linha: registerTypeAdapter multilinha removido pela metade quebra o Java
    pattern = referencePattern(resources)
    lines = source.split("\n")
    noiseFree = loadTool("derive-contract").withoutNoise(source)
    kept = []
    statement = []
    matched = False
    for line, code in zip(lines, noiseFree):
        statement.append(line)
        matched = matched or bool(pattern.search(code))
        if code.strip() and not code.rstrip().endswith((";", "{", "}")):
            continue
        if not matched:
            kept.extend(statement)
        statement = []
        matched = False
    kept.extend(statement)
    return "\n".join(kept)


def validate(out: Path, source: Path | None, needsMaven: bool) -> str | None:
    if source is None or not (source / MAIN_PACKAGE).is_dir():
        return f"sdk-{LANGUAGE} ausente ou sem {MAIN_PACKAGE} — rode `make clone-sdk-ref`, defina SDK_JAVA ou passe --from"
    if out.is_symlink():
        return f"--out ({out}) é um symlink; informe um diretório real"
    resolvedOut, resolvedSource = out.resolve(), source.resolve()
    if resolvedOut.is_relative_to(resolvedSource) or resolvedSource.is_relative_to(resolvedOut):
        return f"--out ({out}) não pode estar dentro do sdk de origem nem conter a origem"
    if not out.parent.is_dir():
        return f"o diretório pai de --out ({out.parent}) não existe"
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        return f"--out ({out}) existe e não é um diretório vazio: apague o diretório ou use outro --out"
    if needsMaven and not shutil.which("mvn"):
        return "mvn ausente — necessário para compilar (ou use --skip-compile)"
    return None


def copyTree(source: Path, out: Path) -> None:
    shutil.copytree(
        source,
        out,
        symlinks=False,
        ignore=shutil.ignore_patterns(".git", "target"),
        ignore_dangling_symlinks=True,
        dirs_exist_ok=True,
    )


def removeResources(out: Path, resources: list[str]) -> list[str]:
    removed = []
    for name in resources:
        path = out / MAIN_PACKAGE / f"{name}.java"
        if not path.is_file():
            continue
        path.unlink()
        removed.append(name)
    return removed


def removeResourceTests(out: Path, resources: list[str]) -> list[str]:
    removed = []
    for name in resources:
        path = out / TEST_ROOT / f"Test{name}.java"
        if not path.is_file():
            continue
        path.unlink()
        removed.append(path.name)
    return removed


def findReferences(root: Path, pattern: re.Pattern) -> dict[str, list[str]]:
    found = {}
    for path in sorted(root.rglob("*.java")):
        names = referencedNames(readText(path), pattern)
        if names:
            found[str(path)] = names
    return found


def removeDependentTests(out: Path, pattern: re.Pattern) -> list[str]:
    testRoot = out / TEST_ROOT
    dependents = []
    for path in findReferences(testRoot, pattern):
        Path(path).unlink()
        dependents.append(str(Path(path).relative_to(testRoot)))
    return dependents


def adaptGsonFile(out: Path, resources: list[str]) -> list[str]:
    path = out / GSON_ADAPTERS
    if not path.is_file():
        return []
    original = readText(path)
    adapted = adaptGsonAdapters(source=original, resources=resources)
    if adapted == original:
        return []
    path.write_bytes(adapted.encode("utf-8"))
    return [str(GSON_ADAPTERS)]


def runCommand(command: list[str], cwd: Path, timeout: int, env: dict | None = None) -> tuple[int, str]:
    # grupo próprio: o mvn forka a JVM, e matar só o mvn deixaria o filho vivo segurando o pipe
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env={**os.environ, **env} if env else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as error:
        return (EXIT_TIMEOUT, f"[ERROR] {command[0]} não executou: {error}")
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        killGroup(process.pid)
        drainAfterKill(process)
        return (EXIT_TIMEOUT, f"[ERROR] {command[0]} excedeu {timeout}s")
    except BaseException:
        killGroup(process.pid)
        raise
    return (process.returncode, output)


def killGroup(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def drainAfterKill(process: subprocess.Popen) -> None:
    # um neto fora do grupo pode segurar o pipe; sem limite o drain travaria
    try:
        process.communicate(timeout=KILL_DRAIN_SECONDS)
    except subprocess.TimeoutExpired:
        return


def compileBase(out: Path, mavenArguments: list[str], runner) -> tuple[int, str]:
    command = ["mvn", "-q", "-B", "--no-transfer-progress", *mavenArguments, "test-compile"]
    return runner(command, out, COMPILE_TIMEOUT_SECONDS)


def sourceSha(source: Path) -> str:
    result = subprocess.run(["git", "-C", str(source), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "SHA indisponível"


def parseArguments(argv: list[str] | None):
    parser = argparse.ArgumentParser(description="Monta a base de replicação: sdk-java sem os recursos gerados")
    parser.add_argument("--out", required=True, help="diretório de saída (inexistente ou vazio)")
    parser.add_argument("--from", dest="source", default=None, help="raiz do sdk-java (padrão: SDK_JAVA ou _references/sdk-java)")
    parser.add_argument("--skip-compile", dest="skipCompile", action="store_true", help="não roda o mvn test-compile")
    parser.add_argument("--maven-arg", dest="mavenArguments", action="append", default=[], help="repassado ao mvn antes do goal (repetível)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, runner=None, source: Path | None = None,
         resources: list[str] | None = None, sha: str | None = None) -> int:
    args = parseArguments(argv)
    out = Path(args.out)
    source = Path(source) if source else loadTool("derive-contract").referenceRepo(LANGUAGE, args.source)
    resources = resources if resources is not None else schemaResources()

    issue = validate(out, source, needsMaven=runner is None and not args.skipCompile)
    if issue:
        emit(f"[ERROR] {issue}")
        return 2
    if not resources:
        emit("[ERROR] nenhum recurso a remover")
        return 2

    emit(f"[INFO] origem: {source} @ {sha or sourceSha(source)}")
    pattern = referencePattern(resources)
    try:
        copyTree(source, out)
        removedResources = removeResources(out, resources)
        removedTests = removeResourceTests(out, resources)
        dependents = removeDependentTests(out, pattern)
        adapted = adaptGsonFile(out, resources)
    except OSError as error:
        emit(f"[ERROR] falha ao montar a base: {error}")
        return 2
    emit(f"[INFO] {len(removedResources)} recurso(s) removido(s)")
    emit(f"[INFO] testes removidos: {len(removedTests)}")
    emit(f"[INFO] dependentes removidos: {len(dependents)}")
    for name in dependents:
        emit(f"    {name}")
    emit(f"[INFO] arquivos adaptados: {len(adapted)}")
    for name in adapted:
        emit(f"    {name}")

    leftovers = findReferences(out / "src", pattern)
    for path, names in leftovers.items():
        emit(f"[ERROR] {Path(path).relative_to(out)} ainda referencia recurso removido: {', '.join(names)}")
    if leftovers:
        return 1

    if args.skipCompile:
        emit("[INFO] compilação pulada (--skip-compile)")
        return 0

    code, output = compileBase(out, args.mavenArguments, runner or runCommand)
    if code != 0:
        emit(f"[ERROR] mvn test-compile falhou (exit {code}); últimas {TAIL_LINES} linhas:")
        emit("\n".join(output.splitlines()[-TAIL_LINES:]))
        return 1
    emit("[OK] base de replicação compila")
    return 0


if __name__ == "__main__":
    sys.exit(main())
