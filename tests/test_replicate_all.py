import os
import json
import hashlib
import subprocess
import importlib.util
from pathlib import Path

import pytest

from conftest import TOOLS_DIR, REPO_ROOT, requiresGenerator, requiresJavaSdk, requiresPythonSdk


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
    pattern = replicate.referencePattern(FAKE_RESOURCES)
    for path in sorted((out / "src").rglob("*.java")):
        text = path.read_text(encoding="utf-8")
        if "BROKEN" in text:
            return (1, f"[ERROR] {path.name}:[1,1] BROKEN\n[ERROR] COMPILATION ERROR")
        missing = [
            name for name in replicate.referencedNames(text, pattern)
            if name != path.stem and not (out / MAIN / f"{name}.java").is_file()
        ]
        if missing:
            return (1, f"[ERROR] {path.name}:[1,1] cannot find symbol {missing[0]}")
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
    (2, "", "erro", ""),
])
def test_classifyBuildReadsExitCodeAndText(code, output, state, detailPart):
    result = replicate.classifyBuild(code=code, output=output)
    assert result["state"] == state
    assert detailPart in result["detail"]


def test_classifyBuildGateDetailKeepsOnlyTheFirstGaps():
    gaps = "".join(f"    [ERROR] /x/B.java CONTRACT_GAP field ausente: f{number}\n" for number in range(8))
    result = replicate.classifyBuild(code=1, output=gaps + "[ERROR] assert-generated main (exit 1)\n")
    assert result["detail"].count("field ausente") == 3


def test_classifyBuildZeroWithoutBuiltLineIsAnError():
    assert replicate.classifyBuild(code=0, output="")["state"] == "erro"


def test_copyStagingCopiesTheTreeIntoTheTarget(tmpPath):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "a")
    out.mkdir()
    copied, conflict = replicate.copyStaging(staging=staging, out=out)
    assert conflict is None
    assert copied == [out / MAIN / "Alpha.java"]
    assert (out / MAIN / "Alpha.java").read_text() == "a"


def test_copyStagingRefusesToOverwriteAndCopiesNothing(tmpPath):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "new")
    writeFile(staging, f"{MAIN}/Beta.java", "new")
    writeFile(out, f"{MAIN}/Beta.java", "old")
    copied, conflict = replicate.copyStaging(staging=staging, out=out)
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
    assert "BROKEN" in byName["Beta"]["strict"]["detail"]
    assert not (out / MAIN / "Beta.java").exists()
    assert byName["Delta"]["strict"]["state"] == "gerado"
    assert (out / MAIN / "Delta.java").is_file()


def test_compilationFailureWithAbsentDependencyIsDependencyMissingNotCompileError(sdk, tmpPath):
    strict = {**allGenerated(), "Beta": (1, LINT_BLOCKED, {}), "Gamma": generated("Gamma", "class Gamma { Beta beta; }\n")}
    target = tmpPath / "replica.json"
    out = tmpPath / "out"
    runner, _ = fakeRunner(strict)
    runMain(sdk, out, "--json", str(target), runner=runner)
    _, byName = entries(target)
    assert byName["Gamma"]["strict"]["state"] == "dependência ausente"
    assert byName["Gamma"]["strict"]["detail"] == "Beta; mvn: [ERROR] Gamma.java:[1,1] cannot find symbol Beta"
    assert byName["Gamma"]["strict"]["compiled"] is False
    assert not (out / MAIN / "Gamma.java").exists()
    assert byName["Alpha"]["strict"]["state"] == "gerado"


def test_compileErrorDetailKeepsAtMostFiveMavenErrorLines(sdk, tmpPath):
    noisy = "\n".join(f"[ERROR] linha {number}" for number in range(9))
    errors = replicate.mavenErrors(noisy)
    assert errors == [f"[ERROR] linha {number}" for number in range(5)]


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
    for state, count in report["byState"].items():
        assert f"{state}" in text and str(count) in text
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
    assert "ciclo de dependência (ou dependente de ciclo) entre recursos: " in text and "Alpha" in text and "Beta" in text
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
    code = replicate.main(["--out", "rel/out", "--skip-compile"], runner=runner, source=sdk, resources=FAKE_RESOURCES, sha="abc1234")
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


def test_copyStagingFailureMidwayRemovesWhatWasCopiedAndReportsAnError(sdk, tmpPath, monkeypatch):
    staging, out = tmpPath / "staging", tmpPath / "out"
    writeFile(staging, f"{MAIN}/Alpha.java", "a")
    writeFile(staging, f"{MAIN}/Beta.java", "b")
    out.mkdir()
    realCopy = replicate.shutil.copyfile
    count = []

    def flaky(source, target):
        count.append(source)
        if len(count) == 2:
            raise OSError("disco cheio")
        return realCopy(source, target)

    monkeypatch.setattr(replicate.shutil, "copyfile", flaky)
    copied, problem = replicate.copyStaging(staging=staging, out=out)
    assert copied == []
    assert "disco cheio" in problem
    assert not any(path.is_file() for path in out.rglob("*"))
    count.clear()
    state = replicate.placeAndCompile(staging, [], {"out": out, "skipCompile": True})
    assert state["state"] == "erro"


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


JAVA_REPO = REPO_ROOT / "_references/sdk-java"
M2 = Path.home() / ".m2/repository"


def gitStatus(path):
    return subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True).stdout


@requiresJavaSdk
@requiresPythonSdk
def test_realTopologicalOrderPutsEveryResourceAfterAllItsDependencies():
    names = replicate.schemaResources()
    dependencies = replicate.dependenciesOf(source=JAVA_REPO, resources=names)
    order, stuck = replicate.topologicalOrder(dependencies=dependencies, requested=names)
    assert stuck == []
    assert sorted(order) == sorted(names)
    position = {name: index for index, name in enumerate(order)}
    for name, deps in dependencies.items():
        assert all(position[dep] < position[name] for dep in deps)
    assert {"Boleto", "BoletoHolmes", "Transfer"} <= set(dependencies["Event"])
    assert dependencies["VerifiedTransfer"] == ["Transfer"]
    assert dependencies["CorporateRule"] == ["CardMethod", "MerchantCategory", "MerchantCountry"]
    assert sorted(name for name, deps in dependencies.items() if deps) == [
        "CorporateCard", "CorporateHolder", "CorporateRule", "Event", "Invoice", "PaymentRequest", "VerifiedTransfer",
    ]
    assert len(order) == 42


@pytest.fixture(scope="module")
def realReplication(tmp_path_factory, request):
    request.getfixturevalue("javaRuntimeClasspath")
    out = tmp_path_factory.mktemp("replicated") / "sdk"
    report = tmp_path_factory.mktemp("report") / "replica.json"
    before = gitStatus(JAVA_REPO)
    code = replicate.main([
        "--out", str(out),
        "--json", str(report),
        f"--maven-arg=-Dmaven.repo.local={M2}",
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
        ["mvn", "-q", "-B", "--no-transfer-progress", f"-Dmaven.repo.local={M2}", "test-compile"],
        capture_output=True, text=True, cwd=realReplication["out"],
    )
    assert result.returncode == 0, result.stdout[-2000:]
