import os
import sys
import json
import time
import shutil
import hashlib
import subprocess
import importlib.util
from pathlib import Path

import pytest

from conftest import TOOLS_DIR, REPO_ROOT, M2_REPOSITORY, requiresJavaSdk, requiresPythonSdk


def loadMakeReplica():
    spec = importlib.util.spec_from_file_location("make_replica", TOOLS_DIR / "make-replica.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replica = loadMakeReplica()

GSON_ADAPTERS = """\
package com.starkbank.utils;

import com.starkbank.Event;
import com.starkbank.PaymentRequest;
import com.starkbank.Transfer;
import com.starkbank.error.ErrorElement;
import com.starkcore.utils.GsonEvent;

public class GsonAdapters {
    public static synchronized void setup() {
        GsonEvent.registerTypeAdapter(ErrorElement.class, new ErrorElement.Deserializer());
        GsonEvent.registerTypeAdapter(Event.class, new Event.Deserializer());
        GsonEvent.registerTypeAdapter(PaymentRequest.class, new PaymentRequest.Deserializer());
        GsonEvent.registerTypeAdapter(Transfer.Rule.class, new Transfer.Rule.Deserializer());
    }
}
"""

REST = "package com.starkbank.utils;\r\n\r\npublic class Rest {\r\n    int x = 1;\r\n}\r\n"
COMMENT_ONLY_TEST = "// uses Event somewhere\n/** Javadoc: Transfer */\nclass TestKey { String s = \"Event Transfer\"; }\n"
HELPER_TEST = "package utils;\nimport com.starkbank.Event;\npublic class Helper { Event event; }\n"
FAKE_RESOURCES = ["Event", "PaymentRequest", "Transfer"]

MAIN = "src/main/java/com/starkbank"
TEST = "src/test/java"


def writeFile(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def fakeSdk(root):
    writeFile(root, f"{MAIN}/Event.java", "public class Event {}\n")
    writeFile(root, f"{MAIN}/Transfer.java", "public class Transfer { class Rule {} }\n")
    writeFile(root, f"{MAIN}/Balance.java", "public class Balance {}\n")
    writeFile(root, f"{MAIN}/utils/Rest.java", REST)
    writeFile(root, f"{MAIN}/utils/GsonAdapters.java", GSON_ADAPTERS)
    writeFile(root, f"{TEST}/TestEvent.java", "class TestEvent { Event e; }\n")
    writeFile(root, f"{TEST}/TestBalance.java", "class TestBalance { Balance b; }\n")
    writeFile(root, f"{TEST}/TestKey.java", COMMENT_ONLY_TEST)
    writeFile(root, f"{TEST}/utils/Helper.java", HELPER_TEST)
    writeFile(root, f"{TEST}/utils/User.java", "package utils;\npublic class User {}\n")
    writeFile(root, ".git/HEAD", "ref: refs/heads/main\n")
    writeFile(root, "target/junk.class", "x")
    return root


@pytest.fixture
def sdk(tmpPath):
    return fakeSdk(tmpPath / "sdk")


def digestTree(root: Path) -> dict:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def runMain(sdk, out, *extra, resources=FAKE_RESOURCES, runner=None):
    return replica.main(
        ["--out", str(out), *extra],
        runner=runner,
        source=sdk,
        resources=resources,
        sha="abc1234",
    )


def listFiles(root: Path) -> set:
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}


def test_removesMainResourcesAndTheirTests(sdk, tmpPath):
    out = tmpPath / "out"
    assert runMain(sdk, out, "--skip-compile") == 0
    files = listFiles(out)
    assert f"{MAIN}/Event.java" not in files
    assert f"{MAIN}/Transfer.java" not in files
    assert f"{TEST}/TestEvent.java" not in files
    assert f"{MAIN}/Balance.java" in files
    assert f"{TEST}/TestBalance.java" in files


def test_ignoresGitAndTargetWhenCopying(sdk, tmpPath):
    out = tmpPath / "out"
    runMain(sdk, out, "--skip-compile")
    assert not (out / ".git").exists()
    assert not (out / "target").exists()


def test_absentResourceIsNotAnError(sdk, tmpPath, capsys):
    out = tmpPath / "out"
    assert runMain(sdk, out, "--skip-compile", resources=["Event", "Ghost"]) == 0
    assert "1 recurso(s) removido(s)" in capsys.readouterr().out


def test_removesDependentTestInSubpackageByCodeReference(sdk, tmpPath, capsys):
    out = tmpPath / "out"
    runMain(sdk, out, "--skip-compile")
    assert not (out / f"{TEST}/utils/Helper.java").exists()
    assert (out / f"{TEST}/utils/User.java").exists()
    assert "Helper.java" in capsys.readouterr().out


def test_keepsTestThatMentionsResourceOnlyInCommentOrString(sdk, tmpPath):
    out = tmpPath / "out"
    runMain(sdk, out, "--skip-compile")
    assert (out / f"{TEST}/TestKey.java").read_text() == COMMENT_ONLY_TEST


def test_wordBoundaryDoesNotMatchLongerIdentifiers(tmpPath):
    root = fakeSdk(tmpPath / "sdk")
    writeFile(root, f"{TEST}/utils/Other.java", "class Other { GsonEvent g; EventLog l; MyEvent m; }\n")
    out = tmpPath / "out"
    runMain(root, out, "--skip-compile")
    assert (out / f"{TEST}/utils/Other.java").exists()


def test_infrastructureFileIsKeptByteForByte(sdk, tmpPath):
    out = tmpPath / "out"
    runMain(sdk, out, "--skip-compile")
    assert (out / f"{MAIN}/utils/Rest.java").read_bytes() == REST.encode("utf-8")


def test_gsonAdaptersAdaptedKeepsErrorElement(sdk, tmpPath):
    out = tmpPath / "out"
    runMain(sdk, out, "--skip-compile")
    adapted = (out / f"{MAIN}/utils/GsonAdapters.java").read_text()
    assert "ErrorElement" in adapted
    assert "GsonEvent" in adapted
    for name in FAKE_RESOURCES:
        assert f"{name}.class" not in adapted
        assert f"com.starkbank.{name};" not in adapted


def test_adaptGsonAdaptersIsPureAndDropsOnlyRemovedLines():
    adapted = replica.adaptGsonAdapters(source=GSON_ADAPTERS, resources=["Event"])
    assert "Event.class" not in adapted
    assert "import com.starkbank.Event;" not in adapted
    assert "PaymentRequest.class" in adapted
    assert adapted.count("\n") == GSON_ADAPTERS.count("\n") - 2


def test_adaptGsonAdaptersKeepsLineMentioningResourceOnlyInComment():
    source = "import a.B;\n// Event is registered elsewhere\nint x;\n"
    assert replica.adaptGsonAdapters(source=source, resources=["Event"]) == source


MULTILINE_GSON = """\
import com.starkbank.Event;
import com.starkbank.error.ErrorElement;
public class GsonAdapters {
    public static synchronized void setup() {
        GsonEvent.registerTypeAdapter(
            Event.class,
            new Event.Deserializer());
        GsonEvent.registerTypeAdapter(
            ErrorElement.class,
            new ErrorElement.Deserializer());
        initialized = true;
    }
}
"""


def test_adaptGsonAdaptersRemovesMultiLineRegistrationWhole():
    adapted = replica.adaptGsonAdapters(source=MULTILINE_GSON, resources=["Event"])
    assert adapted == (
        "import com.starkbank.error.ErrorElement;\n"
        "public class GsonAdapters {\n"
        "    public static synchronized void setup() {\n"
        "        GsonEvent.registerTypeAdapter(\n"
        "            ErrorElement.class,\n"
        "            new ErrorElement.Deserializer());\n"
        "        initialized = true;\n"
        "    }\n"
        "}\n"
    )


def test_adaptGsonAdaptersKeepsStructureWhenNothingMatches():
    assert replica.adaptGsonAdapters(source=MULTILINE_GSON, resources=["Ghost"]) == MULTILINE_GSON


def test_outSymlinkIsUsageError(sdk, tmpPath, capsys):
    target = tmpPath / "target-dir"
    target.mkdir()
    out = tmpPath / "link"
    out.symlink_to(target)
    assert runMain(sdk, out, "--skip-compile") == 2
    assert "symlink" in capsys.readouterr().out
    assert listFiles(target) == set()


def test_outDanglingSymlinkIsUsageError(sdk, tmpPath):
    out = tmpPath / "link"
    out.symlink_to(tmpPath / "missing")
    assert runMain(sdk, out, "--skip-compile") == 2
    assert not (tmpPath / "missing").exists()


def test_outEqualToSourceIsUsageError(sdk):
    assert runMain(sdk, sdk, "--skip-compile") == 2


def test_outNotEmptyMessageSaysHowToProceed(sdk, tmpPath, capsys):
    out = tmpPath / "out"
    out.mkdir()
    (out / "x.txt").write_text("x")
    runMain(sdk, out, "--skip-compile")
    assert "apague o diretório ou use outro --out" in capsys.readouterr().out


def test_copyFailureIsUsageErrorWithoutTraceback(sdk, tmpPath, capsys, monkeypatch):
    def failingCopy(*args, **kwargs):
        raise PermissionError("negado")

    monkeypatch.setattr(replica.shutil, "copytree", failingCopy)
    assert runMain(sdk, tmpPath / "out", "--skip-compile") == 2
    assert "[ERROR] falha ao montar a base: negado" in capsys.readouterr().out


def test_missingMavenIsUsageError(sdk, tmpPath, capsys, monkeypatch):
    monkeypatch.setattr(replica.shutil, "which", lambda name: None)
    out = tmpPath / "out"
    assert runMain(sdk, out) == 2
    assert "mvn ausente" in capsys.readouterr().out
    assert not out.exists()


def test_runCommandMissingExecutableReturnsTimeoutCodeWithoutRaising(tmpPath):
    code, output = replica.runCommand(command=["comando-que-nao-existe-xyz"], cwd=tmpPath, timeout=5)
    assert code == replica.EXIT_TIMEOUT
    assert "[ERROR]" in output


def test_runCommandInterruptKillsProcessGroupAndReraises(tmpPath, monkeypatch):
    pidFile = tmpPath / "pidfile"
    original = replica.subprocess.Popen.communicate
    calls = []

    def interrupting(self, *args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            return original(self, *args, **kwargs)
        deadline = time.monotonic() + 5
        while not pidFile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
        raise KeyboardInterrupt

    monkeypatch.setattr(replica.subprocess.Popen, "communicate", interrupting)
    with pytest.raises(KeyboardInterrupt):
        replica.runCommand(
            command=["sh", "-c", f"sleep 60 & echo $! > {pidFile}; wait"],
            cwd=tmpPath,
            timeout=30,
        )
    childPid = int(pidFile.read_text().strip())
    deadline = time.monotonic() + 3
    alive = True
    while alive and time.monotonic() < deadline:
        try:
            os.kill(childPid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            alive = False
    assert not alive


def test_remainingMainFileReferencingRemovedResourceFailsClosed(sdk, tmpPath, capsys):
    writeFile(sdk, f"{MAIN}/utils/Uses.java", "class Uses { Event e; }\n")
    out = tmpPath / "out"
    called = []
    code = runMain(sdk, out, runner=lambda command, cwd, timeout: called.append(command) or (0, ""))
    output = capsys.readouterr().out
    assert code == 1
    assert "Uses.java" in output
    assert "Event" in output
    assert called == []


def test_sourceIsNeverWritten(sdk, tmpPath):
    before = digestTree(sdk)
    runMain(sdk, tmpPath / "out", "--skip-compile")
    assert digestTree(sdk) == before


def test_outputIsRealFilesNotSymlinks(sdk, tmpPath):
    out = tmpPath / "out"
    runMain(sdk, out, "--skip-compile")
    assert not any(path.is_symlink() for path in out.rglob("*"))


def test_outNotEmptyIsUsageError(sdk, tmpPath):
    out = tmpPath / "out"
    out.mkdir()
    (out / "x.txt").write_text("x")
    assert runMain(sdk, out, "--skip-compile") == 2
    assert listFiles(out) == {"x.txt"}


def test_emptyExistingOutIsAccepted(sdk, tmpPath):
    out = tmpPath / "out"
    out.mkdir()
    assert runMain(sdk, out, "--skip-compile") == 0


def test_outInsideSourceIsUsageError(sdk):
    assert runMain(sdk, sdk / "replica", "--skip-compile") == 2
    assert not (sdk / "replica").exists()


def test_outContainingSourceIsUsageError(tmpPath):
    outer = tmpPath / "outer"
    inner = fakeSdk(outer / "sdk")
    assert runMain(inner, outer, "--skip-compile") == 2


def test_outParentMissingIsUsageError(sdk, tmpPath):
    assert runMain(sdk, tmpPath / "nope" / "out", "--skip-compile") == 2


def test_invalidSourceIsUsageError(tmpPath, capsys):
    (tmpPath / "empty").mkdir()
    out = tmpPath / "out"
    assert runMain(tmpPath / "empty", out, "--skip-compile") == 2
    assert not out.exists()
    assert "[ERROR]" in capsys.readouterr().out


def test_compilationFailureExitsOneWithLastTwentyLines(sdk, tmpPath, capsys):
    output = "\n".join(f"linha {number}" for number in range(1, 31))
    code = runMain(sdk, tmpPath / "out", runner=lambda command, cwd, timeout: (1, output))
    text = capsys.readouterr().out
    assert code == 1
    assert "linha 30" in text
    assert "linha 11" in text
    assert "linha 10\n" not in text
    assert "base de replicação compila" not in text


def test_compilationRunsMavenInOutWithGivenArguments(sdk, tmpPath, capsys):
    out = tmpPath / "out"
    seen = {}

    def runner(command, cwd, timeout):
        seen.update(command=command, cwd=cwd, timeout=timeout)
        return (0, "")

    assert runMain(sdk, out, "--maven-arg=-o", "--maven-arg=-Dx=1", runner=runner) == 0
    assert seen["command"] == ["mvn", "-q", "-B", "--no-transfer-progress", "-o", "-Dx=1", "test-compile"]
    assert Path(seen["cwd"]) == out
    assert seen["timeout"] == 600
    assert "[OK] base de replicação compila" in capsys.readouterr().out


def test_runCommandTimeoutKillsTheWholeProcessGroup(tmpPath):
    pidFile = tmpPath / "pidfile"
    started = time.monotonic()
    code, output = replica.runCommand(
        command=["sh", "-c", f"sleep 60 & echo $! > {pidFile}; wait"],
        cwd=tmpPath,
        timeout=1,
    )
    elapsed = time.monotonic() - started
    assert code == replica.EXIT_TIMEOUT
    assert elapsed < 10
    childPid = int(pidFile.read_text().strip())
    deadline = time.monotonic() + 3
    alive = True
    while alive and time.monotonic() < deadline:
        try:
            os.kill(childPid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            alive = False
    assert not alive


def test_runCommandReturnsExitCodeAndOutput(tmpPath):
    code, output = replica.runCommand(command=["sh", "-c", "echo oi; exit 3"], cwd=tmpPath, timeout=10)
    assert code == 3
    assert "oi" in output


JAVA_REPO = REPO_ROOT / "_references/sdk-java"
EXPECTED_DEPENDENTS = {"TestTypeAdapter.java", "utils/MerchantPurchase.java", "utils/MerchantSession.java"}


def gitStatus(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True).stdout


@pytest.fixture(scope="module")
def realReplica(tmp_path_factory, request):
    request.getfixturevalue("javaRuntimeClasspath")
    out = tmp_path_factory.mktemp("replica") / "sdk"
    before = gitStatus(JAVA_REPO)
    # sem `-o`: o cache que o CI popula (dependency:build-classpath) tem as dependências mas não os plugins
    # do Maven, e o test-compile offline falha lá por falta de maven-source-plugin
    code = replica.main(
        ["--out", str(out), f"--maven-arg=-Dmaven.repo.local={M2_REPOSITORY}"],
    )
    return {"code": code, "out": out, "before": before}


@requiresJavaSdk
@requiresPythonSdk
def test_realReplicaCompilesWithout42Resources(realReplica):
    assert realReplica["code"] == 0
    names = replica.schemaResources()
    assert len(names) == 42
    out = realReplica["out"]
    assert not any((out / MAIN / f"{name}.java").exists() for name in names)
    assert not any((out / TEST / f"Test{name}.java").exists() for name in names)


@requiresJavaSdk
@requiresPythonSdk
def test_realReplicaRemovesExactlyTheExpectedTestDependents(realReplica):
    out = realReplica["out"]
    names = replica.schemaResources()
    originalTests = {
        str(path.relative_to(JAVA_REPO / TEST))
        for path in (JAVA_REPO / TEST).rglob("*.java")
    }
    assert realReplica["code"] == 0
    removed = originalTests - listFilesRelative(out / TEST)
    dependents = removed - {f"Test{name}.java" for name in names}
    assert dependents == EXPECTED_DEPENDENTS


def listFilesRelative(root: Path) -> set:
    return {str(path.relative_to(root)) for path in root.rglob("*.java")}


@requiresJavaSdk
@requiresPythonSdk
def test_realGsonAdaptersLosesEventPaymentRequestTransfer(realReplica):
    assert realReplica["code"] == 0
    adapted = (realReplica["out"] / MAIN / "utils/GsonAdapters.java").read_text()
    assert "ErrorElement" in adapted
    for name in ("Event", "PaymentRequest", "Transfer"):
        assert f"{name}.class" not in adapted
        assert f"com.starkbank.{name};" not in adapted


@requiresJavaSdk
@requiresPythonSdk
def test_realReferenceStaysUntouched(realReplica):
    assert realReplica["before"] == ""
    assert gitStatus(JAVA_REPO) == ""


@requiresJavaSdk
@requiresPythonSdk
def test_realReplicaListsFortyTwoGaps(realReplica):
    assert realReplica["code"] == 0
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "list-gaps.py"), "--json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "SDK_JAVA": str(realReplica["out"])},
    )
    assert result.returncode == 0
    assert len(json.loads(result.stdout)["gaps"]) == 42
