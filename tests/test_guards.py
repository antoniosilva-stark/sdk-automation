import shutil
from pathlib import Path

from conftest import GENERATOR_SKIP, commandWorks, missingGeneratorDependency, missingJavacDependency


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


def test_skipReasonNamesTheDependency():
    if GENERATOR_SKIP is None:
        assert shutil.which("npx")
        return
    assert "npx" in GENERATOR_SKIP or "JRE" in GENERATOR_SKIP