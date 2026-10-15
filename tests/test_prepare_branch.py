import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

TOOL = REPO_ROOT / "tools/prepare-branch.py"
BRANCH = "sdk-automation/widget"
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@local",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@local"}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                            env={**os.environ, **IDENTITY})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def remote(tmpPath):
    bare = tmpPath / "remote.git"
    _git(tmpPath, "init", "-q", "--bare", str(bare))
    seed = tmpPath / "seed"
    _git(tmpPath, "clone", "-q", f"file://{bare}", str(seed))
    _git(seed, "checkout", "-q", "-b", "master")
    _commit(seed, "base-1")
    _git(seed, "push", "-q", "origin", "master")
    return {"url": f"file://{bare}", "seed": seed, "root": tmpPath}


def _checkout(remote: dict) -> Path:
    target = remote["root"] / "target"
    _git(remote["root"], "clone", "-q", remote["url"], str(target))
    return target


def _run(target: Path):
    result = subprocess.run([sys.executable, str(TOOL), BRANCH, "master"], cwd=target,
                            capture_output=True, text=True, env={**os.environ, **IDENTITY})
    outputs = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return result.returncode, outputs, result.stderr


def _advanceBase(remote: dict) -> str:
    seed = remote["seed"]
    _git(seed, "checkout", "-q", "master")
    tip = _commit(seed, "base-2")
    _git(seed, "push", "-q", "origin", "master")
    return tip


def _pushBranch(remote: dict) -> str:
    seed = remote["seed"]
    _git(seed, "checkout", "-q", "-B", BRANCH, "master")
    tip = _commit(seed, "generated")
    _git(seed, "push", "-q", "origin", BRANCH)
    return tip


def test_absentBranchStartsFromTheBase(remote):
    target = _checkout(remote)

    code, outputs, _ = _run(target)

    assert code == 0
    assert outputs == {"exists": "false", "old": ""}
    assert _git(target, "rev-parse", "HEAD") == _git(target, "rev-parse", "origin/master")
    assert _git(target, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH


def test_branchThatContainsTheBaseIsReused(remote):
    branchTip = _pushBranch(remote)
    target = _checkout(remote)

    code, outputs, _ = _run(target)

    assert code == 0
    assert outputs == {"exists": "true", "old": ""}
    assert _git(target, "rev-parse", "HEAD") == branchTip


def test_branchBehindTheBaseIsRegeneratedOnTheCurrentBase(remote):
    branchTip = _pushBranch(remote)
    baseTip = _advanceBase(remote)
    target = _checkout(remote)

    code, outputs, log = _run(target)

    assert code == 0, log
    assert outputs == {"exists": "false", "old": branchTip}
    assert _git(target, "rev-parse", "HEAD") == baseTip
    assert "atras" in log


def test_baseThatMovedAfterTheCheckoutIsStillTheStartingPoint(remote):
    target = _checkout(remote)
    baseTip = _advanceBase(remote)

    code, outputs, _ = _run(target)

    assert code == 0
    assert outputs["exists"] == "false"
    assert _git(target, "rev-parse", "HEAD") == baseTip


def test_unreachableRemoteFailsInsteadOfReadingAsAbsent(remote):
    target = _checkout(remote)
    _git(target, "remote", "set-url", "origin", f"file://{remote['root']}/nao-existe.git")

    code, outputs, log = _run(target)

    assert code == 2
    assert outputs == {}
    assert "nao foi possivel" in log
