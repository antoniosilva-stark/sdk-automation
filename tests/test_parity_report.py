import json
import importlib.util

from conftest import TOOLS_DIR

# Saídas reais do build-resource --advisory (--lang java), com o ruído do gerador e o caminho temporário cortados.
SPLIT_PROFILE_PASSES = """\
[INFO] SplitProfile (java, 1 run(s)) → /tmp/into
    [OK] os 1 recurso(s) exigido(s) são geráveis
[OK] lint-spec (exit 0)
[INFO] régua derivada de sdk-java @ f60902a
[OK] generate main (java) (exit 0)
    [OK] SplitProfile: 58 entrada(s) → /tmp/b/java-splitprofile-main.contract
[OK] derive-contract main (exit 0)
[INFO] SplitProfile já existe no sdk-java — relatório de paridade, nada será entregue
    [INFO] /tmp/b/main/src/main/java/com/starkbank/SplitProfile.java — linguagem java
    [OK] /tmp/b/main/src/main/java/com/starkbank/SplitProfile.java sem problemas bloqueantes
[OK] assert-generated main (exit 0)
[INFO] paridade medida pelo gate estrito: 1 passaram, 0 reprovaram, 0 ausentes no SDK de referência
"""

EVENT_FAILS = """\
[INFO] Event já existe no sdk-java — relatório de paridade, nada será entregue
    [INFO] /tmp/b/main/src/main/java/com/starkbank/Event.java — linguagem java
    [ERROR] /tmp/b/main/src/main/java/com/starkbank/Event.java CONTRACT_GAP constructor ausente: public Event(String created, Boolean isDelivered, String subscription, String id, String workspaceId)
    [ERROR] /tmp/b/main/src/main/java/com/starkbank/Event.java CONTRACT_GAP constructor ausente: public Event(Map<String, Object> data)
    [ERROR] /tmp/b/main/src/main/java/com/starkbank/Event.java CONTRACT_GAP inner ausente: public static class Deserializer implements JsonDeserializer<Event>
    [ERROR] /tmp/b/main/src/main/java/com/starkbank/Event.java CONTRACT_GAP field divergente: esperado 'public Transfer.Log log;', gerado 'public String log;'
    [ERROR] /tmp/b/main/src/main/java/com/starkbank/Event.java CONTRACT_GAP declaration ausente: public class Event extends Resource
    [ERROR] 5 gap(s) de contrato em modo --strict
[ERROR] assert-generated main (exit 1)
[INFO] paridade medida pelo gate estrito: 0 passaram, 1 reprovaram, 0 ausentes no SDK de referência
"""

CARD_METHOD_BLOCKED = """\
[INFO] CardMethod (java, 1 run(s)) → /tmp/into
    [ERROR] apis/spec-v2.openapi.yaml:3411 NO_ID CardMethod: schema sem 'id': o template emite 'extends Resource' com 'super(null)'
    [ERROR] 1 problema(s) bloqueando a geração
[ERROR] lint-spec (exit 1)
"""

WIDGET_ABSENT = """\
[OK] lint-spec (exit 0)
[INFO] Widget não existe no sdk-java, classificado pelo list-gaps como lacuna canônica — esta execução preenche a lacuna
[OK] Widget construído e verificado em /tmp/into
"""

WIDGET_ABSENT_ANNOUNCED = """\
[OK] lint-spec (exit 0)
[INFO] Widget não existe no sdk-java, classificado pelo list-gaps como lacuna canônica — esta execução preenche a lacuna
"""

GENERATOR_CRASH = """\
[INFO] Widget (java, 1 run(s)) → /tmp/into
[OK] lint-spec (exit 0)
[ERROR] generate main (java) (exit 1)
"""


def loadParityReport():
    spec = importlib.util.spec_from_file_location("parity_report", TOOLS_DIR / "parity-report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parity = loadParityReport()


def test_parseGapsPassingResourceHasNoGaps():
    result = parity.parseGaps(SPLIT_PROFILE_PASSES)
    assert result["state"] == "passa"
    assert result["gaps"] == []


def test_parseGapsFailingResourceListsKindAndText():
    result = parity.parseGaps(EVENT_FAILS)
    assert result["state"] == "reprova"
    assert len(result["gaps"]) == 5
    assert result["gaps"][3] == (
        "field",
        "divergente: esperado 'public Transfer.Log log;', gerado 'public String log;'",
    )
    assert result["gaps"][4] == ("declaration", "ausente: public class Event extends Resource")


def test_parseGapsIgnoresTheStrictSummaryLine():
    kinds = [kind for kind, _ in parity.parseGaps(EVENT_FAILS)["gaps"]]
    assert kinds == ["constructor", "constructor", "inner", "field", "declaration"]


def test_parseGapsLintBlockedKeepsTheLintCode():
    result = parity.parseGaps(CARD_METHOD_BLOCKED)
    assert result["state"] == "barrado pelo lint-spec"
    assert result["detail"] == "NO_ID"
    assert result["gaps"] == []


def test_parseGapsResourceMissingFromTheSdkIsAbsent():
    assert parity.parseGaps(WIDGET_ABSENT)["state"] == "ausente no SDK"


def test_parseGapsAbsentResourceThatFailsAfterTheNoticeIsError():
    output = WIDGET_ABSENT_ANNOUNCED + "[ERROR] gerador main retornou 0 mas não produziu o artefato: /tmp/x\n"
    result = parity.parseGaps(output)
    assert result["state"] == "erro"
    assert "não produziu o artefato" in result["detail"]


def test_parseGapsOtherFailureIsErrorWithTheLastErrorLine():
    result = parity.parseGaps(GENERATOR_CRASH)
    assert result["state"] == "erro"
    assert result["detail"] == "[ERROR] generate main (java) (exit 1)"


def test_parseGapsEmptyOutputIsError():
    assert parity.parseGaps("")["state"] == "erro"


def _results():
    return [
        {"resource": "SplitProfile", **parity.parseGaps(SPLIT_PROFILE_PASSES)},
        {"resource": "Event", **parity.parseGaps(EVENT_FAILS)},
        {"resource": "CardMethod", **parity.parseGaps(CARD_METHOD_BLOCKED)},
        {"resource": "Widget", **parity.parseGaps(WIDGET_ABSENT)},
        {"resource": "Crashy", **parity.parseGaps(GENERATOR_CRASH)},
    ]


def test_aggregateCountsStrictPassesOverTheMeasuredTotal():
    report = parity.aggregate(_results())
    assert report["passing"] == 1
    assert report["total"] == 5


def test_aggregateSumsGapsByKindOrderedByCountThenName():
    report = parity.aggregate(_results())
    assert list(report["byKind"].items()) == [
        ("constructor", 2), ("declaration", 1), ("field", 1), ("inner", 1),
    ]


def test_aggregateOrdersResourcesByGapsDescThenName():
    names = [entry["resource"] for entry in parity.aggregate(_results())["resources"]]
    assert names == ["Event", "CardMethod", "Crashy", "SplitProfile", "Widget"]


def test_aggregateResourceEntryCarriesStateGapCountAndGapDetails():
    entry = parity.aggregate(_results())["resources"][0]
    assert entry["state"] == "reprova"
    assert entry["gaps"] == 5
    assert entry["gapDetails"][0][0] == "constructor"


def test_renderShowsScoreboardTableKindsAndSha():
    report = {**parity.aggregate(_results()), "sha": "f60902a"}
    text = parity.render(report)
    assert "1 de 5 passam o gate estrito" in text
    assert "sdk-java @ f60902a" in text
    assert "Event" in text and "reprova" in text and "barrado pelo lint-spec (NO_ID)" in text
    assert "constructor" in text
    assert text.index("Event") < text.index("SplitProfile")


def test_renderIsDeterministic():
    report = {**parity.aggregate(_results()), "sha": "f60902a"}
    assert parity.render(report) == parity.render({**parity.aggregate(list(reversed(_results()))), "sha": "f60902a"})


def _fakeRunner(outputs):
    calls = []

    def run(command, timeout):
        resource = command[2]
        calls.append(command)
        return outputs[resource]

    return run, calls


def _sdkRoot(tmpPath):
    (tmpPath / "src/main/java/com/starkbank").mkdir(parents=True)
    return tmpPath


def test_mainMeasuresGivenResourcesAndAlwaysExitsZero(tmpPath, capsys):
    runner, calls = _fakeRunner({"SplitProfile": (4, SPLIT_PROFILE_PASSES), "Event": (4, EVENT_FAILS)})
    code = parity.main(["SplitProfile", "Event"], runner=runner, javaRoot=_sdkRoot(tmpPath), sha="abc1234")
    out = capsys.readouterr().out
    assert code == 0
    assert "1 de 2 passam o gate estrito" in out
    assert len(calls) == 2
    assert all("--advisory" in command and "--lang" in command for command in calls)


def test_mainJsonWritesTheSameReportToTheGivenFile(tmpPath):
    runner, _ = _fakeRunner({"Event": (4, EVENT_FAILS)})
    target = tmpPath / "parity.json"
    code = parity.main(
        ["Event", "--json", str(target)], runner=runner, javaRoot=_sdkRoot(tmpPath), sha="abc1234",
    )
    report = json.loads(target.read_text(encoding="utf-8"))
    assert code == 0
    assert report["sha"] == "abc1234"
    assert report["passing"] == 0
    assert report["byKind"]["constructor"] == 2


def test_mainUnknownResourceIsUsageError(tmpPath, capsys):
    runner, calls = _fakeRunner({})
    code = parity.main(["Nope"], runner=runner, javaRoot=_sdkRoot(tmpPath), sha="abc1234")
    assert code == 2
    assert "Nope" in capsys.readouterr().out
    assert calls == []


def test_mainMissingJavaReferenceIsEnvironmentError(tmpPath, capsys):
    code = parity.main(
        ["Event"], runner=lambda command, timeout: (0, ""), javaRoot=tmpPath / "ausente", sha="abc1234",
    )
    assert code == 2
    assert "sdk-java" in capsys.readouterr().out


def test_mainRepeatedResourceIsMeasuredOnce(tmpPath, capsys):
    runner, calls = _fakeRunner({"Event": (4, EVENT_FAILS)})
    code = parity.main(["Event", "Event"], runner=runner, javaRoot=_sdkRoot(tmpPath), sha="abc1234")
    assert code == 0
    assert len(calls) == 1
    assert "0 de 1 passam o gate estrito" in capsys.readouterr().out


def test_mainJsonPathWithoutParentDirectoryFailsBeforeMeasuring(tmpPath, capsys):
    runner, calls = _fakeRunner({"Event": (4, EVENT_FAILS)})
    code = parity.main(
        ["Event", "--json", str(tmpPath / "ausente" / "parity.json")],
        runner=runner, javaRoot=_sdkRoot(tmpPath), sha="abc1234",
    )
    assert code == 2
    assert "--json" in capsys.readouterr().out
    assert calls == []


def test_mainWithoutResourcesMeasuresEverySchema(tmpPath, capsys):
    seen = []

    def runner(command, timeout):
        seen.append(command[2])
        return (4, SPLIT_PROFILE_PASSES)

    code = parity.main(["--jobs", "1"], runner=runner, javaRoot=_sdkRoot(tmpPath), sha="abc1234")
    assert code == 0
    assert len(seen) == 42
    assert "SplitProfile" in seen and "CardMethod" in seen
    assert "42 de 42 passam o gate estrito" in capsys.readouterr().out


def test_schemaResourcesReadsTheClassNameFromTheSchemaNotTheFileName():
    names = parity.schemaResources()
    assert "SplitProfile" in names
    assert "splitprofile" not in names
    assert len(names) == 42
