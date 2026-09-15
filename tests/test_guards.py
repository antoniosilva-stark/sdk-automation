import ast
import shutil
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
    """A referencia resolvida vive num lugar so: o conftest, que delega aos tools.

    Presa ao diretorio do usuario, a trava existe na maquina de quem escreveu e fica
    inerte no CI —
    foi o que aconteceu com o golden, que seguiu pulado mesmo depois do clone-sdk-ref.
    """
    needle = "Path" + ".home()"
    offenders = [
        path.name
        for path in sorted((REPO_ROOT / "tests").rglob("*.py"))
        if path.name != "conftest.py" and needle in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"resolucao de referencia espalhada: {', '.join(offenders)}"


def test_noSourceHardcodesAPersonalCheckout():
    """A referencia canonica e o clone em _references/, feito pelo setup do projeto.

    Cravar o layout de diretorio de uma pessoa faz o projeto funcionar na maquina dela e
    nao na dos outros. Quem quiser apontar para um clone proprio usa SDK_PYTHON,
    SDK_JAVA ou SDK_NODE.
    """
    needle = "workspace" + "/bank"
    searched = sorted((REPO_ROOT / "tools").glob("*.py")) + sorted((REPO_ROOT / "tests").rglob("*.py"))
    searched.append(REPO_ROOT / "Makefile")

    offenders = [
        path.name for path in searched
        if path.name != "test_guards.py" and needle in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"checkout pessoal cravado em: {', '.join(offenders)}"


def test_referenceResolutionIsSharedNotDuplicated(conftest):
    """Cada tool resolve a referencia que ele le; o teste delega, nao reimplementa."""
    assert conftest.resolvedPythonSdk.__module__ == "conftest"
    assert callable(conftest.resolvedJavaSdk)
    assert callable(conftest.resolvedNodeSdk)


def test_skipReasonNamesTheDependency():
    if GENERATOR_SKIP is None:
        assert shutil.which("npx")
        return
    assert "npx" in GENERATOR_SKIP or "JRE" in GENERATOR_SKIP

def test_everyBuildResourceCallIsGuardedByTheGeneratorMark():
    """Teste que espera o build chegar ao gerador precisa do marcador: sem JRE ele reprova
    enquanto as irmas dele pulam. Quem aborta antes de gerar — linguagem invalida, destino
    ausente, lint reprovado — nao precisa.
    """
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
