import json

import pytest

from test_compare_with_real import SPLIT as REAL, SPLIT, trees, result, compare

QUERY = "    public static List<Split> query() throws Exception {\n        return null;\n    }\n"
QUERY_GAP = "signature ausente: public static List<Split> query()"
GET_GAP = "signature ausente: public static Split get(String id)"
REASON = "escrito a mao no real, sem molde gerado"


def entry(kind="signature", text=QUERY_GAP, **extra):
    return {"kind": kind, "text": text, "count": 1, "reason": REASON, **extra}


def gapped(resource, *gaps):
    return {**result(resource, state="com gaps"), "gaps": list(gaps)}


def waive(results, waivers):
    return compare.applyWaivers(results=results, waivers=waivers)


def writeWaivers(tmp_path, text):
    path = tmp_path / "java-handwritten.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def runMain(tmp_path, *extra, resource="Split", real=REAL, generated=None):
    generatedRoot, sourceRoot = trees(tmp_path, real=real, generated=generated, resource=resource)
    jsonPath = tmp_path / "out.json"
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--json", str(jsonPath),
        *extra,
        resource,
    ])
    return code, json.loads(jsonPath.read_text(encoding="utf-8"))


def test_waiverConsumesOnlyTheMatchingGap():
    other = ("signature", GET_GAP)
    [waived], obsolete = waive([gapped("Split", ("signature", QUERY_GAP), other)], {"Split": [entry()]})
    assert waived["gaps"] == [other]
    assert waived["waived"] == [{"kind": "signature", "text": QUERY_GAP, "reason": REASON}]
    assert waived["state"] == "com gaps"
    assert obsolete == []


def test_countWaivesTheRepeatedGapUpToCount():
    gap = ("field", "field ausente: public String confirmationMode;")
    [one], oneObsolete = waive([gapped("Split", gap, gap)], {"Split": [entry("field", gap[1])]})
    assert one["gaps"] == [gap]
    assert len(one["waived"]) == 1
    assert oneObsolete == []

    [two], twoObsolete = waive([gapped("Split", gap, gap)], {"Split": [entry("field", gap[1], count=2)]})
    assert two["gaps"] == []
    assert len(two["waived"]) == 2
    assert twoObsolete == []


def test_countAboveTheOccurrencesIsObsoleteAndNamesTheEntry():
    gap = ("field", "field ausente: public String confirmationMode;")
    [waived], obsolete = waive([gapped("Split", gap)], {"Split": [entry("field", gap[1], count=2)]})
    assert waived["gaps"] == []
    assert len(waived["waived"]) == 1
    assert obsolete == [
        {"resource": "Split", "kind": "field", "text": gap[1], "count": 2, "consumed": 1, "reason": REASON}
    ]


def test_waiverWithoutAnyMatchingGapIsObsolete():
    [kept], obsolete = waive([gapped("Split", ("signature", GET_GAP))], {"Split": [entry()]})
    assert kept["gaps"] == [("signature", GET_GAP)]
    assert kept["waived"] == []
    assert [(item["resource"], item["text"], item["consumed"]) for item in obsolete] == [("Split", QUERY_GAP, 0)]


@pytest.mark.parametrize("nearMiss", [
    ("signature", QUERY_GAP + " "),
    ("signature", QUERY_GAP.replace("query()", "query(int a)")),
    ("signature", QUERY_GAP.upper()),
    ("signature", "ausente: public static List<Split> query()"),
    ("constructor", QUERY_GAP),
])
def test_nearMatchDoesNotWaiveAndTheEntryBecomesObsolete(nearMiss):
    [kept], obsolete = waive([gapped("Split", nearMiss)], {"Split": [entry()]})
    assert kept["gaps"] == [nearMiss]
    assert kept["waived"] == []
    assert len(obsolete) == 1


def test_obsoleteIsOnlyEnforcedForResourcesMeasuredInTheRun():
    waivers = {"Split": [entry()], "Split": [entry()], "Gadget": [entry()]}
    notGenerated = {**result("Gadget", similarity=None, state="nao gerado"), "gaps": []}
    results = [gapped("Split", ("signature", QUERY_GAP)), notGenerated]
    _, obsolete = waive(results, waivers)
    assert obsolete == []


def test_resourceWaivedToZeroHasItsOwnState():
    [waived], _ = waive([gapped("Split", ("signature", QUERY_GAP))], {"Split": [entry()]})
    assert waived["gaps"] == []
    assert waived["state"] == "sem gaps (com dispensa)"


def test_resourcesWithoutWaiversAreReturnedUntouched():
    clean = result("Clean", state="igual")
    gaps = gapped("Gaps", ("signature", QUERY_GAP))
    results, obsolete = waive([clean, gaps], {})
    assert results == [clean, gaps]
    assert obsolete == []


def test_reportKeepsTheTwoZeroGapCountsApart():
    results, _ = waive(
        [
            gapped("Waived", ("signature", QUERY_GAP)),
            gapped("Partial", ("signature", QUERY_GAP), ("signature", GET_GAP)),
            result("Plain", state="sem gaps"),
        ],
        {"Waived": [entry()], "Partial": [entry()]},
    )
    report = compare.buildReport(results=results, sha="x")
    assert report["zeroGaps"] == 1
    assert report["zeroGapsWithWaiver"] == 1
    assert report["waivedTotal"] == 2
    assert report["gapsTotal"] == 1
    assert report["obsoleteWaivers"] == []
    byName = {item["resource"]: item for item in report["resources"]}
    assert byName["Waived"]["state"] == "sem gaps (com dispensa)"
    assert byName["Waived"]["waivedCount"] == 1
    assert byName["Waived"]["waived"] == [{"kind": "signature", "text": QUERY_GAP, "reason": REASON}]
    assert byName["Plain"]["waived"] == [] and byName["Plain"]["waivedCount"] == 0


def test_renderPrintsTheTwoNumbersTheTotalsAndTheReasons():
    results, _ = waive(
        [gapped("Waived", ("signature", QUERY_GAP)), result("Plain", state="sem gaps")],
        {"Waived": [entry()]},
    )
    text = compare.render(compare.buildReport(results=results, sha="x"))
    assert "1 com 0 gaps" in text
    assert (
        "[INFO] dispensa de membros escritos à mão: 1 sem gaps só pela dispensa; "
        "1 gaps dispensados; 0 gaps restantes"
    ) in text
    assert any(QUERY_GAP in line and REASON in line and "Waived" in line for line in text.splitlines())


def test_renderListsObsoleteWaiversAsErrors():
    gap = ("field", "field ausente: public String confirmationMode;")
    results, obsolete = waive([gapped("Split", gap)], {"Split": [entry("field", gap[1], count=3)]})
    text = compare.render(compare.buildReport(results=results, sha="x", obsolete=obsolete))
    line = next(line for line in text.splitlines() if "dispensa obsoleta" in line)
    assert line.startswith("[ERROR]")
    assert "Split" in line and gap[1] in line and "count 3" in line and "consumida 1" in line


def test_renderWithoutWaiversIsUnchanged():
    results = [result("Beta", gaps=2, similarity=0.5, state="com gaps"), result("Alpha", state="igual")]
    text = compare.render(compare.buildReport(results=results, sha="abc"))
    assert "dispensa" not in text


def test_mainWaivesTheRealGapAndExitsZero(tmp_path):
    path = writeWaivers(tmp_path, f"Split:\n  - kind: signature\n    text: \"{QUERY_GAP}\"\n    reason: \"{REASON}\"\n")
    code, report = runMain(tmp_path, "--waivers", str(path), generated=REAL.replace(QUERY, ""))
    entryOut = report["resources"][0]
    assert code == 0
    assert entryOut["gaps"] == [] and entryOut["gapCount"] == 0
    assert entryOut["waived"] == [{"kind": "signature", "text": QUERY_GAP, "reason": REASON}]
    assert entryOut["state"] == "sem gaps (com dispensa)"
    assert report["zeroGaps"] == 0 and report["zeroGapsWithWaiver"] == 1 and report["waivedTotal"] == 1


def test_mainFailsOnObsoleteWaiverForAComparedResource(tmp_path, capsys):
    path = writeWaivers(tmp_path, f"Split:\n  - kind: signature\n    text: \"{GET_GAP}\"\n    reason: \"{REASON}\"\n")
    code, report = runMain(tmp_path, "--waivers", str(path), generated=REAL.replace(QUERY, ""))
    out = capsys.readouterr().out
    assert code == 1
    assert "dispensa obsoleta" in out and "Split" in out and GET_GAP in out
    assert report["obsoleteWaivers"][0]["consumed"] == 0
    assert report["resources"][0]["gapCount"] == 1


def test_mainDoesNotFailOnObsoleteWaiverOfAResourceNotCompared(tmp_path):
    path = writeWaivers(tmp_path, f"Boleto:\n  - kind: signature\n    text: \"{GET_GAP}\"\n    reason: \"{REASON}\"\n")
    code, report = runMain(tmp_path, "--waivers", str(path), generated=REAL)
    assert code == 0
    assert report["obsoleteWaivers"] == []


def test_mainNoWaiversShowsTheRawGapsEvenWithADefaultFile(tmp_path, monkeypatch, capsys):
    path = writeWaivers(tmp_path, f"Split:\n  - kind: signature\n    text: \"{QUERY_GAP}\"\n    reason: \"{REASON}\"\n")
    generated = REAL.replace(QUERY, "")
    monkeypatch.setattr(compare, "WAIVERS_FILE", path)
    code, report = runMain(tmp_path, "--no-waivers", generated=generated)
    withFlag = capsys.readouterr().out
    assert code == 0
    assert report["resources"][0]["gapCount"] == 1
    assert report["waivedTotal"] == 0
    assert "dispensa" not in withFlag


def test_mainExplicitMissingWaiverFileIsUsageError(tmp_path, capsys):
    generatedRoot, sourceRoot = trees(tmp_path, real=REAL, generated=REAL)
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--waivers", str(tmp_path / "nowhere.yaml"),
        "Split",
    ])
    assert code == 2
    assert "--waivers" in capsys.readouterr().out


def test_mainDefaultFileMissingMeansNoWaivers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(compare, "WAIVERS_FILE", tmp_path / "absent.yaml")
    code, report = runMain(tmp_path, generated=REAL.replace(QUERY, ""))
    assert code == 0
    assert report["waivedTotal"] == 0 and report["resources"][0]["gapCount"] == 1


def test_mainMalformedWaiverFileIsUsageError(tmp_path, capsys):
    path = writeWaivers(tmp_path, "Nope:\n  - kind: signature\n")
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=SPLIT, resource="Split")
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--waivers", str(path),
        "Split",
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "[ERROR]" in out and "Nope" in out
    assert "comparados" not in out


def test_loadWaiversMissingFileIsNoWaivers(tmp_path):
    assert compare.loadWaivers(tmp_path / "absent.yaml") == {}


def test_loadWaiversCommentOnlyFileIsNoWaivers(tmp_path):
    assert compare.loadWaivers(writeWaivers(tmp_path, "# so o cabecalho\n")) == {}


def test_loadWaiversDefaultsCountToOne(tmp_path):
    text = f"Boleto:\n  - kind: field\n    text: \"field ausente: public String x;\"\n    reason: \"{REASON}\"\n"
    assert compare.loadWaivers(writeWaivers(tmp_path, text)) == {
        "Boleto": [{"kind": "field", "text": "field ausente: public String x;", "count": 1, "reason": REASON}]
    }


def block(drop=None, **fields):
    merged = {"kind": "signature", "text": "signature ausente: x", "reason": REASON, **fields}
    merged.pop(drop, None)
    lines = [
        f"{'  - ' if index == 0 else '    '}{key}: {json.dumps(value)}"
        for index, (key, value) in enumerate(merged.items())
    ]
    return "Boleto:\n" + "\n".join(lines) + "\n"


INVALID = {
    "unknownResource": ("Nope:\n  - kind: field\n", ["Nope", "java-handwritten"]),
    "wrongCaseResource": ("boleto:\n  - kind: field\n", ["boleto"]),
    "topLevelList": ("- Boleto\n", ["java-handwritten", "mapa"]),
    "resourceValueNotList": ("Boleto:\n  kind: field\n", ["Boleto", "lista"]),
    "entryNotMapping": ("Boleto:\n  - just text\n", ["Boleto[0]", "just text"]),
    "unknownKind": (block(kind="methods"), ["Boleto[0].kind", "methods"]),
    "nonStringKind": (block(kind=7), ["Boleto[0].kind", "7"]),
    "missingKind": (block(drop="kind"), ["Boleto[0].kind"]),
    "emptyText": (block(text=""), ["Boleto[0].text"]),
    "blankText": (block(text="   "), ["Boleto[0].text"]),
    "nonStringText": (block(text=123), ["Boleto[0].text", "123"]),
    "missingText": (block(drop="text"), ["Boleto[0].text"]),
    "emptyReason": (block(reason=""), ["Boleto[0].reason"]),
    "nonStringReason": (block(reason=["a"]), ["Boleto[0].reason"]),
    "missingReason": (block(drop="reason"), ["Boleto[0].reason"]),
    "zeroCount": (block(count=0), ["Boleto[0].count", "0"]),
    "negativeCount": (block(count=-2), ["Boleto[0].count", "-2"]),
    "stringCount": (block(count="2"), ["Boleto[0].count", "'2'"]),
    "floatCount": (block(count=1.5), ["Boleto[0].count", "1.5"]),
    "booleanCount": (block(count=True), ["Boleto[0].count", "True"]),
    "unknownKey": (block(cout=2), ["Boleto[0].cout"]),
    "duplicateEntry": (block() + block().split("\n", 1)[1], ["Boleto", "duplicad", "signature ausente: x"]),
    "malformedYaml": ("Boleto: [\n", ["java-handwritten"]),
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_loadWaiversRejectsInvalidShapeNamingResourceKeyAndValue(tmp_path, name):
    text, fragments = INVALID[name]
    with pytest.raises(ValueError) as raised:
        compare.loadWaivers(writeWaivers(tmp_path, text))
    for fragment in fragments:
        assert fragment in str(raised.value)
