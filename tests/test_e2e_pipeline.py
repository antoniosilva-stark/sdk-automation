import os
import sys
import shutil
import pytest
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, requiresGenerator, runTool

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
    for relative in _declaredTargets():
        _git(repo, "add", "--", relative)

    staged = _git(repo, "diff", "--cached", "--name-only").split()
    if not staged:
        return False
    _git(repo, "commit", "-qm", f"Add {RESOURCE} resource")
    return True


@requiresGenerator
def test_fullChainLeavesNoUndeclaredArtifact(tmpPath, referenceWithout):
    repo = _repo(tmpPath)

    code, out = runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo),
                        env=referenceWithout(LANGUAGE, RESOURCE))
    assert code == 0, out

    untracked = _git(repo, "status", "--porcelain", "-uall").split()
    assert [entry for entry in untracked if entry != "??"] == _declaredTargets()


@requiresGenerator
def test_commitCarriesOnlyTheResourceFile(tmpPath, referenceWithout):
    repo = _repo(tmpPath)
    runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo),
            env=referenceWithout(LANGUAGE, RESOURCE))

    assert _commitDeclared(repo) is True

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == [
        "src/main/java/com/starkbank/SplitProfile.java",
    ]


@requiresGenerator
def test_secondRunCreatesNoCommit(tmpPath, referenceWithout):
    repo = _repo(tmpPath)
    reference = referenceWithout(LANGUAGE, RESOURCE)

    runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo), env=reference)
    assert _commitDeclared(repo) is True
    first = _git(repo, "rev-parse", "HEAD").strip()

    runTool("build-resource.py", RESOURCE, "--lang", LANGUAGE, "--into", str(repo), env=reference)
    assert _commitDeclared(repo) is False
    assert _git(repo, "rev-parse", "HEAD").strip() == first


def _isolatedCopyOfTheTooling(tmpPath):
    """Copia descartavel da ferramenta: corromper o template aqui nao toca no do repositorio."""
    root = tmpPath / "isolated"
    root.mkdir()
    for name in ("tools", "templates", "apis", "tests/contract", "tests/waivers"):
        shutil.copytree(REPO_ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("openapitools.json", "package.json"):
        shutil.copy(REPO_ROOT / name, root / name)
    for name in ("node_modules", "_references"):
        if (REPO_ROOT / name).exists():
            (root / name).symlink_to(REPO_ROOT / name)
    return root


@requiresGenerator
def test_isolatedCopyOfTheToolingBuildsWhenTheTemplateIsIntact(tmpPath, referenceWithout):
    repo = _repo(tmpPath)
    isolated = _isolatedCopyOfTheTooling(tmpPath)

    result = subprocess.run([sys.executable, str(isolated / "tools/build-resource.py"), RESOURCE,
                             "--lang", LANGUAGE, "--into", str(repo)], capture_output=True, text=True,
                            env={**os.environ, **referenceWithout(LANGUAGE, RESOURCE)})

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "src/main/java/com/starkbank/SplitProfile.java").is_file()


@requiresGenerator
def test_corruptTemplateAbortsBeforePlacing(tmpPath, referenceWithout):
    repo = _repo(tmpPath)
    isolated = _isolatedCopyOfTheTooling(tmpPath)
    template = isolated / "templates/java/model.mustache"

    original = template.read_text(encoding="utf-8")
    corrompido = original.replace("extends Resource {", "extends Resource {\n    public String ;", 1)
    assert corrompido != original, "a corrupcao nao pegou: o teste passaria sem testar nada"
    template.write_text(corrompido, encoding="utf-8")
    scratch = tmpPath / "scratch"
    scratch.mkdir()
    result = subprocess.run([sys.executable, str(isolated / "tools/build-resource.py"), RESOURCE,
                             "--lang", LANGUAGE, "--into", str(repo)], capture_output=True, text=True,
                            env={**os.environ, **referenceWithout(LANGUAGE, RESOURCE), "TMPDIR": str(scratch)})
    code, out = result.returncode, result.stdout

    assert code == 1, out
    leaked = [entry.name for entry in scratch.iterdir() if entry.name.startswith("build-resource-")]
    assert leaked == [], f"o diretorio temporario do build vazou no caminho de falha: {leaked}"
    assert list((repo / "src").rglob("*.java")) == []
    assert _git(repo, "status", "--porcelain", "-uall").strip() == ""
