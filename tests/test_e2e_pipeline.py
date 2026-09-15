import json
import shutil
import pytest
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, requiresGenerator, runTool

SDK_PYTHON = Path.home() / "workspace/bank/sdk-python"
VENDORED = REPO_ROOT / "_references/sdk-python"
REAL_JAVA = REPO_ROOT / "_references/sdk-java/src/main/java/com/starkbank"

RESOURCE = "SplitProfile"
LANGUAGE = "java"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout


def _repo(tmpPath: Path) -> Path:
    repo = tmpPath / "target"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "sync@test")
    _git(repo, "config", "user.name", "sync")
    (repo / "README.md").write_text("alvo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    return repo


def _declaredTargets() -> list[str]:
    code, out = runTool("place-generated.py", RESOURCE, "--lang", LANGUAGE, "--targets")
    assert code == 0, out
    return out.strip().splitlines()


def _commitDeclared(repo: Path) -> bool:
    """Espelha o step do workflow: adiciona so o que o layout declara e commita se mudou."""
    for relative in _declaredTargets():
        _git(repo, "add", "--", relative)

    staged = _git(repo, "diff", "--cached", "--name-only").split()
    if not staged:
        return False
    _git(repo, "commit", "-qm", f"Add {RESOURCE} resource")
    return True


@pytest.mark.skipif(
    not ((SDK_PYTHON / "starkbank").is_dir() or (VENDORED / "starkbank").is_dir())
    or not REAL_JAVA.is_dir(),
    reason="referências do Stark Bank não resolvidas",
)
def test_targetResourceComesFromDiscoveryNotAList():
    code, out = runTool("list-gaps.py", "--json")

    assert code == 0, out
    assert json.loads(out)["gaps"] == [RESOURCE]


@requiresGenerator
def test_fullChainLeavesNoUndeclaredArtifact(tmpPath):
    """Criterio da entrega: o `git status` pre-commit so mostra o que ferramenta escreveu."""
    repo = _repo(tmpPath)

    code, out = runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo))
    assert code == 0, out

    untracked = _git(repo, "status", "--porcelain", "-uall").split()
    assert [entry for entry in untracked if entry != "??"] == _declaredTargets()


@requiresGenerator
def test_commitCarriesResourceAndTestTogether(tmpPath):
    repo = _repo(tmpPath)
    runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo))

    assert _commitDeclared(repo) is True

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == [
        "src/main/java/com/starkbank/SplitProfile.java",
        "src/test/java/TestSplitProfile.java",
    ]


@requiresGenerator
def test_secondRunCreatesNoCommit(tmpPath):
    """Idempotencia: rodar 2x sobre a mesma branch nao pode gerar commit vazio nem duplicar PR."""
    repo = _repo(tmpPath)

    runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo))
    assert _commitDeclared(repo) is True
    first = _git(repo, "rev-parse", "HEAD").strip()

    runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo))
    assert _commitDeclared(repo) is False
    assert _git(repo, "rev-parse", "HEAD").strip() == first


@requiresGenerator
def test_corruptTemplateAbortsBeforePlacing(tmpPath):
    """Teste negativo: defeito no template nao pode chegar ao repo alvo."""
    repo = _repo(tmpPath)
    template = REPO_ROOT / "templates/java/model.mustache"
    backup = tmpPath / "model.mustache.bak"
    shutil.copy(template, backup)

    try:
        template.write_text(
            template.read_text(encoding="utf-8").replace(
                "public class {{classname}} extends Resource {",
                "public class {{classname}} extends Resource {\n    public String {{naoExiste}};",
                1,
            ),
            encoding="utf-8",
        )
        code, out = runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo))
    finally:
        shutil.copy(backup, template)

    assert code == 1, out
    assert list((repo / "src").rglob("*.java")) == []
    assert _git(repo, "status", "--porcelain", "-uall").strip() == ""
