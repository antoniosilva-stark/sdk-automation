import ast
import shutil
import subprocess
from pathlib import Path

from conftest import GENERATOR_SKIP, commandWorks, missingGeneratorDependency, missingJavacDependency, REPO_ROOT


def _fakeBin(tmpPath: Path, name: str, script: str) -> Path:
    root = tmpPath / "bin"
    root.mkdir(exist_ok=True)
    fake = root / name
    fake.write_text(script, encoding="utf-8")
    fake.chmod(0o755)
    return root


def test_commandWorksAcceptsCommandExitingZero(tmpPath, monkeypatch):
    root = _fakeBin(tmpPath, "probe", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", str(root))
    assert commandWorks("probe") is True


def test_commandWorksRejectsCommandExitingNonZero(tmpPath, monkeypatch):
    root = _fakeBin(tmpPath, "probe", "#!/bin/sh\nexit 1\n")
    monkeypatch.setenv("PATH", str(root))
    assert commandWorks("probe") is False


def test_commandWorksRejectsMissingCommand(tmpPath, monkeypatch):
    monkeypatch.setenv("PATH", str(tmpPath))
    assert commandWorks("nao-existe-mesmo") is False


def test_javaPresentButBrokenReportsJre(tmpPath, monkeypatch):
    root = _fakeBin(tmpPath, "java", "#!/bin/sh\nexit 1\n")
    _fakeBin(tmpPath, "npx", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", str(root))

    reason = missingGeneratorDependency()
    assert reason is not None
    assert "JRE" in reason


def test_missingNpxReportsNpx(tmpPath, monkeypatch):
    root = _fakeBin(tmpPath, "java", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", str(root))

    assert missingGeneratorDependency() == "npx ausente"


def test_completeEnvironmentDoesNotSkip(tmpPath, monkeypatch):
    root = _fakeBin(tmpPath, "java", "#!/bin/sh\nexit 0\n")
    _fakeBin(tmpPath, "npx", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", str(root))

    assert missingGeneratorDependency() is None


def test_brokenJavacReportsJavac(tmpPath, monkeypatch):
    root = _fakeBin(tmpPath, "javac", "#!/bin/sh\nexit 1\n")
    monkeypatch.setenv("PATH", str(root))

    reason = missingJavacDependency()
    assert reason is not None
    assert "javac" in reason


def test_noTestFileBindsTheReferenceToHome():
    needle = "Path" + ".home()"
    offenders = [
        path.name
        for path in sorted((REPO_ROOT / "tests").rglob("*.py"))
        if path.name != "conftest.py" and needle in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"resolucao de referencia espalhada: {', '.join(offenders)}"


def test_noSourceHardcodesAPersonalCheckout():
    needle = "workspace" + "/bank"
    searched = sorted((REPO_ROOT / "tools").glob("*.py")) + sorted((REPO_ROOT / "tests").rglob("*.py"))
    searched.append(REPO_ROOT / "Makefile")

    offenders = [
        path.name for path in searched
        if path.name != "test_guards.py" and needle in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"checkout pessoal cravado em: {', '.join(offenders)}"


def test_referenceResolutionIsSharedNotDuplicated(conftest):
    assert conftest.resolvedPythonSdk.__module__ == "conftest"
    assert callable(conftest.resolvedJavaSdk)
    assert callable(conftest.resolvedNodeSdk)


def test_skipReasonNamesTheDependency():
    if GENERATOR_SKIP is None:
        assert shutil.which("npx")
        return
    assert "npx" in GENERATOR_SKIP or "JRE" in GENERATOR_SKIP

def test_everyBuildResourceCallIsGuardedByTheGeneratorMark():
    source = (REPO_ROOT / "tests/test_build_resource.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    desguardados = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        body = "\n".join(lines[node.lineno - 1:node.end_lineno])
        if 'runTool("build-resource.py"' not in body:
            continue
        chegaAoGerador = "assert code == 0" in body or "read_text" in body
        if not chegaAoGerador:
            continue
        marks = {decorator.id for decorator in node.decorator_list if isinstance(decorator, ast.Name)}
        if "requiresGenerator" not in marks:
            desguardados.append(node.name)

    assert desguardados == [], f"chamam o gerador sem @requiresGenerator: {desguardados}"


def test_noToolParsesYamlWithThePurePythonLoader():
    lentos = []
    for path in sorted((REPO_ROOT / "tools").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "yaml.safe_load(" in source:
            lentos.append(path.name)
        if "yaml.load(" in source and "CSafeLoader" not in source:
            lentos.append(f"{path.name} (sem fallback declarado)")

    assert lentos == [], f"parse lento de YAML: {lentos}"


def test_theFastLoaderIsActuallyAvailableHere():
    import yaml

    assert hasattr(yaml, "CSafeLoader"), "libyaml ausente: PyYAML instalado sem a extensao C"


def test_everyFastLoaderActuallyRuns():
    from importlib.util import module_from_spec, spec_from_file_location

    quebrados = []
    for path in sorted((REPO_ROOT / "tools").glob("*.py")):
        if "def loadFast" not in path.read_text(encoding="utf-8"):
            continue
        spec = spec_from_file_location(path.stem.replace("-", "_"), path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            assert module.loadFast("componente: valor") == {"componente": "valor"}
        except Exception as error:
            quebrados.append(f"{path.name}: {type(error).__name__} {error}")

    assert quebrados == [], f"loadFast quebrado: {quebrados}"


def test_referenceRefreshFailsInsteadOfSwallowing(tmpPath):
    binario = tmpPath / "bin"
    binario.mkdir()
    (binario / "git").write_text(
        '#!/bin/sh\ncase "$*" in *sdk-python*) exit 1;; *) exit 0;; esac\n',
        encoding="utf-8")
    (binario / "git").chmod(0o755)

    resultado = subprocess.run(
        ["make", "refresh-sdk-ref"], cwd=REPO_ROOT, capture_output=True, text=True,
        env={"PATH": f"{binario}:/usr/bin:/bin", "HOME": str(tmpPath)},
    )

    assert resultado.returncode != 0, "o fetch de sdk-python falhou e o alvo saiu verde"
    assert "fetch falhou" in resultado.stdout + resultado.stderr


def test_noTestPinsABranchNameThatVariesBetweenForks():
    remoto = "origin" + "/"
    fixos = []
    arquivos = sorted((REPO_ROOT / "tests").rglob("*.py")) + sorted((REPO_ROOT / "tests").rglob("*.js"))
    for path in arquivos:
        if path.name == Path(__file__).name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if remoto + "development" in line or remoto + "main" in line:
                fixos.append(f"{path.name}:{number}")

    assert fixos == [], f"nome de branch fixo em teste: {fixos}"


GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(cwd), **GIT_IDENTITY},
    )
    return result.stdout.strip()


def _remoteWithTwoCommits(tmpPath: Path) -> tuple[str, str]:
    remote = tmpPath / "remote" / "sdk-demo.git"
    remote.mkdir(parents=True)
    _git(remote, "init", "-q")
    (remote / "marker.txt").write_text("first", encoding="utf-8")
    _git(remote, "add", ".")
    _git(remote, "commit", "-q", "-m", "first")
    first = _git(remote, "rev-parse", "HEAD")
    (remote / "marker.txt").write_text("second", encoding="utf-8")
    _git(remote, "commit", "-q", "-am", "second")
    return f"file://{remote.parent}", first


def _makeReference(workdir: Path, remoteUrl: str, pin: str) -> subprocess.CompletedProcess:
    workdir.mkdir(exist_ok=True)
    return subprocess.run(
        ["make", "-f", str(REPO_ROOT / "Makefile"), "reference",
         "REPO=sdk-demo", "MARKER=marker.txt", f"PIN={pin}", f"SDK_REMOTE={remoteUrl}"],
        cwd=workdir, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(workdir)},
    )


def test_pinnedReferenceChecksOutThePinnedCommitNotTheRemoteHead(tmpPath):
    remoteUrl, first = _remoteWithTwoCommits(tmpPath)
    workdir = tmpPath / "work"

    result = _makeReference(workdir, remoteUrl, first)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (workdir / "_references/sdk-demo/marker.txt").read_text(encoding="utf-8") == "first"
    assert _git(workdir / "_references/sdk-demo", "rev-parse", "HEAD") == first


def test_pinnedReferenceMovesAnExistingCloneToThePin(tmpPath):
    remoteUrl, first = _remoteWithTwoCommits(tmpPath)
    workdir = tmpPath / "work"
    workdir.mkdir()
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"{remoteUrl}/sdk-demo.git", "_references/sdk-demo"],
        cwd=workdir, check=True, capture_output=True,
    )
    assert (workdir / "_references/sdk-demo/marker.txt").read_text(encoding="utf-8") == "second"

    result = _makeReference(workdir, remoteUrl, first)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (workdir / "_references/sdk-demo/marker.txt").read_text(encoding="utf-8") == "first"


def test_pinnedReferenceFailsWhenThePinDoesNotExist(tmpPath):
    remoteUrl, _ = _remoteWithTwoCommits(tmpPath)

    result = _makeReference(tmpPath / "work", remoteUrl, "0" * 40)

    assert result.returncode != 0
    assert "nao foi possivel fixar" in result.stdout + result.stderr


def test_pinnedReferenceLeavesAnOverrideUntouched(tmpPath):
    remoteUrl, first = _remoteWithTwoCommits(tmpPath)
    personal = tmpPath / "personal-checkout"
    personal.mkdir()
    (personal / "marker.txt").write_text("my local work", encoding="utf-8")
    workdir = tmpPath / "work"
    (workdir / "_references").mkdir(parents=True)
    (workdir / "_references/sdk-demo").symlink_to(personal)

    result = _makeReference(workdir, remoteUrl, first)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (personal / "marker.txt").read_text(encoding="utf-8") == "my local work"
    assert not (personal / ".git").exists()


def test_breakingChangeBaseFollowsTheRemoteDefaultBranch():
    result = subprocess.run(["make", "-n", "check-bc"], cwd=REPO_ROOT, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert '--base "origin/' in result.stdout
    assert "symbolic-ref" not in result.stdout, "a base tem de ser resolvida, nao repassada como comando"


def test_npmScriptsThatRunTheDetectorGiveItABase():
    import json
    scripts = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]

    for name, command in scripts.items():
        if "breaking-change-detector" in command:
            assert "--base" in command, f"npm run {name}: o detector exige --base"


def test_javaConfigMapsTheSameTypesAsTheBuild():
    import yaml
    import importlib.util
    config = yaml.safe_load((REPO_ROOT / "tools/generator-java-config.yaml").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("build_resource", REPO_ROOT / "tools/build-resource.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extra = module.GENERATORS["java"][0]["extra"]
    pipeline = dict(pair.split("=") for pair in extra[extra.index("--type-mappings") + 1].split(","))

    assert config["typeMappings"] == pipeline, "make generate-all emitiria Java diferente do pipeline"
