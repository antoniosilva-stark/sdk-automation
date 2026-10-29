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


def _commit(repo: Path, name: str, author: str = "t@local") -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", f"user.email={author}", "commit", "-q", "--author", f"x <{author}>", "-m", name)
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


def _run(target: Path, *extra: str):
    result = subprocess.run([sys.executable, str(TOOL), BRANCH, "master", "--bot-email", "t@local", *extra], cwd=target,
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


def test_branchBehindTheBaseWithAForeignCommitFailsInsteadOfDiscardingIt(remote):
    seed = remote["seed"]
    _pushBranch(remote)
    _commit(seed, "reviewer-fix", author="reviewer@company.com")
    _git(seed, "push", "-q", "origin", BRANCH)
    _advanceBase(remote)
    target = _checkout(remote)

    code, outputs, log = _run(target)

    assert code == 3
    assert outputs == {}
    assert "reviewer@company.com" in log


def test_foreignCommitOnABranchThatContainsTheBaseIsKept(remote):
    seed = remote["seed"]
    _pushBranch(remote)
    tip = _commit(seed, "reviewer-fix", author="reviewer@company.com")
    _git(seed, "push", "-q", "origin", BRANCH)
    target = _checkout(remote)

    code, outputs, _ = _run(target)

    assert code == 0
    assert outputs == {"exists": "true", "old": ""}
    assert _git(target, "rev-parse", "HEAD") == tip


def test_decoyRefWithTheSameSuffixIsNotTheBranch(remote):
    seed = remote["seed"]
    _git(seed, "checkout", "-q", "-B", f"x/{BRANCH}", "master")
    _commit(seed, "decoy")
    _git(seed, "push", "-q", "origin", f"x/{BRANCH}")
    target = _checkout(remote)

    code, outputs, _ = _run(target)

    assert code == 0
    assert outputs == {"exists": "false", "old": ""}


def test_failedCheckoutFailsInsteadOfReportingSuccess(remote):
    target = _checkout(remote)
    (remote["seed"] / "base-1").write_text("changed upstream", encoding="utf-8")
    _git(remote["seed"], "commit", "-q", "-am", "touch base-1")
    _git(remote["seed"], "push", "-q", "origin", "master")
    (target / "base-1").write_text("local edit", encoding="utf-8")

    code, outputs, log = _run(target)

    assert code == 2
    assert outputs == {}
    assert "checkout" in log


def test_shallowCloneFarBehindTheBaseDoesNotBlameTheBaseAuthors(remote):
    seed = remote["seed"]
    _pushBranch(remote)
    _git(seed, "checkout", "-q", "master")
    for number in range(120):
        _commit(seed, f"upstream-{number}", author="human@company.com")
    _git(seed, "push", "-q", "origin", "master")
    target = remote["root"] / "shallow"
    _git(remote["root"], "clone", "-q", "--depth", "1", remote["url"], str(target))

    code, outputs, log = _run(target)

    assert code == 0, log
    assert outputs["exists"] == "false"
    assert outputs["old"] != ""


def _allowed(remote: dict, *paths: str) -> str:
    listing = remote["root"] / "allowed.txt"
    listing.write_text("\n".join(paths) + "\n", encoding="utf-8")
    return str(listing)


def test_reusedBranchThatTouchesOnlyDeclaredPathsIsKept(remote):
    _pushBranch(remote)
    target = _checkout(remote)

    code, outputs, log = _run(target, "--allowed-paths-file", _allowed(remote, "generated"))

    assert code == 0, log
    assert outputs == {"exists": "true", "old": ""}


def test_reusedBranchWithAFileOutsideTheDeclaredPathsIsRefused(remote):
    seed = remote["seed"]
    _pushBranch(remote)
    _commit(seed, "pom.xml", author="attacker@company.com")
    _git(seed, "push", "-q", "origin", BRANCH)
    target = _checkout(remote)

    code, outputs, log = _run(target, "--allowed-paths-file", _allowed(remote, "generated"))

    assert code == 3
    assert outputs == {}
    assert "pom.xml" in log


def test_renameOntoADeclaredPathIsStillAStrayFile(remote):
    seed = remote["seed"]
    _git(seed, "checkout", "-q", "master")
    (seed / "Checks.java").write_text("class Checks { /* conteudo longo o bastante para o rename */ }" * 5, encoding="utf-8")
    _git(seed, "add", "Checks.java")
    _git(seed, "commit", "-q", "-m", "checks")
    _git(seed, "push", "-q", "origin", "master")
    _git(seed, "checkout", "-q", "-B", BRANCH, "master")
    _git(seed, "mv", "Checks.java", "Widget.java")
    _git(seed, "commit", "-q", "-m", "rename")
    _git(seed, "push", "-q", "origin", BRANCH)
    target = _checkout(remote)

    code, outputs, log = _run(target, "--allowed-paths-file", _allowed(remote, "Widget.java"))

    assert code == 3, log
    assert "Checks.java" in log


def test_upToDateBranchLongerThanTheFetchDepthIsReusedNotRegenerated(remote):
    seed = remote["seed"]
    _git(seed, "checkout", "-q", "-B", BRANCH, "master")
    for number in range(120):
        _commit(seed, f"generated-{number}")
    _git(seed, "push", "-q", "origin", BRANCH)
    target = remote["root"] / "shallow"
    _git(remote["root"], "clone", "-q", "--depth", "1", remote["url"], str(target))

    code, outputs, log = _run(target)

    assert code == 0, log
    assert outputs == {"exists": "true", "old": ""}


def _addToBase(remote: dict, name: str) -> None:
    seed = remote["seed"]
    _git(seed, "checkout", "-q", "master")
    _commit(seed, name)
    _git(seed, "push", "-q", "origin", "master")


def _localBranches(target: Path) -> str:
    return _git(target, "branch", "--list", BRANCH)


def test_createOnlyWithAPathAbsentFromTheBaseCreatesTheBranch(remote):
    target = _checkout(remote)

    code, outputs, log = _run(target, "--allowed-paths-file", _allowed(remote, "Widget.java"), "--create-only")

    assert code == 0, log
    assert outputs == {"exists": "false", "old": ""}
    assert _git(target, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH


def test_createOnlyFailsWhenADeclaredPathAlreadyExistsOnTheBase(remote):
    target = _checkout(remote)
    headBefore = _git(target, "symbolic-ref", "-q", "--short", "HEAD")

    code, outputs, log = _run(target, "--allowed-paths-file", _allowed(remote, "base-1"), "--create-only")

    assert code == 4
    assert outputs == {}
    assert "base-1" in log
    assert "create-only" in log
    assert _localBranches(target) == ""
    assert _git(target, "symbolic-ref", "-q", "--short", "HEAD") == headBefore


def test_createOnlyFailsWhenTheBaseGainedTheFileAfterTheBotBranchWasPushed(remote):
    branchTip = _pushBranch(remote)
    _addToBase(remote, "Widget.java")
    target = _checkout(remote)

    code, outputs, log = _run(target, "--allowed-paths-file", _allowed(remote, "generated", "Widget.java"),
                              "--create-only")

    assert code == 4, log
    assert outputs == {}
    assert "Widget.java" in log
    assert _localBranches(target) == ""
    assert _git(remote["root"] / "remote.git", "rev-parse", f"refs/heads/{BRANCH}") == branchTip


def test_createOnlyNamesOnlyTheDeclaredPathsThatExist(remote):
    target = _checkout(remote)

    code, _, log = _run(target, "--allowed-paths-file", _allowed(remote, "Missing.java", "base-1", "Other.java"),
                        "--create-only")

    assert code == 4
    assert "base-1" in log
    assert "Missing.java" not in log
    assert "Other.java" not in log


def test_withoutCreateOnlyAnExistingDeclaredPathDoesNotFail(remote):
    target = _checkout(remote)

    code, outputs, log = _run(target, "--allowed-paths-file", _allowed(remote, "base-1"))

    assert code == 0, log
    assert outputs == {"exists": "false", "old": ""}


def test_createOnlyFailsClosedWhenTheBaseTreeCannotBeListed(remote):
    target = _checkout(remote)

    # caminho fora do repositorio faz o ls-tree falhar: erro de verdade nao pode ser lido como "ausente"
    code, outputs, log = _run(target, "--allowed-paths-file", _allowed(remote, "../escape"), "--create-only")

    assert code == 2, log
    assert outputs == {}
    assert _localBranches(target) == ""


def test_createOnlyWithoutAllowedPathsFileIsAnArgumentError(remote):
    target = _checkout(remote)

    code, outputs, log = _run(target, "--create-only")

    assert code == 2
    assert outputs == {}
    assert "--allowed-paths-file" in log
    assert _localBranches(target) == ""
