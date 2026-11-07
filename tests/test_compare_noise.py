import json

import pytest

from test_compare_with_real import SPLIT as REAL, trees, result, compare, noRealWaivers  # noqa: F401
from test_compare_waivers import QUERY, QUERY_GAP, GET_GAP, gapped, writeWaivers

UNUSED_IMPORT = "import java.util.Map;"
NOISE_GAP = f"require ausente: {UNUSED_IMPORT}"
NOISE_REASON = "importado no real e nunca usado"
HANDWRITTEN_REASON = "escrito a mao no real, sem molde gerado"
NO_NOISE_IMPORT = REAL.replace(f"{UNUSED_IMPORT}\n", "")


def noiseEntry(text=NOISE_GAP, **extra):
    return {"kind": "require", "text": text, "count": 1, "reason": NOISE_REASON, **extra}


def handwrittenEntry(text=QUERY_GAP):
    return {"kind": "signature", "text": text, "count": 1, "reason": HANDWRITTEN_REASON}


def noised(results, noise):
    return compare.applyNoise(results=results, noise=noise)


def writeNoise(tmp_path, text):
    path = tmp_path / "java-unused-imports.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def noiseYaml(text=NOISE_GAP, resource="Split"):
    return f"{resource}:\n  - kind: require\n    text: \"{text}\"\n    reason: \"{NOISE_REASON}\"\n"


def handwrittenYaml(text=QUERY_GAP):
    return f"Split:\n  - kind: signature\n    text: \"{text}\"\n    reason: \"{HANDWRITTEN_REASON}\"\n"


def runMain(tmp_path, *extra, real=REAL, generated=NO_NOISE_IMPORT):
    generatedRoot, sourceRoot = trees(tmp_path, real=real, generated=generated, resource="Split")
    jsonPath = tmp_path / "out.json"
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--json", str(jsonPath),
        *extra,
        "Split",
    ])
    return code, json.loads(jsonPath.read_text(encoding="utf-8"))


def test_theRealGapOfTheUnusedImportIsTheTextTheFileMustCarry():
    gaps = compare.gapsOf(realSource=REAL, generatedSource=NO_NOISE_IMPORT, filePath="Split.java")
    assert ("require", NOISE_GAP) in gaps


def test_noiseConsumesOnlyTheMatchingGapIntoItsOwnList():
    other = ("signature", GET_GAP)
    [applied], obsolete = noised([gapped("Split", ("require", NOISE_GAP), other)], {"Split": [noiseEntry()]})
    assert applied["gaps"] == [other]
    assert applied["noiseWaived"] == [{"kind": "require", "text": NOISE_GAP, "reason": NOISE_REASON}]
    assert "waived" not in applied
    assert applied["state"] == "com gaps"
    assert obsolete == []


def test_resourceWhoseGapsAreAllNoiseHasItsOwnState():
    [applied], _ = noised([gapped("Split", ("require", NOISE_GAP))], {"Split": [noiseEntry()]})
    assert applied["gaps"] == []
    assert applied["state"] == "sem gaps (so ruido)"


def test_noiseEntryThatConsumesLessThanCountIsObsoleteAndNamesTheEntry():
    [applied], obsolete = noised([gapped("Split", ("require", NOISE_GAP))], {"Split": [noiseEntry(count=2)]})
    assert applied["gaps"] == []
    assert obsolete == [
        {"resource": "Split", "kind": "require", "text": NOISE_GAP, "count": 2, "consumed": 1, "reason": NOISE_REASON}
    ]


@pytest.mark.parametrize("nearMiss", [
    ("require", NOISE_GAP + " "),
    ("require", NOISE_GAP.replace("Map", "List")),
    ("require", NOISE_GAP.upper()),
    ("signature", NOISE_GAP),
])
def test_nearMatchDoesNotConsumeAndTheNoiseEntryBecomesObsolete(nearMiss):
    [applied], obsolete = noised([gapped("Split", nearMiss)], {"Split": [noiseEntry()]})
    assert applied["gaps"] == [nearMiss]
    assert applied["noiseWaived"] == []
    assert len(obsolete) == 1


def test_obsoleteNoiseIsOnlyEnforcedForResourcesMeasuredInTheRun():
    notGenerated = {**result("Gadget", similarity=None, state="nao gerado"), "gaps": []}
    _, obsolete = noised([notGenerated], {"Gadget": [noiseEntry()]})
    assert obsolete == []


def test_bothFilesOnOneResourceAreConsumedIndependentlyAndTheStateStaysWithDispensa():
    gaps = [("signature", QUERY_GAP), ("require", NOISE_GAP)]
    waived, waivedObsolete = compare.applyWaivers(
        results=[gapped("Split", *gaps)],
        waivers={"Split": [handwrittenEntry()]},
    )
    [both], noiseObsolete = noised(waived, {"Split": [noiseEntry()]})
    assert both["gaps"] == []
    assert both["waived"] == [{"kind": "signature", "text": QUERY_GAP, "reason": HANDWRITTEN_REASON}]
    assert both["noiseWaived"] == [{"kind": "require", "text": NOISE_GAP, "reason": NOISE_REASON}]
    assert both["state"] == "sem gaps (com dispensa)"
    assert waivedObsolete == [] and noiseObsolete == []


def test_aTextInBothFilesWithOneGapLeavesTheSecondEntryObsolete():
    waived, _ = compare.applyWaivers(
        results=[gapped("Split", ("require", NOISE_GAP))],
        waivers={"Split": [{**noiseEntry(), "reason": HANDWRITTEN_REASON}]},
    )
    [applied], obsolete = noised(waived, {"Split": [noiseEntry()]})
    assert applied["noiseWaived"] == []
    assert [item["text"] for item in obsolete] == [NOISE_GAP]


def test_reportKeepsNoiseCountsApartFromTheHandwrittenOnes():
    results, _ = noised(
        [
            gapped("OnlyNoise", ("require", NOISE_GAP)),
            gapped("Partial", ("require", NOISE_GAP), ("signature", GET_GAP)),
            result("Plain", state="sem gaps"),
        ],
        {"OnlyNoise": [noiseEntry()], "Partial": [noiseEntry()]},
    )
    report = compare.buildReport(results=results, sha="x")
    assert report["zeroGaps"] == 1
    assert report["zeroGapsWithNoise"] == 1
    assert report["zeroGapsWithWaiver"] == 0
    assert report["noiseWaivedTotal"] == 2
    assert report["waivedTotal"] == 0
    assert report["gapsTotal"] == 1
    assert report["obsoleteWaivers"] == [] and report["obsoleteNoise"] == []
    byName = {item["resource"]: item for item in report["resources"]}
    assert byName["OnlyNoise"]["state"] == "sem gaps (so ruido)"
    assert byName["OnlyNoise"]["noiseWaivedCount"] == 1
    assert byName["OnlyNoise"]["noiseWaived"] == [{"kind": "require", "text": NOISE_GAP, "reason": NOISE_REASON}]
    assert byName["OnlyNoise"]["waived"] == [] and byName["OnlyNoise"]["waivedCount"] == 0
    assert byName["Plain"]["noiseWaived"] == [] and byName["Plain"]["noiseWaivedCount"] == 0


def test_aResourceNeedingBothClassesCountsAsWaiverNotAsNoise():
    waived, _ = compare.applyWaivers(
        results=[gapped("Both", ("signature", QUERY_GAP), ("require", NOISE_GAP))],
        waivers={"Both": [handwrittenEntry()]},
    )
    results, _ = noised(waived, {"Both": [noiseEntry()]})
    report = compare.buildReport(results=results, sha="x")
    assert report["zeroGapsWithWaiver"] == 1
    assert report["zeroGapsWithNoise"] == 0
    assert report["zeroGaps"] == 0


def test_renderPrintsTheNoiseSummaryTheReasonsAndKeepsTheHandwrittenLineApart():
    results, _ = noised(
        [gapped("Noisy", ("require", NOISE_GAP)), result("Plain", state="sem gaps")],
        {"Noisy": [noiseEntry()]},
    )
    text = compare.render(compare.buildReport(results=results, sha="x"))
    assert "[INFO] dispensa de imports sem uso no real: 1 sem gaps so pelo ruido; 1 gaps dispensados" in text
    assert "dispensa de membros escritos à mão" not in text
    assert any(NOISE_GAP in line and NOISE_REASON in line and "Noisy" in line for line in text.splitlines())
    assert compare.NOISE_FILE.name in text


def test_renderListsObsoleteNoiseAsErrorsNamingTheNoiseFile():
    results, obsolete = noised([gapped("Split", ("require", NOISE_GAP))], {"Split": [noiseEntry(count=3)]})
    text = compare.render(compare.buildReport(results=results, sha="x", obsoleteNoise=obsolete))
    line = next(line for line in text.splitlines() if "dispensa obsoleta" in line)
    assert line.startswith("[ERROR]")
    assert "Split" in line and NOISE_GAP in line and "count 3" in line and "consumida 1" in line
    assert compare.NOISE_FILE.name in line


def test_renderWithoutNoiseIsUnchangedByTheNoiseFields():
    results = [result("Beta", gaps=2, similarity=0.5, state="com gaps"), result("Alpha", state="igual")]
    assert compare.render(compare.buildReport(results=results, sha="abc")).split("\n") == [
        "[INFO] gerado x real (sdk-java @ abc)",
        "[INFO] 2 comparados de 2; 1 com 0 gaps; 1 idênticos; similaridade média 75.0%",
        "",
        "  recurso  gaps  similar.  estado",
        "  Beta        2     50.0%  com gaps",
        "  Alpha       0    100.0%  igual",
        "",
        "[INFO] gaps por tipo:",
        "  constructor      2",
    ]


def test_mainNoiseConsumesTheRealGapAndExitsZero(tmp_path):
    path = writeNoise(tmp_path, noiseYaml())
    code, report = runMain(tmp_path, "--noise", str(path))
    entryOut = report["resources"][0]
    assert code == 0
    assert entryOut["gaps"] == [] and entryOut["state"] == "sem gaps (so ruido)"
    assert entryOut["noiseWaived"] == [{"kind": "require", "text": NOISE_GAP, "reason": NOISE_REASON}]
    assert report["zeroGaps"] == 0 and report["zeroGapsWithNoise"] == 1 and report["noiseWaivedTotal"] == 1


def test_mainFailsOnObsoleteNoiseOfAComparedResource(tmp_path, capsys):
    path = writeNoise(tmp_path, noiseYaml())
    code, report = runMain(tmp_path, "--noise", str(path), generated=REAL)
    out = capsys.readouterr().out
    assert code == 1
    assert "dispensa obsoleta" in out and NOISE_GAP in out
    assert report["obsoleteNoise"][0]["consumed"] == 0
    assert report["obsoleteWaivers"] == []


def test_mainNoiseDoesNotFailOnObsoleteNoiseOfAResourceNotCompared(tmp_path):
    path = writeNoise(tmp_path, noiseYaml(resource="Boleto"))
    code, report = runMain(tmp_path, "--noise", str(path), generated=REAL)
    assert code == 0
    assert report["obsoleteNoise"] == []


def test_mainBothFilesOnOneResourceExitZeroWithTheStateAndTwoLines(tmp_path, capsys):
    noise = writeNoise(tmp_path, noiseYaml())
    waivers = writeWaivers(tmp_path, handwrittenYaml())
    code, report = runMain(
        tmp_path, "--noise", str(noise), "--waivers", str(waivers), generated=NO_NOISE_IMPORT.replace(QUERY, ""),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert report["resources"][0]["state"] == "sem gaps (com dispensa)"
    assert report["waivedTotal"] == 1 and report["noiseWaivedTotal"] == 1
    assert "dispensa de membros escritos à mão" in out
    assert "dispensa de imports sem uso no real" in out


def test_mainNoWaiversDisablesBothFilesEvenWithDefaultFiles(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(compare, "NOISE_FILE", writeNoise(tmp_path, noiseYaml()))
    monkeypatch.setattr(compare, "WAIVERS_FILE", writeWaivers(tmp_path, handwrittenYaml()))
    code, report = runMain(tmp_path, "--no-waivers", generated=NO_NOISE_IMPORT.replace(QUERY, ""))
    out = capsys.readouterr().out
    assert code == 0
    assert report["resources"][0]["gapCount"] == 2
    assert report["waivedTotal"] == 0 and report["noiseWaivedTotal"] == 0
    assert "dispensa" not in out


def test_mainDefaultNoiseFileIsAppliedWithoutAFlag(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "NOISE_FILE", writeNoise(tmp_path, noiseYaml()))
    code, report = runMain(tmp_path)
    assert code == 0
    assert report["noiseWaivedTotal"] == 1


def test_mainDefaultNoiseFileMissingMeansNoNoise(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "NOISE_FILE", tmp_path / "absent.yaml")
    code, report = runMain(tmp_path)
    assert code == 0
    assert report["noiseWaivedTotal"] == 0 and report["resources"][0]["gapCount"] == 1


def test_mainEmptyNoiseFilePrintsExactlyWhatNoWaiversPrints(tmp_path, capsys):
    empty = writeNoise(tmp_path, "# so o cabecalho\n")
    runMain(tmp_path, "--no-waivers")
    baseline = capsys.readouterr().out
    runMain(tmp_path, "--noise", str(empty))
    assert capsys.readouterr().out == baseline
    assert "ruido" not in baseline


def test_mainExplicitMissingNoiseFileIsUsageError(tmp_path, capsys):
    generatedRoot, sourceRoot = trees(tmp_path, real=REAL, generated=REAL, resource="Split")
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--noise", str(tmp_path / "nowhere.yaml"),
        "Split",
    ])
    assert code == 2
    assert "--noise" in capsys.readouterr().out


def test_mainNoiseWithNoWaiversIsUsageError(tmp_path, capsys):
    path = writeNoise(tmp_path, noiseYaml())
    generatedRoot, sourceRoot = trees(tmp_path, real=REAL, generated=REAL, resource="Split")
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--noise", str(path),
        "--no-waivers",
        "Split",
    ])
    assert code == 2
    assert "--noise" in capsys.readouterr().out


def test_mainMalformedNoiseFileIsUsageErrorNamingTheSection(tmp_path, capsys):
    path = writeNoise(tmp_path, noiseYaml(text="signature ausente: public void x()"))
    generatedRoot, sourceRoot = trees(tmp_path, real=REAL, generated=REAL, resource="Split")
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--noise", str(path),
        "Split",
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "[ERROR]" in out and "Split[0].text" in out
    assert "comparados" not in out


def test_loadNoiseMissingFileIsNoNoise(tmp_path):
    assert compare.loadNoise(tmp_path / "absent.yaml") == {}


def test_loadNoiseCommentOnlyFileIsNoNoise(tmp_path):
    assert compare.loadNoise(writeNoise(tmp_path, "# so o cabecalho\n")) == {}


def test_loadNoiseDefaultsCountToOneAndAcceptsEvent(tmp_path):
    text = noiseYaml(text="require ausente: import com.starkbank.ellipticcurve.Ecdsa;", resource="Event")
    assert compare.loadNoise(writeNoise(tmp_path, text)) == {
        "Event": [{
            "kind": "require",
            "text": "require ausente: import com.starkbank.ellipticcurve.Ecdsa;",
            "count": 1,
            "reason": NOISE_REASON,
        }]
    }


def block(drop=None, **fields):
    merged = {"kind": "require", "text": NOISE_GAP, "reason": NOISE_REASON, **fields}
    merged.pop(drop, None)
    lines = [
        f"{'  - ' if index == 0 else '    '}{key}: {json.dumps(value)}"
        for index, (key, value) in enumerate(merged.items())
    ]
    return "Boleto:\n" + "\n".join(lines) + "\n"


INVALID = {
    "unknownResource": ("Nope:\n  - kind: require\n", ["Nope", "java-unused-imports"]),
    "unknownKey": (block(cout=2), ["Boleto[0].cout"]),
    "signatureKind": (block(kind="signature"), ["Boleto[0].kind", "signature", "require"]),
    "unknownKind": (block(kind="methods"), ["Boleto[0].kind", "methods"]),
    "missingKind": (block(drop="kind"), ["Boleto[0].kind"]),
    "missingReason": (block(drop="reason"), ["Boleto[0].reason"]),
    "blankReason": (block(reason="  "), ["Boleto[0].reason"]),
    "missingText": (block(drop="text"), ["Boleto[0].text"]),
    "otherGapKind": (block(text="signature ausente: public void x()"), ["Boleto[0].text", "signature ausente"]),
    "noPrefix": (block(text="import java.util.Map;"), ["Boleto[0].text", "import java.util.Map;"]),
    "noSemicolon": (block(text="require ausente: import java.util.Map"), ["Boleto[0].text", "java.util.Map"]),
    "wildcard": (block(text="require ausente: import java.util.*;"), ["Boleto[0].text", "java.util.*"]),
    "staticImport": (block(text="require ausente: import static java.util.Map.of;"), ["Boleto[0].text", "static"]),
    "noPackage": (block(text="require ausente: import Map;"), ["Boleto[0].text", "Map"]),
    "extraTrailing": (block(text=NOISE_GAP + " "), ["Boleto[0].text"]),
    "trailingNewline": (block(text=NOISE_GAP + "\n"), ["Boleto[0].text"]),
    "zeroCount": (block(count=0), ["Boleto[0].count", "0"]),
    "duplicateEntry": (block() + block().split("\n", 1)[1], ["Boleto", "duplicad", NOISE_GAP]),
    "malformedYaml": ("Boleto: [\n", ["java-unused-imports"]),
    "topLevelList": ("- Boleto\n", ["java-unused-imports", "mapa"]),
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_loadNoiseRejectsInvalidShapeNamingResourceKeyAndValue(tmp_path, name):
    text, fragments = INVALID[name]
    with pytest.raises(ValueError) as raised:
        compare.loadNoise(writeNoise(tmp_path, text))
    for fragment in fragments:
        assert fragment in str(raised.value)
