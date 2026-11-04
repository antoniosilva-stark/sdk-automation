import os
import sys
import json
import signal
import hashlib
import subprocess
import importlib.util
from pathlib import Path

import pytest

from conftest import (
    TOOLS_DIR,
    REPO_ROOT,
    M2_REPOSITORY,
    runWithClosedStdout,
    requiresGenerator,
    requiresJavaSdk,
    requiresPythonSdk,
)


def loadReplicateAll():
    spec = importlib.util.spec_from_file_location("replicate_all", TOOLS_DIR / "replicate-all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replicate = loadReplicateAll()

MAIN = "src/main/java/com/starkbank"
FAKE_RESOURCES = ["Alpha", "Beta", "Gamma", "Delta"]

GSON_REAL = """\
package com.starkbank.utils;

import com.starkbank.Alpha;
import com.starkbank.Beta;
import com.starkcore.utils.GsonEvent;

public class GsonAdapters {
    public static synchronized void setup() {
        GsonEvent.registerTypeAdapter(Alpha.class, new Alpha.Deserializer());
        GsonEvent.registerTypeAdapter(Beta.class, new Beta.Deserializer());
    }
}
"""
GSON_BROKEN = GSON_REAL.replace("new Beta.Deserializer());", "new Beta.Deserializer(), BROKEN);")

LINT_BLOCKED = """\
[INFO] Gamma (java, 1 run(s)) → /tmp/into
    [ERROR] apis/spec-v2.openapi.yaml:3411 NO_ID Gamma: schema sem 'id': o template emite 'extends Resource'
    [ERROR] 1 problema(s) bloqueando a geração
[ERROR] lint-spec (exit 1)
"""
UNSUPPORTED = """\
[OK] lint-spec (exit 0)
[INFO] Delta não existe no sdk-java, classificado pelo list-gaps como lacuna canônica
[ERROR] formato nao suportado: enum sem id
"""
GATE_FAILS = """\
[OK] lint-spec (exit 0)
    [ERROR] /tmp/b/main/src/main/java/com/starkbank/Beta.java CONTRACT_GAP constructor ausente: public Beta(String id)
    [ERROR] /tmp/b/main/src/main/java/com/starkbank/Beta.java CONTRACT_GAP field ausente: public String name;
    [ERROR] 2 gap(s) de contrato em modo --strict
[ERROR] assert-generated main (exit 1)
"""
NO_ARTIFACT = "[OK] lint-spec (exit 0)\n[ERROR] gerador main retornou 0 mas não produziu o artefato: Beta.java\n"
ALREADY_EXISTS = "[INFO] Beta já existe no sdk-java — relatório de paridade, nada será entregue\n"
TIMED_OUT = "[ERROR] /usr/bin/python3 excedeu 600s"
MAVEN_HEADER = "[ERROR] COMPILATION ERROR : \n"


def writeFile(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def fakeSdk(root):
    writeFile(root, f"{MAIN}/Alpha.java", "public class Alpha {}\n")
    writeFile(root, f"{MAIN}/Beta.java", "public class Beta { Alpha alpha; /* Gamma */ String s = \"Gamma\"; }\n")
    writeFile(root, f"{MAIN}/Gamma.java", "public class Gamma { Beta beta; Alpha alpha; Gamma self; }\n")
    writeFile(root, f"{MAIN}/Delta.java", "// Alpha\npublic class Delta {}\n")
    writeFile(root, f"{MAIN}/Balance.java", "public class Balance {}\n")
    writeFile(root, f"{MAIN}/utils/GsonAdapters.java", GSON_REAL)
    writeFile(root, ".git/HEAD", "ref: refs/heads/main\n")
    return root


@pytest.fixture
def sdk(tmpPath):
    return fakeSdk(tmpPath / "sdk")


def generated(name, body=None):
    output = f"[OK] lint-spec (exit 0)\n[OK] {name} construído e verificado em /tmp/staging\n"
    return (0, output, {f"{MAIN}/{name}.java": body or f"public class {name} {{}}\n"})


def allGenerated():
    return {name: generated(name) for name in FAKE_RESOURCES}


def fakeCompile(out):
    """Imita o javac: símbolo ausente ou o token BROKEN reprovam, como no Maven real."""
    replica = replicate.loadTool("make-replica")
    pattern = replica.referencePattern(FAKE_RESOURCES)
    for path in sorted((out / "src").rglob("*.java")):
        text = path.read_text(encoding="utf-8")
        if "BROKEN" in text:
            return (1, f"{MAVEN_HEADER}[ERROR] /tmp/x/{MAIN}/{path.name}:[1,1] BROKEN\n[ERROR] -> [Help 1]")
        missing = [
            name for name in replica.referencedNames(text, pattern)
            if name != path.stem and not (out / MAIN / f"{name}.java").is_file()
        ]
        if missing:
            return (1, (
                f"{MAVEN_HEADER}[ERROR] /tmp/x/{MAIN}/{path.name}:[1,1] cannot find symbol\n"
                f"[ERROR]   symbol:   class {missing[0]}\n[ERROR] -> [Help 1]"
            ))
    return (0, "")


def fakeRunner(strict, advisory=None):
    calls = []

    def run(command, cwd, timeout, env=None):
        calls.append({"command": command, "cwd": cwd, "timeout": timeout, "env": env})
        if command[0] == "mvn":
            return fakeCompile(Path(cwd))
        table = advisory if "--advisory" in command and advisory is not None else strict
        code, output, files = table[command[2]]
        staging = Path(command[command.index("--into") + 1])
        for relative, content in files.items():
            writeFile(staging, relative, content)
        return (code, output)

    return run, calls


def buildCalls(calls):
    return [call for call in calls if call["command"][0] != "mvn"]


def mavenCalls(calls):
    return [call for call in calls if call["command"][0] == "mvn"]


def runMain(sdk, out, *extra, runner, resources=FAKE_RESOURCES):
    return replicate.main(
        ["--out", str(out), *extra],
        runner=runner,
        source=sdk,
        resources=resources,
        sha="abc1234",
    )


def digestTree(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def entries(jsonPath):
    report = json.loads(Path(jsonPath).read_text(encoding="utf-8"))
    return report, {entry["resource"]: entry for entry in report["resources"]}


def test_topologicalOrderPutsDependentAfterDependencyWithAlphabeticalTies():
    dependencies = {"Alpha": [], "Beta": ["Alpha"], "Gamma": ["Alpha", "Beta"], "Delta": []}
    order, stuck = replicate.topologicalOrder(dependencies=dependencies, requested=list(dependencies))
    assert order == ["Alpha", "Beta", "Delta", "Gamma"]
    assert stuck == []


def test_topologicalOrderIgnoresDependenciesOutsideTheRequestedSet():
    dependencies = {"Alpha": [], "Beta": ["Alpha"], "Gamma": ["Beta"]}
    order, stuck = replicate.topologicalOrder(dependencies=dependencies, requested=["Gamma", "Beta"])
    assert order == ["Beta", "Gamma"]
    assert stuck == []


def test_topologicalOrderReportsTheNodesOfACycle():
    dependencies = {"Alpha": ["Beta"], "Beta": ["Alpha"], "Delta": []}
    order, stuck = replicate.topologicalOrder(dependencies=dependencies, requested=list(dependencies))
    assert order == ["Delta"]
    assert stuck == ["Alpha", "Beta"]


def test_dependenciesOfKeepsOnlyCodeReferencesToOtherResources(sdk):
    dependencies = replicate.dependenciesOf(source=sdk, resources=FAKE_RESOURCES + ["Ghost"])
    assert dependencies["Beta"] == ["Alpha"]
    assert dependencies["Gamma"] == ["Alpha", "Beta"]
    assert dependencies["Delta"] == []
    assert dependencies["Ghost"] == []


@pytest.mark.parametrize("code, output, state, detailPart", [
    (0, generated("Beta")[1], "gerado", ""),
    (1, LINT_BLOCKED, "barrado pelo lint-spec", "NO_ID"),
    (1, UNSUPPORTED, "formato nao suportado", "enum sem id"),
    (1, GATE_FAILS, "reprovado no gate", "constructor ausente: public Beta(String id)"),
    (4, ALREADY_EXISTS, "existe", ""),
    (1, NO_ARTIFACT, "erro", "não produziu o artefato"),
    (124, TIMED_OUT, "erro", "excedeu 600s"),
    (2, "", "erro", "sem saída"),
])
def test_classifyBuildReadsExitCodeAndText(code, output, state, detailPart):
    result = replicate.classifyBuild(code=code, output=output)
    assert result["state"] == state
    assert detailPart in result["detail"]


def test_classifyBuildGateDetailKeepsOnlyTheFirstGaps():
    gaps = "".join(f"    [ERROR] /x/B.java CONTRACT_GAP field ausente: f{number}\n" for number in range(8))
    result = replicate.classifyBuild(code=1, output=gaps + "[ERROR] assert-generated main (exit 1)\n")
    assert result["detail"].count("field ausente") == 3


def test_classifyBuildChildThatDiesWithATracebackShowsItsLastLineAsTheCause():
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "tools/build-resource.py", line 9, in <module>\n'
        "    raise ValueError(\"boom\")\n"
        "ValueError: boom\n\n"
    )
    result = replicate.classifyBuild(code=1, output=traceback)
    assert result["state"] == "erro"
    assert result["detail"] == "ValueError: boom"


def test_classifyBuildKeepsTheLastErrorLineWhenThereAreErrorLines():
    result = replicate.classifyBuild(code=1, output="[ERROR] primeira\n[ERROR] ultima\ncauda\n")
    assert result["detail"] == "[ERROR] ultima"


def test_classifyBuildZeroWithoutBuiltLineIsAnError():
    assert replicate.classifyBuild(code=0, output="")["state"] == "erro"


def test_copyStagingCopiesTheTreeIntoTheTarget(tmpPath):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "a")
    out.mkdir()
    copied = []
    assert replicate.copyStaging(staging=staging, out=out, copied=copied) is None
    assert copied == [out / MAIN / "Alpha.java"]
    assert (out / MAIN / "Alpha.java").read_text() == "a"


def test_copyStagingRefusesToOverwriteAndCopiesNothing(tmpPath):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "new")
    writeFile(staging, f"{MAIN}/Beta.java", "new")
    writeFile(out, f"{MAIN}/Beta.java", "old")
    copied = []
    conflict = replicate.copyStaging(staging=staging, out=out, copied=copied)
    assert copied == []
    assert "Beta.java" in conflict
    assert (out / MAIN / "Beta.java").read_text() == "old"
    assert not (out / MAIN / "Alpha.java").exists()


def test_replicatesEveryResourceInTopologicalOrderInsideTheTarget(sdk, tmpPath, capsys):
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    report, byName = entries(target)
    assert [entry["resource"] for entry in report["resources"]] == ["Alpha", "Beta", "Delta", "Gamma"]
    assert [entry["order"] for entry in report["resources"]] == [1, 2, 3, 4]
    assert all(entry["strict"] == {"state": "gerado", "detail": "", "compiled": True} for entry in byName.values())
    assert byName["Gamma"]["deps"] == ["Alpha", "Beta"]
    assert [call["command"][2] for call in buildCalls(calls)] == ["Alpha", "Beta", "Delta", "Gamma"]
    assert all((out / MAIN / f"{name}.java").is_file() for name in FAKE_RESOURCES)
    assert "4 de 4 gerados e compilando (strict), +0 via advisory" in capsys.readouterr().out


def test_buildRunsLikeTheCiWithoutAdvisoryIntoAFreshStagingThatIsRemoved(sdk, tmpPath):
    out = tmpPath / "out"
    runner, calls = fakeRunner(allGenerated())
    runMain(sdk, out, runner=runner)
    stagings = {call["command"][call["command"].index("--into") + 1] for call in buildCalls(calls)}
    assert len(stagings) == 4
    assert not any(Path(staging).exists() for staging in stagings)
    for call in buildCalls(calls):
        command = call["command"]
        assert command[3:5] == ["--lang", "java"]
        assert "--advisory" not in command
        assert call["timeout"] == 600
        assert Path(call["cwd"]) == replicate.REPO_ROOT


def test_sdkJavaGoesOnlyToTheChildAndNeverToTheProcessEnvironment(sdk, tmpPath, monkeypatch):
    monkeypatch.setenv("SDK_JAVA", "/valor/original")
    out = tmpPath / "out"
    runner, calls = fakeRunner(allGenerated())
    runMain(sdk, out, runner=runner)
    assert os.environ["SDK_JAVA"] == "/valor/original"
    assert all(call["env"] == {"SDK_JAVA": str(out)} for call in buildCalls(calls))
    assert all(call["env"] is None for call in mavenCalls(calls))


def test_mavenRunsInTheTargetWithTheGivenArgumentsWithoutOffline(sdk, tmpPath):
    out = tmpPath / "out"
    runner, calls = fakeRunner(allGenerated())
    runMain(sdk, out, "--maven-arg=-Dx=1", runner=runner)
    assert all(call["command"] == ["mvn", "-q", "-B", "--no-transfer-progress", "-Dx=1", "test-compile"]
               for call in mavenCalls(calls))
    assert all(Path(call["cwd"]) == out for call in mavenCalls(calls))


def test_everyBuildStateIsReportedFromItsForgedOutput(sdk, tmpPath):
    strict = {
        "Alpha": generated("Alpha"),
        "Beta": (1, GATE_FAILS, {}),
        "Gamma": (1, LINT_BLOCKED, {}),
        "Delta": (4, ALREADY_EXISTS, {}),
    }
    target = tmpPath / "replica.json"
    runner, _ = fakeRunner(strict)
    assert runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner) == 0
    report, byName = entries(target)
    assert byName["Alpha"]["strict"]["state"] == "gerado"
    assert byName["Beta"]["strict"]["state"] == "reprovado no gate"
    assert byName["Gamma"]["strict"] == {"state": "barrado pelo lint-spec", "detail": "NO_ID", "compiled": None}
    assert byName["Delta"]["strict"]["state"] == "existe"
    assert report["byState"] == {
        "gerado": 1, "barrado pelo lint-spec": 1, "existe": 1, "reprovado no gate": 1,
    }


def test_existingTargetFileIsNeverOverwritten(sdk, tmpPath):
    clash = (0, generated("Alpha")[1], {f"{MAIN}/Balance.java": "public class Balance { int x; }\n"})
    target = tmpPath / "replica.json"
    out = tmpPath / "out"
    runner, _ = fakeRunner({**allGenerated(), "Alpha": clash})
    runMain(sdk, out, "--json", str(target), runner=runner)
    _, byName = entries(target)
    assert byName["Alpha"]["strict"]["state"] == "erro"
    assert "arquivo já existe" in byName["Alpha"]["strict"]["detail"]
    assert (out / MAIN / "Balance.java").read_text() == "public class Balance {}\n"


def test_compilationFailureRemovesTheNewFilesAndTheNextResourcesKeepGoing(sdk, tmpPath):
    strict = {**allGenerated(), "Beta": generated("Beta", "public class Beta { Alpha alpha; BROKEN }\n")}
    target = tmpPath / "replica.json"
    out = tmpPath / "out"
    runner, _ = fakeRunner(strict)
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    _, byName = entries(target)
    assert byName["Beta"]["strict"]["state"] == "erro de compilação"
    assert byName["Beta"]["strict"]["compiled"] is False
    assert "Beta.java:[1,1] BROKEN" in byName["Beta"]["strict"]["detail"]
    assert "COMPILATION ERROR" not in byName["Beta"]["strict"]["detail"]
    assert not (out / MAIN / "Beta.java").exists()
    assert byName["Delta"]["strict"]["state"] == "gerado"
    assert (out / MAIN / "Delta.java").is_file()


def test_compilationFailureWithAbsentDependencyIsDependencyMissingNotCompileError(sdk, tmpPath):
    strict = {
        **allGenerated(),
        "Beta": (1, LINT_BLOCKED, {}),
        "Gamma": generated("Gamma", "class Gamma { Beta beta; }\n"),
    }
    target = tmpPath / "replica.json"
    out = tmpPath / "out"
    runner, _ = fakeRunner(strict)
    runMain(sdk, out, "--json", str(target), runner=runner)
    _, byName = entries(target)
    assert byName["Gamma"]["strict"]["state"] == "dependência ausente"
    detail = byName["Gamma"]["strict"]["detail"]
    assert detail.startswith("Beta; mvn: ")
    assert "Gamma.java:[1,1] cannot find symbol" in detail
    assert "COMPILATION ERROR" not in detail
    assert byName["Gamma"]["strict"]["compiled"] is False
    assert not (out / MAIN / "Gamma.java").exists()
    assert byName["Alpha"]["strict"]["state"] == "gerado"


def test_compileErrorDetailKeepsAtMostFiveMavenErrorLines(sdk, tmpPath):
    noisy = "\n".join(f"[ERROR] linha {number}" for number in range(9))
    errors = replicate.mavenErrors(noisy, Path("/tmp/x"))
    assert errors == [f"[ERROR] linha {number}" for number in range(5)]


def test_mavenErrorsDropsTheCompilationHeaderSoTheCauseComesFirst():
    output = (
        "[ERROR] COMPILATION ERROR : \n"
        "[ERROR] /tmp/x/src/main/java/com/starkbank/Gamma.java:[1,1] cannot find symbol\n"
        "[ERROR]   symbol:   class Beta\n"
        "[ERROR] -> [Help 1]\n"
    )
    errors = replicate.mavenErrors(output, Path("/tmp/x"))
    assert errors[0].endswith("Gamma.java:[1,1] cannot find symbol")
    assert len(errors) == 3


def test_mavenErrorsKeepsFiveLinesAfterTheHeader():
    noisy = MAVEN_HEADER + "\n".join(f"[ERROR] linha {number}" for number in range(9))
    assert replicate.mavenErrors(noisy, Path("/tmp/x")) == [f"[ERROR] linha {number}" for number in range(5)]


def test_advisoryPassRunsOnlyForUnsupportedFormatAndKeepsTheFileInTheTarget(sdk, tmpPath):
    strict = {**allGenerated(), "Delta": (1, UNSUPPORTED, {}), "Gamma": (1, LINT_BLOCKED, {})}
    advisory = {"Delta": generated("Delta")}
    target = tmpPath / "replica.json"
    out = tmpPath / "out"
    runner, calls = fakeRunner(strict, advisory)
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    report, byName = entries(target)
    advisoryCalls = [call["command"][2] for call in buildCalls(calls) if "--advisory" in call["command"]]
    assert advisoryCalls == ["Delta"]
    assert byName["Delta"]["strict"]["state"] == "formato nao suportado"
    assert byName["Delta"]["advisory"] == {"state": "gerado", "detail": "", "compiled": True}
    assert byName["Alpha"]["advisory"] is None
    assert byName["Gamma"]["advisory"] is None
    assert (out / MAIN / "Delta.java").is_file()
    assert report["generated"] == 2
    assert report["advisoryGenerated"] == 1


def test_advisoryCompileFailureRemovesTheFilesAndReportsItInTheAdvisoryColumn(sdk, tmpPath):
    strict = {**allGenerated(), "Delta": (1, UNSUPPORTED, {})}
    advisory = {"Delta": generated("Delta", "public class Delta { BROKEN }\n")}
    target = tmpPath / "replica.json"
    out = tmpPath / "out"
    runner, _ = fakeRunner(strict, advisory)
    runMain(sdk, out, "--json", str(target), runner=runner)
    _, byName = entries(target)
    assert byName["Delta"]["strict"]["state"] == "formato nao suportado"
    assert byName["Delta"]["advisory"]["state"] == "erro de compilação"
    assert not (out / MAIN / "Delta.java").exists()


def test_integrationSwapsTheAdaptedGsonAdaptersForTheRealOneWhenEverythingIsPresent(sdk, tmpPath):
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    runMain(sdk, out, "--json", str(target), runner=runner)
    report, _ = entries(target)
    assert report["integration"]["state"] == "ok"
    assert (out / MAIN / "utils/GsonAdapters.java").read_text() == GSON_REAL


def test_integrationFailureRestoresTheAdaptedGsonAdapters(sdk, tmpPath):
    writeFile(sdk, f"{MAIN}/utils/GsonAdapters.java", GSON_BROKEN)
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    report, _ = entries(target)
    adapted = replicate.loadTool("make-replica").adaptGsonAdapters(source=GSON_BROKEN, resources=FAKE_RESOURCES)
    assert report["integration"]["state"] == "falhou"
    assert "BROKEN" in report["integration"]["detail"]
    assert (out / MAIN / "utils/GsonAdapters.java").read_text() == adapted


def test_integrationIsSkippedListingTheMissingResources(sdk, tmpPath, capsys):
    strict = {**allGenerated(), "Beta": (1, LINT_BLOCKED, {})}
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, _ = fakeRunner(strict)
    runMain(sdk, out, "--json", str(target), runner=runner)
    report, _ = entries(target)
    assert report["integration"] == {"state": "pulada", "detail": "faltam Beta"}
    assert "pulada: faltam Beta" in capsys.readouterr().out
    assert "Beta.class" not in (out / MAIN / "utils/GsonAdapters.java").read_text()


def test_skipCompileNeverRunsMavenNorRemovesAndLeavesCompiledNull(sdk, tmpPath, capsys):
    strict = {**allGenerated(), "Beta": generated("Beta", "public class Beta { BROKEN }\n")}
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, calls = fakeRunner(strict)
    assert runMain(sdk, out, "--skip-compile", "--json", str(target), runner=runner) == 0
    report, byName = entries(target)
    assert mavenCalls(calls) == []
    assert byName["Beta"]["strict"] == {"state": "gerado", "detail": "", "compiled": None}
    assert (out / MAIN / "Beta.java").is_file()
    assert report["integration"] == {"state": "pulada", "detail": "(--skip-compile)"}
    text = capsys.readouterr().out
    assert "pulada (--skip-compile)" in text
    assert "4 de 4 gerados (compilação pulada) (strict), +0 via advisory" in text
    assert "compilando" not in text


def test_repeatedResourceIsBuiltOnce(sdk, tmpPath):
    out = tmpPath / "out"
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, out, "Alpha", "Alpha", runner=runner) == 0
    assert [call["command"][2] for call in buildCalls(calls)] == ["Alpha"]


def test_subsetReplicatesOnlyTheRequestedButTheBaseRemovesEveryResource(sdk, tmpPath):
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, out, "Alpha", "--json", str(target), runner=runner) == 0
    report, _ = entries(target)
    assert [call["command"][2] for call in buildCalls(calls)] == ["Alpha"]
    assert (out / MAIN / "Alpha.java").is_file()
    assert not any((out / MAIN / f"{name}.java").exists() for name in ("Beta", "Gamma", "Delta"))
    assert (out / MAIN / "Balance.java").is_file()
    assert report["integration"]["state"] == "pulada"
    assert report["integration"]["detail"] == "faltam Beta"


def test_textReportMatchesTheJson(sdk, tmpPath, capsys):
    strict = {**allGenerated(), "Delta": (1, UNSUPPORTED, {}), "Gamma": (1, LINT_BLOCKED, {})}
    target = tmpPath / "replica.json"
    runner, _ = fakeRunner(strict, {"Delta": generated("Delta")})
    runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner)
    text = capsys.readouterr().out
    report, _ = entries(target)
    assert report["sha"] == "abc1234"
    assert report["out"] == str(tmpPath / "out")
    assert "[INFO] replica sdk-java @ abc1234" in text
    assert "2 de 4 gerados e compilando (strict), +1 via advisory" in text
    assert "barrado pelo lint-spec (NO_ID)" in text
    table = text[text.index("[INFO] 2 de 4"):]
    positions = [table.index(f" {entry['resource']} ") for entry in report["resources"]]
    assert positions == sorted(positions)
    assert report["byState"] == {"gerado": 2, "barrado pelo lint-spec": 1, "formato nao suportado": 1}
    section = text[text.index("[INFO] por estado (strict):"):].split("\n\n")[0].splitlines()[1:]
    assert len(section) == len(report["byState"])
    for line, (state, count) in zip(section, report["byState"].items()):
        assert line.split() == [*state.split(), str(count)]
    assert report["integration"]["state"] == "ok"


def test_sourceIsNeverWritten(sdk, tmpPath):
    before = digestTree(sdk)
    runner, _ = fakeRunner(allGenerated())
    runMain(sdk, tmpPath / "out", runner=runner)
    assert digestTree(sdk) == before


def test_baseFailureExitsOneBeforeBuildingAnything(sdk, tmpPath, capsys):
    runner, calls = fakeRunner(allGenerated())

    def failingBase(command, cwd, timeout, env=None):
        calls.append({"command": command})
        return (1, "[ERROR] boom")

    assert runMain(sdk, tmpPath / "out", runner=failingBase) == 1
    assert "[ERROR] base de replicação falhou" in capsys.readouterr().out
    assert buildCalls(calls) == []


@pytest.mark.parametrize("prepare", ["notEmpty", "symlink", "insideSource", "containsSource", "noParent"])
def test_invalidOutIsUsageErrorBeforeAnyWork(sdk, tmpPath, prepare):
    out = tmpPath / "out"
    if prepare == "notEmpty":
        writeFile(out, "x.txt", "x")
    if prepare == "symlink":
        (tmpPath / "real").mkdir()
        out.symlink_to(tmpPath / "real")
    if prepare == "insideSource":
        out = sdk / "replica"
    if prepare == "containsSource":
        out = tmpPath
    if prepare == "noParent":
        out = tmpPath / "ausente" / "out"
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, out, runner=runner) == 2
    assert calls == []


def test_invalidSourceIsUsageError(tmpPath, capsys):
    (tmpPath / "vazio").mkdir()
    runner, calls = fakeRunner(allGenerated())
    assert runMain(tmpPath / "vazio", tmpPath / "out", runner=runner) == 2
    assert "[ERROR]" in capsys.readouterr().out
    assert calls == []


def test_jsonWithoutParentDirectoryIsUsageErrorBeforeAnyWork(sdk, tmpPath, capsys):
    runner, calls = fakeRunner(allGenerated())
    code = runMain(sdk, tmpPath / "out", "--json", str(tmpPath / "ausente" / "r.json"), runner=runner)
    assert code == 2
    assert "--json" in capsys.readouterr().out
    assert calls == []
    assert not (tmpPath / "out").exists()


def test_unknownResourceIsUsageError(sdk, tmpPath, capsys):
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, tmpPath / "out", "Nope", runner=runner) == 2
    assert "Nope" in capsys.readouterr().out
    assert calls == []


def test_dependencyCycleIsUsageErrorListingTheNodes(sdk, tmpPath, capsys):
    writeFile(sdk, f"{MAIN}/Alpha.java", "public class Alpha { Beta beta; }\n")
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, tmpPath / "out", runner=runner) == 2
    text = capsys.readouterr().out
    assert "ciclo de dependência (ou dependente de ciclo) entre recursos: " in text
    assert "Alpha" in text and "Beta" in text
    assert calls == []


def test_missingMavenOrNpxIsUsageErrorBeforeAnyWork(sdk, tmpPath, capsys, monkeypatch):
    out = tmpPath / "out"
    monkeypatch.setattr(replicate.shutil, "which", lambda name: None if name == "mvn" else "/bin/x")
    assert runMain(sdk, out, runner=None) == 2
    assert "mvn ausente" in capsys.readouterr().out
    monkeypatch.setattr(replicate.shutil, "which", lambda name: None if name == "npx" else "/bin/x")
    assert runMain(sdk, out, runner=None) == 2
    assert "npx ausente" in capsys.readouterr().out
    assert not out.exists()


def test_relativeOutFromAnotherCwdReachesTheChildAsTheResolvedAbsolutePath(sdk, tmpPath, monkeypatch):
    work = tmpPath / "work"
    (work / "rel").mkdir(parents=True)
    monkeypatch.chdir(work)
    runner, calls = fakeRunner(allGenerated())
    code = replicate.main(
        ["--out", "rel/out", "--skip-compile"],
        runner=runner,
        source=sdk,
        resources=FAKE_RESOURCES,
        sha="abc1234",
    )
    assert code == 0
    expected = (work / "rel/out").resolve()
    assert all(call["env"] == {"SDK_JAVA": str(expected)} for call in buildCalls(calls))
    assert Path(buildCalls(calls)[0]["env"]["SDK_JAVA"]).is_absolute()
    assert (expected / MAIN / "Alpha.java").is_file()


def test_relativeJsonIsResolvedBeforeTheWorkEvenIfTheCwdChanges(sdk, tmpPath, monkeypatch):
    work, elsewhere = tmpPath / "work", tmpPath / "elsewhere"
    work.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(work)
    runner, _ = fakeRunner(allGenerated())

    def wandering(command, cwd, timeout, env=None):
        os.chdir(elsewhere)
        return runner(command, cwd, timeout, env)

    code = replicate.main(["--out", str(tmpPath / "out"), "--skip-compile", "--json", "r.json"],
                          runner=wandering, source=sdk, resources=FAKE_RESOURCES, sha="abc1234")
    assert code == 0
    assert (work / "r.json").is_file()
    assert not (elsewhere / "r.json").exists()


def twoFileStaging(tmpPath):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "a")
    writeFile(staging, f"{MAIN}/Beta.java", "b")
    out.mkdir()
    return staging, out


def filesIn(root):
    return [path for path in root.rglob("*") if path.is_file()]


def test_copyStagingFailureMidwayRemovesWhatWasCopiedAndReportsAnError(tmpPath, monkeypatch):
    staging, out = twoFileStaging(tmpPath)
    realCopy = replicate.shutil.copyfile
    count = []

    def flaky(source, target):
        count.append(source)
        if len(count) == 2:
            raise OSError("disco cheio")
        return realCopy(source, target)

    monkeypatch.setattr(replicate.shutil, "copyfile", flaky)
    state = replicate.placeAndCompile(staging, [], {"out": out, "skipCompile": True})
    assert state["state"] == "erro"
    assert "disco cheio" in state["detail"]
    assert filesIn(out) == []


def test_copyStagingFailureLeavesNoPartialFileInTheTarget(tmpPath, monkeypatch):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "abcdef")
    out.mkdir()

    def halfThenFail(source, target):
        Path(target).write_text("abc")
        raise OSError("disco cheio")

    monkeypatch.setattr(replicate.shutil, "copyfile", halfThenFail)
    state = replicate.placeAndCompile(staging, [], {"out": out, "skipCompile": True})
    assert "disco cheio" in state["detail"]
    assert filesIn(out) == []


@pytest.mark.parametrize("skipCompile", [True, False])
def test_interruptBetweenTwoCopiesLeavesNothingInTheTargetAndRaises(tmpPath, monkeypatch, skipCompile):
    staging, out = twoFileStaging(tmpPath)
    writeFile(out, f"{MAIN}/Balance.java", "old")
    realCopy = replicate.shutil.copyfile
    count = []

    def interruptedOnSecond(source, target):
        count.append(source)
        if len(count) == 2:
            raise KeyboardInterrupt
        return realCopy(source, target)

    monkeypatch.setattr(replicate.shutil, "copyfile", interruptedOnSecond)
    config = {"out": out, "skipCompile": skipCompile, "mavenArguments": [], "runner": fakeRunner(allGenerated())[0]}
    with pytest.raises(KeyboardInterrupt):
        replicate.placeAndCompile(staging, [], config)
    assert filesIn(out) == [out / MAIN / "Balance.java"]


def test_interruptRightAfterTheCopyReturnsLeavesNothingInTheTargetAndRaises(tmpPath, monkeypatch):
    staging, out = twoFileStaging(tmpPath)
    realCopyStaging = replicate.copyStaging

    def interruptedAfterReturn(staging, out, copied):
        realCopyStaging(staging=staging, out=out, copied=copied)
        raise KeyboardInterrupt

    monkeypatch.setattr(replicate, "copyStaging", interruptedAfterReturn)
    config = {"out": out, "skipCompile": False, "mavenArguments": [], "runner": fakeRunner(allGenerated())[0]}
    with pytest.raises(KeyboardInterrupt):
        replicate.placeAndCompile(staging, [], config)
    assert filesIn(out) == []


def test_interruptDuringTheCompileRemovesTheCopiedFilesAndRaises(tmpPath):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "a")
    writeFile(out, f"{MAIN}/Balance.java", "old")

    def interrupting(command, cwd, timeout, env=None):
        raise KeyboardInterrupt

    config = {"out": out, "skipCompile": False, "mavenArguments": [], "runner": interrupting}
    with pytest.raises(KeyboardInterrupt):
        replicate.placeAndCompile(staging, [], config)
    assert not (out / MAIN / "Alpha.java").exists()
    assert (out / MAIN / "Balance.java").read_text() == "old"


def failingUnlinkOf(monkeypatch, blocked):
    realUnlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self == blocked:
            raise PermissionError("negado")
        return realUnlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def test_removeAllTriesEveryPathAndReturnsTheFailuresWithoutRaising(tmpPath, monkeypatch):
    paths = [tmpPath / name for name in ("a", "b", "c")]
    for path in paths:
        path.write_text("x")
    failingUnlinkOf(monkeypatch, paths[0])
    assert replicate.removeAll(paths) == [f"{paths[0]}: negado"]
    assert [path.exists() for path in paths] == [True, False, False]


def test_compileFailureWithIncompleteRollbackIsErrorNamingWhatWasLeftAndTheCause(tmpPath, monkeypatch, capsys):
    staging, out = twoFileStaging(tmpPath)
    left = out / MAIN / "Alpha.java"
    failingUnlinkOf(monkeypatch, left)

    def broken(command, cwd, timeout, env=None):
        return (1, f"{MAVEN_HEADER}[ERROR] /tmp/x/{MAIN}/Beta.java:[1,1] BROKEN\n")

    config = {"out": out, "skipCompile": False, "mavenArguments": [], "runner": broken}
    state = replicate.placeAndCompile(staging, [], config)
    assert state["state"] == "erro"
    assert state["compiled"] is False
    assert state["detail"].splitlines()[0] == f"rollback incompleto: {left}: negado"
    assert "erro de compilação" in state["detail"]
    assert "BROKEN" in state["detail"]
    assert filesIn(out) == [left]
    assert f"[ERROR] falha ao remover do alvo o arquivo copiado {left}: negado" in capsys.readouterr().out


def test_interruptWithIncompleteRollbackStillRaisesTheInterruptAndWarnsOnTheConsole(tmpPath, monkeypatch, capsys):
    staging, out = twoFileStaging(tmpPath)
    left = out / MAIN / "Alpha.java"
    failingUnlinkOf(monkeypatch, left)

    def interrupting(command, cwd, timeout, env=None):
        raise KeyboardInterrupt

    config = {"out": out, "skipCompile": False, "mavenArguments": [], "runner": interrupting}
    with pytest.raises(KeyboardInterrupt):
        replicate.placeAndCompile(staging, [], config)
    assert filesIn(out) == [left]
    assert f"[ERROR] falha ao remover do alvo o arquivo copiado {left}: negado" in capsys.readouterr().out


def test_unexpectedErrorInOneResourceBecomesErrorStateAndTheNextOnesAreMeasured(sdk, tmpPath):
    runner, _ = fakeRunner(allGenerated())

    def flaky(command, cwd, timeout, env=None):
        if command[0] != "mvn" and command[2] == "Beta":
            raise OSError("sem processo")
        return runner(command, cwd, timeout, env)

    target = tmpPath / "replica.json"
    assert runMain(sdk, tmpPath / "out", "--json", str(target), runner=flaky) == 0
    _, byName = entries(target)
    assert byName["Beta"]["strict"]["state"] == "erro"
    assert "sem processo" in byName["Beta"]["strict"]["detail"]
    assert byName["Delta"]["strict"]["state"] == "gerado"


def test_interruptInABuildIsNotSwallowedByThePerResourceHandling(sdk, tmpPath):
    def interrupting(command, cwd, timeout, env=None):
        if command[0] != "mvn" and command[2] == "Beta":
            raise KeyboardInterrupt
        return fakeRunner(allGenerated())[0](command, cwd, timeout, env)

    with pytest.raises(KeyboardInterrupt):
        runMain(sdk, tmpPath / "out", runner=interrupting)


def test_timeoutOfOneResourceBecomesErrorWithTheMessage(sdk, tmpPath):
    target = tmpPath / "replica.json"
    runner, _ = fakeRunner({**allGenerated(), "Alpha": (124, TIMED_OUT, {})})
    assert runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner) == 0
    _, byName = entries(target)
    assert byName["Alpha"]["strict"]["state"] == "erro"
    assert "excedeu 600s" in byName["Alpha"]["strict"]["detail"]


def test_invalidFromIsUsageError(tmpPath, capsys):
    (tmpPath / "vazio").mkdir()
    runner, calls = fakeRunner(allGenerated())
    code = replicate.main(
        ["--out", str(tmpPath / "out"), "--from", str(tmpPath / "vazio")],
        runner=runner,
        resources=FAKE_RESOURCES,
        sha="abc1234",
    )
    assert code == 2
    assert "[ERROR]" in capsys.readouterr().out
    assert calls == []


@pytest.mark.parametrize("place", ["insideOut", "insideSource"])
def test_jsonInsideOutOrSourceIsUsageErrorBeforeAnyWork(sdk, tmpPath, capsys, place):
    out = tmpPath / "out"
    target = out / "r.json" if place == "insideOut" else sdk / "r.json"
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 2
    text = capsys.readouterr().out
    assert "--json" in text and "dentro" in text
    assert calls == []
    assert not out.exists()
    assert not (sdk / "r.json").exists()


def test_jsonThatExistsAndIsNotWritableIsUsageErrorBeforeAnyWork(sdk, tmpPath, capsys, monkeypatch):
    target = tmpPath / "r.json"
    target.write_text("{}")
    realAccess = replicate.os.access
    monkeypatch.setattr(replicate.os, "access", lambda path, mode: Path(path) != target and realAccess(path, mode))
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner) == 2
    assert "--json" in capsys.readouterr().out
    assert calls == []


def test_jsonPointingToADirectoryIsUsageErrorBeforeAnyWork(sdk, tmpPath, capsys):
    (tmpPath / "dir").mkdir()
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, tmpPath / "out", "--json", str(tmpPath / "dir"), runner=runner) == 2
    assert "--json" in capsys.readouterr().out
    assert calls == []


def test_jsonInAnUnwritableDirectoryIsUsageErrorBeforeAnyWork(sdk, tmpPath, capsys, monkeypatch):
    monkeypatch.setattr(replicate.os, "access", lambda path, mode: False)
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, tmpPath / "out", "--json", str(tmpPath / "r.json"), runner=runner) == 2
    assert "--json" in capsys.readouterr().out
    assert calls == []


def test_jsonWriteFailureIsReportedWithoutTracebackAndExitsTwo(sdk, tmpPath, capsys):
    target = tmpPath / "r.json"
    runner, _ = fakeRunner(allGenerated())

    def sabotage(command, cwd, timeout, env=None):
        target.mkdir(exist_ok=True)
        return runner(command, cwd, timeout, env)

    code = runMain(sdk, tmpPath / "out", "--skip-compile", "--json", str(target), runner=sabotage)
    text = capsys.readouterr().out
    assert code == 2
    assert "[ERROR] falha ao gravar --json: " in text
    assert "replica sdk-java @ abc1234" in text


def test_integrationRestoresTheAdaptedGsonAdaptersWhenTheCompileIsInterrupted(sdk, tmpPath):
    out = tmpPath / "out"
    runner, _ = fakeRunner(allGenerated())

    def interrupting(command, cwd, timeout, env=None):
        if command[0] == "mvn" and (Path(cwd) / MAIN / "utils/GsonAdapters.java").read_text() == GSON_REAL:
            raise KeyboardInterrupt
        return runner(command, cwd, timeout, env)

    with pytest.raises(KeyboardInterrupt):
        runMain(sdk, out, runner=interrupting)
    adapted = replicate.loadTool("make-replica").adaptGsonAdapters(source=GSON_REAL, resources=FAKE_RESOURCES)
    assert (out / MAIN / "utils/GsonAdapters.java").read_text() == adapted


def relativizingMaven(runner):
    """O Maven real imprime o caminho absoluto do arquivo, que contém o --out."""
    def run(command, cwd, timeout, env=None):
        code, output = runner(command, cwd, timeout, env)
        return (code, output.replace("/tmp/x", str(cwd)) if command[0] == "mvn" else output)

    return run


def longOut(tmpPath):
    parent = tmpPath / ("d" * 110)
    parent.mkdir()
    return parent / "out"


def test_compileDetailRelativizesTheMavenPathsToOutSoTheCauseSurvivesTheTableWidth(sdk, tmpPath, capsys):
    strict = {**allGenerated(), "Beta": generated("Beta", "public class Beta { Alpha alpha; BROKEN }\n")}
    out, target = longOut(tmpPath), tmpPath / "replica.json"
    runner, _ = fakeRunner(strict)
    assert runMain(sdk, out, "--json", str(target), runner=relativizingMaven(runner)) == 0
    _, byName = entries(target)
    entry = byName["Beta"]
    assert len(str(out)) > replicate.DETAIL_WIDTH
    assert entry["strict"]["detail"].splitlines()[0] == f"[ERROR] {MAIN}/Beta.java:[1,1] BROKEN"
    assert str(out) not in entry["strict"]["detail"]
    assert "BROKEN" in replicate.shortDetail(entry)
    assert f"{MAIN}/Beta.java:[1,1] BROKEN" in capsys.readouterr().out


def test_dependencyMissingDetailKeepsTheCauseInTheTableWithALongOut(sdk, tmpPath, capsys):
    strict = {
        **allGenerated(),
        "Beta": (1, LINT_BLOCKED, {}),
        "Gamma": generated("Gamma", "class Gamma { Beta beta; }\n"),
    }
    out, target = longOut(tmpPath), tmpPath / "replica.json"
    runner, _ = fakeRunner(strict)
    runMain(sdk, out, "--json", str(target), runner=relativizingMaven(runner))
    _, byName = entries(target)
    entry = byName["Gamma"]
    assert entry["strict"]["state"] == "dependência ausente"
    assert entry["strict"]["detail"].startswith(f"Beta; mvn: [ERROR] {MAIN}/Gamma.java:[1,1] cannot find symbol")
    assert str(out) not in entry["strict"]["detail"]
    assert "cannot find symbol" in replicate.shortDetail(entry)
    assert "cannot find symbol" in capsys.readouterr().out


def test_mavenErrorsStripsTheOutPrefixFromEveryLineIncludingTheFallback():
    out = Path("/work/replica")
    assert replicate.mavenErrors("[ERROR] /work/replica/src/A.java:[1,2] x\n", out) == ["[ERROR] src/A.java:[1,2] x"]
    assert replicate.mavenErrors("falhou em /work/replica/pom.xml\n", out) == ["falhou em pom.xml"]


def test_mavenErrorsNeverCutsAnOutThatOnlyAppearsInsideAnotherPath():
    out = Path("/work/replica")
    line = "[ERROR] /home/u/work/replica/src/A.java:[1,2] x"
    assert replicate.mavenErrors(f"{line}\n", out) == [line]
    fallback = "falhou em /home/u/work/replica/pom.xml"
    assert replicate.mavenErrors(f"{fallback}\n", out) == [fallback]


@pytest.mark.parametrize("place", ["intoSource", "intoOut"])
def test_jsonSymlinkIntoOutOrSourceIsUsageErrorAndNothingIsWrittenThroughIt(sdk, tmpPath, capsys, place):
    out = tmpPath / "out"
    out.mkdir()
    destination = sdk / "r.json" if place == "intoSource" else out / "r.json"
    link = tmpPath / "link.json"
    link.symlink_to(destination)
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, out, "--json", str(link), runner=runner) == 2
    assert "--json" in capsys.readouterr().out
    assert calls == []
    assert not destination.exists()
    assert list(out.iterdir()) == []


def test_jsonSymlinkLeafToAnywhereElseIsAlsoRejected(sdk, tmpPath, capsys):
    (tmpPath / "elsewhere.json").write_text("{}")
    link = tmpPath / "link.json"
    link.symlink_to(tmpPath / "elsewhere.json")
    runner, calls = fakeRunner(allGenerated())
    assert runMain(sdk, tmpPath / "out", "--json", str(link), runner=runner) == 2
    text = capsys.readouterr().out
    assert "--json" in text and "symlink" in text
    assert calls == []
    assert (tmpPath / "elsewhere.json").read_text() == "{}"


def adaptedGsonAdapters():
    return replicate.loadTool("make-replica").adaptGsonAdapters(source=GSON_REAL, resources=FAKE_RESOURCES)


def failingWriteOf(monkeypatch, content, error):
    """Falha a escrita de um conteúdo específico, depois de truncar o arquivo como o open('wb') faria."""
    realWrite = Path.write_bytes

    def write(self, data):
        if data != content:
            return realWrite(self, data)
        realWrite(self, data[:5])
        raise error

    monkeypatch.setattr(Path, "write_bytes", write)


def test_integrationWriteFailureOfTheRealGsonAdaptersIsFalhouAndRestoresTheAdapted(sdk, tmpPath, monkeypatch):
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    failingWriteOf(monkeypatch, GSON_REAL.encode("utf-8"), OSError("disco cheio"))
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    report, _ = entries(target)
    assert report["integration"]["state"] == "falhou"
    assert "disco cheio" in report["integration"]["detail"]
    assert (out / MAIN / "utils/GsonAdapters.java").read_text() == adaptedGsonAdapters()


def test_integrationInterruptRightAfterTheRealWriteRestoresTheAdaptedAndRaises(sdk, tmpPath, monkeypatch):
    out = tmpPath / "out"
    runner, _ = fakeRunner(allGenerated())
    realWrite = Path.write_bytes

    def interruptedAfterWrite(self, data):
        realWrite(self, data)
        if data == GSON_REAL.encode("utf-8"):
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "write_bytes", interruptedAfterWrite)
    with pytest.raises(KeyboardInterrupt):
        runMain(sdk, out, runner=runner)
    assert (out / MAIN / "utils/GsonAdapters.java").read_text() == adaptedGsonAdapters()


def test_integrationReadFailureOfTheRealSourceBeforeTheSwapIsFalhouNotATraceback(sdk, tmpPath, monkeypatch):
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    realRead = Path.read_bytes
    real = sdk / MAIN / "utils/GsonAdapters.java"

    def read(self):
        if self == real and (out / MAIN / "Alpha.java").exists():
            raise OSError("leitura negada")
        return realRead(self)

    monkeypatch.setattr(Path, "read_bytes", read)
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    report, _ = entries(target)
    assert report["integration"]["state"] == "falhou"
    assert "leitura negada" in report["integration"]["detail"]
    assert (out / MAIN / "utils/GsonAdapters.java").read_text() == adaptedGsonAdapters()


def test_integrationRestoreFailureIsReportedOnTheConsoleInsteadOfATraceback(sdk, tmpPath, monkeypatch, capsys):
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    realWrite = Path.write_bytes

    def write(self, data):
        if self.name == "GsonAdapters.java" and (out / MAIN / "Alpha.java").exists():
            raise OSError("disco cheio")
        return realWrite(self, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    report, _ = entries(target)
    assert report["integration"]["state"] == "falhou"
    assert "[ERROR] falha ao restaurar o GsonAdapters adaptado" in capsys.readouterr().out


def failingSwapRead(monkeypatch, out, sdk, failing):
    """Falha só a leitura feita na troca: a do adaptado no alvo, ou a do real depois dela (o readText vem antes)."""
    realRead = Path.read_bytes
    adaptedPath, realPath = out / MAIN / "utils/GsonAdapters.java", sdk / MAIN / "utils/GsonAdapters.java"
    reads = []

    def read(self):
        if (out / MAIN / "Alpha.java").exists() and self == adaptedPath:
            reads.append(self)
        if self == {"adapted": adaptedPath, "real": realPath}[failing] and reads:
            raise OSError("leitura negada")
        return realRead(self)

    monkeypatch.setattr(Path, "read_bytes", read)
    return reads


@pytest.mark.parametrize("failing", ["adapted", "real"])
def test_integrationReadFailureInsideTheSwapIsFalhouAndKeepsTheAdapted(sdk, tmpPath, monkeypatch, capsys, failing):
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    reads = failingSwapRead(monkeypatch, out, sdk, failing)
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    report, _ = entries(target)
    assert reads == [out / MAIN / "utils/GsonAdapters.java"]
    assert report["integration"] == {"state": "falhou", "detail": "OSError: leitura negada"}
    assert "Traceback" not in capsys.readouterr().out
    assert (out / MAIN / "utils/GsonAdapters.java").read_text() == adaptedGsonAdapters()


def test_integrationInterruptInTheMiddleOfTheRealWriteRestoresTheAdaptedAndRaises(sdk, tmpPath, monkeypatch):
    out = tmpPath / "out"
    runner, _ = fakeRunner(allGenerated())
    failingWriteOf(monkeypatch, GSON_REAL.encode("utf-8"), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        runMain(sdk, out, runner=runner)
    assert (out / MAIN / "utils/GsonAdapters.java").read_text() == adaptedGsonAdapters()


def failingRestoreOf(monkeypatch, out, blocked):
    """Recusa as escritas listadas no GsonAdapters do alvo depois que a base já foi montada (Alpha copiado)."""
    realWrite = Path.write_bytes

    def write(self, data):
        if self.name == "GsonAdapters.java" and (out / MAIN / "Alpha.java").exists() and data in blocked:
            raise OSError("disco cheio")
        return realWrite(self, data)

    monkeypatch.setattr(Path, "write_bytes", write)


@pytest.mark.parametrize("cause", ["compileFails", "writeFails"])
def test_integrationRestoreFailureReachesTheDetailInTheTextAndTheJson(sdk, tmpPath, monkeypatch, capsys, cause):
    real = GSON_BROKEN if cause == "compileFails" else GSON_REAL
    writeFile(sdk, f"{MAIN}/utils/GsonAdapters.java", real)
    adapted = replicate.loadTool("make-replica").adaptGsonAdapters(source=real, resources=FAKE_RESOURCES)
    blocked = [adapted.encode("utf-8")] + ([] if cause == "compileFails" else [real.encode("utf-8")])
    out, target = tmpPath / "out", tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    failingRestoreOf(monkeypatch, out, blocked)
    assert runMain(sdk, out, "--json", str(target), runner=runner) == 0
    report, _ = entries(target)
    warning = "ATENÇÃO: falha ao restaurar o GsonAdapters adaptado: disco cheio"
    assert report["integration"]["state"] == "falhou"
    assert report["integration"]["detail"].splitlines()[-1].startswith(warning)
    assert ("BROKEN" if cause == "compileFails" else "OSError: disco cheio") in report["integration"]["detail"]
    assert f"    {warning}" in capsys.readouterr().out


@pytest.mark.parametrize("interruptedIn", ["write", "compile"])
def test_integrationInterruptWithAFailingRestoreRaisesTheInterruptAndWarnsOnTheConsole(
    sdk,
    tmpPath,
    monkeypatch,
    capsys,
    interruptedIn,
):
    out = tmpPath / "out"
    runner, _ = fakeRunner(allGenerated())

    def interrupting(command, cwd, timeout, env=None):
        if command[0] == "mvn" and (Path(cwd) / MAIN / "utils/GsonAdapters.java").read_text() == GSON_REAL:
            raise KeyboardInterrupt
        return runner(command, cwd, timeout, env)

    if interruptedIn == "write":
        failingWriteOf(monkeypatch, GSON_REAL.encode("utf-8"), KeyboardInterrupt())
    failingRestoreOf(monkeypatch, out, [adaptedGsonAdapters().encode("utf-8")])
    with pytest.raises(KeyboardInterrupt):
        runMain(sdk, out, runner=interrupting)
    assert "[ERROR] falha ao restaurar o GsonAdapters adaptado" in capsys.readouterr().out


@pytest.fixture
def originalTerminationHandler():
    original = signal.getsignal(signal.SIGTERM)
    yield original
    signal.signal(signal.SIGTERM, original)


def test_installTerminationHandlerTurnsSigtermIntoItsOwnKeyboardInterrupt(originalTerminationHandler):
    replicate.installTerminationHandler()
    handler = signal.getsignal(signal.SIGTERM)
    assert handler is not originalTerminationHandler
    assert issubclass(replicate.TerminatedBySignal, KeyboardInterrupt)
    with pytest.raises(replicate.TerminatedBySignal):
        handler(signal.SIGTERM, None)


def test_runAsProgramExitsWith143WhenTheTerminationReachesTheTop(monkeypatch):
    def terminated():
        raise replicate.TerminatedBySignal

    monkeypatch.setattr(replicate, "main", terminated)
    assert replicate.runAsProgram() == 143


def test_runAsProgramLetsARealCtrlCPropagate(monkeypatch):
    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(replicate, "main", interrupted)
    with pytest.raises(KeyboardInterrupt) as raised:
        replicate.runAsProgram()
    assert type(raised.value) is KeyboardInterrupt


def test_realSigtermDuringTheCompileRemovesTheCopiedFilesAndRaises(tmpPath, originalTerminationHandler):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "a")
    writeFile(out, f"{MAIN}/Balance.java", "old")

    def terminated(command, cwd, timeout, env=None):
        os.kill(os.getpid(), signal.SIGTERM)
        return (0, "")

    replicate.installTerminationHandler()
    config = {"out": out, "skipCompile": False, "mavenArguments": [], "runner": terminated}
    with pytest.raises(KeyboardInterrupt):
        replicate.placeAndCompile(staging, [], config)
    assert filesIn(out) == [out / MAIN / "Balance.java"]


def test_onlyRunningAsAProgramInstallsTheTerminationHandler():
    # subprocesso: o bloco __main__ não pode tocar o handler do processo do pytest
    script = "\n".join([
        "import sys, runpy, signal",
        f"path = {str(TOOLS_DIR / 'replicate-all.py')!r}",
        "runpy.run_path(path, run_name='replicate_all')",
        "imported = signal.getsignal(signal.SIGTERM) is signal.SIG_DFL",
        "sys.argv = [path, '--help']",
        "try:",
        "    runpy.run_path(path, run_name='__main__')",
        "except SystemExit:",
        "    pass",
        "print(f'imported={imported} program={signal.getsignal(signal.SIGTERM) is signal.SIG_DFL}')",
    ])
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "imported=True program=False"


def test_stagingCreationFailureOfOneResourceBecomesErrorAndTheNextOnesAreMeasured(sdk, tmpPath, monkeypatch):
    realMkdtemp = replicate.tempfile.mkdtemp
    attempts = []

    def flaky(suffix=None, prefix=None, dir=None):
        attempts.append(prefix)
        if len(attempts) == 1:
            raise OSError("disco cheio")
        return realMkdtemp(suffix, prefix, dir)

    monkeypatch.setattr(replicate.tempfile, "mkdtemp", flaky)
    target = tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    assert runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner) == 0
    _, byName = entries(target)
    assert byName["Alpha"]["strict"]["state"] == "erro"
    assert "disco cheio" in byName["Alpha"]["strict"]["detail"]
    assert byName["Delta"]["strict"]["state"] == "gerado"


JAVA_REPO = REPO_ROOT / "_references/sdk-java"


def gitStatus(path):
    return subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True).stdout


@requiresJavaSdk
@requiresPythonSdk
def test_realTopologicalOrderPutsEveryResourceAfterAllItsDependencies():
    names = replicate.loadTool("parity-report").schemaResources()
    dependencies = replicate.dependenciesOf(source=JAVA_REPO, resources=names)
    order, stuck = replicate.topologicalOrder(dependencies=dependencies, requested=names)
    assert stuck == []
    assert len(names) == 42
    assert len(order) == len(set(order)) == len(names)
    assert sorted(order) == sorted(names)
    position = {name: index for index, name in enumerate(order)}
    for name, deps in dependencies.items():
        assert all(position[dep] < position[name] for dep in deps)
    assert {"Boleto", "BoletoHolmes", "Invoice", "Transfer"} <= set(dependencies["Event"])
    assert "Transfer" in dependencies["VerifiedTransfer"]
    assert "CorporateRule" in dependencies["CorporateCard"]
    assert replicate.topologicalOrder(dependencies=dependencies, requested=names) == (order, stuck)


@pytest.fixture(scope="module")
def realReplication(tmp_path_factory, request):
    request.getfixturevalue("javaRuntimeClasspath")
    out = tmp_path_factory.mktemp("replicated") / "sdk"
    report = tmp_path_factory.mktemp("report") / "replica.json"
    before = gitStatus(JAVA_REPO)
    code = replicate.main([
        "--out", str(out),
        "--json", str(report),
        f"--maven-arg=-Dmaven.repo.local={M2_REPOSITORY}",
        "Transaction", "DictKey", "SplitProfile",
    ])
    return {"code": code, "out": out, "report": report, "before": before}


@requiresGenerator
@requiresJavaSdk
@requiresPythonSdk
def test_realReplicationOfThreeIndependentResources(realReplication):
    assert realReplication["code"] == 0
    report, byName = entries(realReplication["report"])
    assert set(byName) == {"Transaction", "DictKey", "SplitProfile"}
    for name, entry in byName.items():
        assert entry["strict"]["state"] == "gerado", (name, entry)
        assert entry["strict"]["compiled"] is True
        assert (realReplication["out"] / MAIN / f"{name}.java").is_file()
    assert report["generated"] == 3


@requiresGenerator
@requiresJavaSdk
@requiresPythonSdk
def test_realReplicationTargetCompilesAndReferenceStaysUntouched(realReplication):
    assert realReplication["code"] == 0
    assert realReplication["before"] == ""
    assert gitStatus(JAVA_REPO) == ""
    result = subprocess.run(
        ["mvn", "-q", "-B", "--no-transfer-progress", f"-Dmaven.repo.local={M2_REPOSITORY}", "test-compile"],
        capture_output=True, text=True, cwd=realReplication["out"],
    )
    assert result.returncode == 0, result.stdout[-2000:]


def test_textShowsTheAdvisoryCauseWhenTheSecondPassFails(sdk, tmpPath, capsys):
    strict = {**allGenerated(), "Delta": (1, UNSUPPORTED, {})}
    advisory = {"Delta": generated("Delta", "public class Delta { BROKEN }\n")}
    target = tmpPath / "replica.json"
    runner, _ = fakeRunner(strict, advisory)
    runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner)
    text = capsys.readouterr().out
    _, byName = entries(target)
    cause = byName["Delta"]["advisory"]["detail"].splitlines()[0]
    assert cause
    line = next(line for line in text.splitlines() if line.startswith("  ") and " Delta " in line)
    assert f"advisory: {cause}"[:replicate.DETAIL_WIDTH] in line


def test_textKeepsTheStrictDetailWhenTheAdvisoryGenerated(sdk, tmpPath, capsys):
    strict = {**allGenerated(), "Delta": (1, UNSUPPORTED, {})}
    target = tmpPath / "replica.json"
    runner, _ = fakeRunner(strict, {"Delta": generated("Delta")})
    runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner)
    text = capsys.readouterr().out
    _, byName = entries(target)
    line = next(line for line in text.splitlines() if line.startswith("  ") and " Delta " in line)
    assert "advisory:" not in line
    assert byName["Delta"]["strict"]["detail"].splitlines()[0][:replicate.DETAIL_WIDTH] in line


def test_emitFlushesSoProgressReachesAPipeImmediately(monkeypatch):
    class Recorder:
        def __init__(self):
            self.flushCount = 0
            self.written = []

        def write(self, text):
            self.written.append(text)

        def flush(self):
            self.flushCount += 1

    recorder = Recorder()
    monkeypatch.setattr(sys, "stdout", recorder)
    replicate.emit("[INFO] [1/2] Alpha: gerado")
    assert recorder.written == ["[INFO] [1/2] Alpha: gerado\n"]
    assert recorder.flushCount == 1


@pytest.mark.parametrize("target, variable, directory", [
    ("replicate", "REPLICATED_DIR", "tools/replicate-all.py"),
    ("replica", "REPLICA_DIR", "tools/make-replica.py"),
])
def test_makeCreatesTheParentOfOutButNotOutItself(target, variable, directory):
    result = subprocess.run(
        ["make", "-n", target, f"{variable}=/tmp/x/y/z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    mkdirs = [line for line in lines if line.startswith("mkdir")]
    assert mkdirs == ["mkdir -p /tmp/x/y/"]
    assert lines.index(mkdirs[0]) < next(i for i, line in enumerate(lines) if directory in line)


@pytest.mark.parametrize("target, variable", [("replicate", "REPLICATED_DIR"), ("replica", "REPLICA_DIR")])
def test_makeParentOfABareOutIsTheCurrentDirectory(target, variable):
    result = subprocess.run(
        ["make", "-n", target, f"{variable}=bare"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert "mkdir -p ./" in result.stdout.splitlines()


def unsupportedEntry(strictDetail, advisory):
    return {
        "resource": "Delta",
        "order": 1,
        "strict": {"state": "formato nao suportado", "detail": strictDetail, "compiled": None},
        "advisory": advisory,
    }


def test_shortDetailShowsTheStrictCauseAndTheAdvisoryCauseWhenTheAdvisoryFails(sdk, tmpPath, capsys):
    strict = {**allGenerated(), "Delta": (1, UNSUPPORTED, {})}
    advisory = {"Delta": generated("Delta", "public class Delta { BROKEN }\n")}
    target = tmpPath / "replica.json"
    runner, _ = fakeRunner(strict, advisory)
    runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner)
    _, byName = entries(target)
    strictCause = byName["Delta"]["strict"]["detail"].splitlines()[0]
    advisoryCause = byName["Delta"]["advisory"]["detail"].splitlines()[0]
    detail = replicate.shortDetail(byName["Delta"])
    assert strictCause[:30] in detail
    assert f"advisory: {advisoryCause[:20]}" in detail
    assert strictCause in byName["Delta"]["strict"]["detail"]
    assert advisoryCause in byName["Delta"]["advisory"]["detail"]
    assert detail in capsys.readouterr().out


def test_shortDetailKeepsTheAdvisoryPrefixAndFitsTheWidthWithLongCauses():
    advisory = {"state": "erro de compilação", "detail": "B" * 300, "compiled": False}
    detail = replicate.shortDetail(unsupportedEntry("A" * 300, advisory))
    assert len(detail) <= replicate.DETAIL_WIDTH
    assert "advisory: B" in detail
    assert detail.startswith("AAAA")


def test_shortDetailKeepsTheWholeShortAdvisoryCauseWhenTheStrictOneIsLong():
    advisory = {"state": "erro de compilação", "detail": "boom", "compiled": False}
    detail = replicate.shortDetail(unsupportedEntry("A" * 300, advisory))
    assert len(detail) <= replicate.DETAIL_WIDTH
    assert detail.endswith("advisory: boom")


@pytest.mark.parametrize("advisory", [
    None,
    {"state": "gerado", "detail": "", "compiled": True},
    {"state": "erro de compilação", "detail": "", "compiled": False},
])
def test_shortDetailWithoutAnAdvisoryCauseShowsOnlyTheStrictCause(advisory):
    assert replicate.shortDetail(unsupportedEntry("create de objeto unico", advisory)) == "create de objeto unico"


def test_renderDoesNotRepeatTheAdvisoryCauseAlreadyInItsBlockedLabel():
    advisory = {"state": "barrado pelo lint-spec", "detail": "NO_ID", "compiled": None}
    entry = unsupportedEntry("get sem id", advisory)
    report = {
        "sha": "abc",
        "skipCompile": False,
        "generated": 0,
        "total": 1,
        "advisoryGenerated": 0,
        "byState": {"formato nao suportado": 1},
        "resources": [entry],
        "integration": {"state": "ok", "detail": ""},
    }
    line = [row for row in replicate.render(report).splitlines() if " Delta " in row][0]
    assert line.count("NO_ID") == 1
    assert "get sem id" in line


class BrokenStdout:

    def __init__(self, brokenWrite=False, brokenFlush=False, error=BrokenPipeError):
        self.brokenWrite = brokenWrite
        self.brokenFlush = brokenFlush
        self.error = error

    def write(self, text):
        if self.brokenWrite:
            raise self.error("write")
        return len(text)

    def flush(self):
        if self.brokenFlush:
            raise self.error("flush")


def test_emitSurvivesABrokenPipeOnWrite(monkeypatch):
    monkeypatch.setattr(sys, "stdout", BrokenStdout(brokenWrite=True))
    replicate.emit("x")


def test_emitSurvivesABrokenPipeOnFlush(monkeypatch):
    monkeypatch.setattr(sys, "stdout", BrokenStdout(brokenFlush=True))
    replicate.emit("x")


def test_emitPointsABrokenStdoutAtDevnullSoLaterWritesAndTheFinalFlushSucceed(monkeypatch):
    reader, writer = os.pipe()
    os.close(reader)
    stdout = open(writer, "w")
    monkeypatch.setattr(sys, "stdout", stdout)
    replicate.emit("x")
    replicate.emit("y")
    assert os.path.samestat(os.fstat(writer), os.stat(os.devnull))
    stdout.close()


@pytest.mark.parametrize("where", ["brokenWrite", "brokenFlush"])
def test_emitDoesNotSwallowOtherOsErrors(monkeypatch, where):
    monkeypatch.setattr(sys, "stdout", BrokenStdout(**{where: True, "error": PermissionError}))
    with pytest.raises(PermissionError):
        replicate.emit("x")


def test_measurementFinishesAndWritesTheJsonWhenTheStdoutPipeIsClosed(sdk, tmpPath, monkeypatch):
    monkeypatch.setattr(sys, "stdout", BrokenStdout(brokenWrite=True))
    target = tmpPath / "replica.json"
    runner, _ = fakeRunner(allGenerated())
    assert runMain(sdk, tmpPath / "out", "--json", str(target), runner=runner) == 0
    report, _ = entries(target)
    assert report["generated"] == 4


@requiresGenerator
@requiresJavaSdk
@requiresPythonSdk
@pytest.mark.parametrize("environment", [{}, {"PYTHONUNBUFFERED": "1"}])
def test_programWithTheStdoutReaderClosedExitsWithTheRealCodeAndWritesTheJson(tmpPath, environment):
    # antes do redirecionamento para o devnull: exit 120 (buffered) ou traceback do emit da base (unbuffered)
    target = tmpPath / "replica.json"
    arguments = [
        str(TOOLS_DIR / "replicate-all.py"),
        "--out", str(tmpPath / "out"),
        "--skip-compile",
        "--json", str(target),
        "DictKey",
    ]
    result = runWithClosedStdout(arguments, environment)
    assert result.returncode == 0, result.stderr
    assert "BrokenPipeError" not in result.stderr
    _, byName = entries(target)
    assert set(byName) == {"DictKey"}


def test_programUsageErrorWithTheStdoutReaderClosedStillExitsWith2(sdk, tmpPath):
    # antes do redirecionamento para o devnull: exit 120, e o CI perdia o código de erro de uso
    out = tmpPath / "out"
    writeFile(out, "junk.txt", "x")
    result = runWithClosedStdout([str(TOOLS_DIR / "replicate-all.py"), "--out", str(out), "--from", str(sdk)], {})
    assert result.returncode == 2, result.stderr
    assert "BrokenPipeError" not in result.stderr
