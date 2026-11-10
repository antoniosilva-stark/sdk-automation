import json
import itertools
import importlib.util

import pytest

from conftest import TOOLS_DIR

spec = importlib.util.spec_from_file_location("gap_report", TOOLS_DIR / "gap-report.py")
gapReport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gapReport)

SHA = "abc1234"
SIG_A = "signature ausente: public static Foo a()"
SIG_B = "signature ausente: public static Foo b()"
CTOR = "constructor ausente: public Foo(String id)"
WAIVED_SIG = "signature ausente: public static Foo handmade()"
REASON = "REASON-SEGREDO escrito a mao no real"

CLASSES = {"Foo": {("signature", SIG_A): "sem-fonte", ("constructor", CTOR): "muda-api-publica"}}

REAL_JAVA = """package com.starkbank;

import java.util.List;

public class Foo {
    public String id;

    public static Foo a() {
        return null;
    }
}
"""

GENERATED_JAVA = """package com.starkbank;

import java.util.List;
import java.util.Map;

public class Foo {
    public String id;

    public static Foo a() {
        return null;
    }

    public static Foo extra() {
        return null;
    }
}
"""


def entryOf(resource="Foo", gaps=(), waived=(), similarity=0.9, state="com gaps", detail=""):
    return {
        "resource": resource,
        "state": state,
        "detail": detail,
        "gaps": [list(gap) for gap in gaps],
        "gapCount": len(gaps),
        "waived": list(waived),
        "waivedCount": len(waived),
        "similarity": similarity,
    }


def sliceOf(entry=None, classes=CLASSES, extras=None):
    return gapReport.buildSlice(entry=entry or entryOf(), classes=classes, sha=SHA, extras=extras)


def withGaps(resource="Foo", count=3):
    gaps = [("signature", f"signature ausente: m{index}()") for index in range(count)]
    return sliceOf(entry=entryOf(resource=resource, gaps=gaps), classes={})


def reportOf(entries):
    return {"sha": SHA, "total": len(entries), "resources": entries}


def writeJson(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_extrasOfReturnsWhatOnlyTheGeneratedHas():
    extras = gapReport.extrasOf(realSource=REAL_JAVA, generatedSource=GENERATED_JAVA)
    assert extras["imports"] == ["import java.util.Map;"]
    assert [member["kind"] for member in extras["members"]] == ["signature"]
    assert "extra" in extras["members"][0]["text"]


def test_extrasOfIsEmptyForEqualSources():
    extras = gapReport.extrasOf(realSource=REAL_JAVA, generatedSource=REAL_JAVA)
    assert extras == {"members": [], "imports": []}


def test_buildSliceSplitsGapsByClassAndKeepsWaivedReason():
    entry = entryOf(
        gaps=[("signature", SIG_B), ("constructor", CTOR), ("signature", SIG_A)],
        waived=[{"kind": "signature", "text": WAIVED_SIG, "reason": REASON}],
    )
    result = sliceOf(entry=entry)
    assert result["resource"] == "Foo"
    assert result["measured"] is True
    assert result["sha"] == SHA
    assert result["gaps"] == [
        {"key": f"Foo | constructor | {CTOR}", "kind": "constructor", "text": CTOR, "class": "muda-api-publica"},
        {"key": f"Foo | signature | {SIG_A}", "kind": "signature", "text": SIG_A, "class": "sem-fonte"},
        {"key": f"Foo | signature | {SIG_B}", "kind": "signature", "text": SIG_B, "class": "sem-classificar"},
    ]
    assert result["waived"] == [
        {"key": f"Foo | signature | {WAIVED_SIG}", "kind": "signature", "text": WAIVED_SIG, "reason": REASON}
    ]
    assert result["extras"] is None


def test_buildSliceUnmeasured():
    result = sliceOf(entry=entryOf(similarity=None, state="nao gerado"))
    assert result["measured"] is False
    assert result["similarity"] is None
    assert result["state"] == "nao gerado"


def test_consolidateCountsByClassAndKeepsExtrasOutOfTheTotal():
    blocked = {("signature", SIG_B): "bloqueado-por-dependencia"}
    classes = {**CLASSES, "Bar": blocked}
    slices = [
        sliceOf(
            entry=entryOf(gaps=[("signature", SIG_A), ("signature", SIG_B)]),
            classes=classes,
            extras={"members": [{"kind": "signature", "text": "x"}], "imports": ["import a.B;", "import a.C;"]},
        ),
        sliceOf(entry=entryOf(resource="Bar", gaps=[("signature", SIG_B)]), classes=classes),
        sliceOf(entry=entryOf(resource="Baz", similarity=None, state="nao gerado"), classes=classes),
    ]
    result = gapReport.consolidate(slices=slices, classes=classes)
    assert result["resources"] == 3
    assert result["measured"] == 2
    assert result["gapsTotal"] == 3
    assert result["byClass"] == {
        "bloqueado-por-dependencia": 1,
        "muda-api-publica": 0,
        "sem-classificar": 1,
        "sem-fonte": 1,
        "tem-fonte": 0,
    }
    assert result["extras"] == {"members": 1, "imports": 2}
    assert [item["resource"] for item in result["slices"]] == ["Bar", "Baz", "Foo"]


def test_consolidateCountsWaivedApartFromTheGapsTotal():
    waived = [{"kind": "signature", "text": WAIVED_SIG, "reason": REASON}]
    slices = [
        sliceOf(entry=entryOf(gaps=[("signature", SIG_A)], waived=waived * 2)),
        sliceOf(entry=entryOf(resource="Bar", waived=waived)),
    ]
    result = gapReport.consolidate(slices=slices, classes=CLASSES)
    assert result["gapsTotal"] == 1
    assert result["waivedTotal"] == 3


def test_consolidateIsIndependentOfInputOrder():
    slices = [withGaps(resource="A", count=1), withGaps(resource="B", count=2), withGaps(resource="C", count=0)]
    results = {
        json.dumps(gapReport.consolidate(slices=list(order), classes={}))
        for order in itertools.permutations(slices)
    }
    assert len(results) == 1


def test_consolidateReportsObsoleteClassesOnlyForMeasuredResources():
    classes = {
        "Foo": {("signature", SIG_A): "sem-fonte", ("signature", SIG_B): "sem-fonte"},
        "Bar": {("signature", SIG_A): "sem-fonte"},
    }
    slices = [
        sliceOf(entry=entryOf(gaps=[("signature", SIG_A)]), classes=classes),
        sliceOf(entry=entryOf(resource="Bar", similarity=None, state="nao gerado"), classes=classes),
    ]
    result = gapReport.consolidate(slices=slices, classes=classes)
    assert result["obsoleteClasses"] == [f"Foo | signature | {SIG_B}"]


def test_consolidateRejectsDuplicatedSlice():
    with pytest.raises(ValueError, match="fatia duplicada: Foo"):
        gapReport.consolidate(slices=[withGaps(), withGaps()], classes={})


DANGEROUS = [
    "@usuario",
    "`code`",
    "```",
    gapReport.MARKER_END,
    gapReport.MARKER_START,
    "<script>alert(1)</script>",
    "linha1\nlinha2\t\r",
    "acentuação ✓ 日本",
    "{{x}} ![i](http://e/x) #1 ~~s~~ 'q' \"d\" \\ /",
]


@pytest.mark.parametrize("raw", DANGEROUS)
def test_safeTextLeavesOnlyAllowedCharacters(raw):
    cleaned = gapReport.safeText(text=raw)
    assert all(character.isascii() and (character.isalnum() or character in " _.,()<>?[]|:-;=\"") for character in cleaned)
    assert "```" not in cleaned
    assert "@" not in cleaned
    assert "!--" not in cleaned


REAL_KEYS = [
    "require ausente: import com.starkbank.utils.SubResource;",
    'field ausente: static ClassData data = new ClassData(Purchase.class, "Purchase");',
]


def test_safeTextKeepsStableKeyCharacters():
    key = f"Foo | signature | {SIG_A}"
    assert gapReport.safeText(text=key) == key


@pytest.mark.parametrize("key", REAL_KEYS)
def test_safeTextKeepsSemicolonEqualsAndQuotesOfRealKeys(key):
    assert gapReport.safeText(text=key) == key


def test_renderSectionShowsRealKeysLiterally():
    gaps = [("require", REAL_KEYS[0]), ("field", REAL_KEYS[1])]
    section = gapReport.renderSection(slice=sliceOf(entry=entryOf(gaps=gaps), classes={}))
    for key in REAL_KEYS:
        assert key in section


def test_safeTextLimitsLength():
    cleaned = gapReport.safeText(text="a" * 1000)
    assert cleaned == "a" * 300 + "..."


HOSTILE_PROSE = [
    '<img src="x.png" onerror="a=1;b">',
    "<script>alert(1)</script>",
    "[x](http:e.com)",
    "<details><summary>x</summary>",
]


def test_safeProseLeavesOnlyTheStrictSet():
    cleaned = gapReport.safeProse(text='aZ09 _.,:-<>()[]|?;="\n')
    assert cleaned == "aZ09 _.,:-" + "_" * 12


def test_safeProseKeepsNormalProseValues():
    for value in [SHA, "nao gerado", "ausente no real", "erro", "Foo", "bloqueado-por-dependencia"]:
        assert gapReport.safeProse(text=value) == value


def test_safeProseLimitsLength():
    assert gapReport.safeProse(text="a" * 1000) == "a" * 300 + "..."


def proseLinesOf(section):
    lines, fenced = [], False
    for line in section.split("\n"):
        if line == "```":
            fenced = not fenced
            continue
        if fenced or line in (gapReport.MARKER_START, gapReport.MARKER_END):
            continue
        lines.append(line)
    return lines


@pytest.mark.parametrize("raw", HOSTILE_PROSE)
@pytest.mark.parametrize("measured", [True, False])
def test_renderSectionProseOutsideTheFenceHasNoMarkupCharacters(raw, measured):
    slice = sliceOf(entry=entryOf(resource=raw, similarity=0.9 if measured else None, state=raw, gaps=[("signature", SIG_A)]))
    slice["sha"] = raw
    section = gapReport.renderSection(slice=slice)
    for line in proseLinesOf(section):
        assert not any(character in line for character in '<>"=[]'), line
    for line in proseLinesOf(section):
        if line.startswith(("Recurso ", "nao medido:")):
            assert "(" not in line and ")" not in line, line
    assert section.count(gapReport.MARKER_START) == 1
    assert section.count(gapReport.MARKER_END) == 1
    assert section.count("```") == (2 if measured else 0)


def test_renderSectionOfANormalSliceIsUnchanged():
    expected = (
        "<!-- sdk-automation:gaps:start -->\n"
        "### Nao gerado / completar a mao\n"
        "Recurso Foo medido contra o sdk-java real em abc1234; so informa o revisor, nao altera o gate.\n"
        "Gaps: 3 (bloqueado-por-dependencia 0, muda-api-publica 1, sem-classificar 1, sem-fonte 1, tem-fonte 0)\n"
        "Dispensados (escritos a mao no real): 1\n"
        "INFO extras, fora da soma: membros 1, imports 1\n"
        "```\n"
        "gaps:\n"
        "[muda-api-publica]\n"
        "Foo | constructor | constructor ausente: public Foo(String id)\n"
        "[sem-classificar]\n"
        "Foo | signature | signature ausente: public static Foo b()\n"
        "[sem-fonte]\n"
        "Foo | signature | signature ausente: public static Foo a()\n"
        "dispensados:\n"
        "Foo | signature | signature ausente: public static Foo handmade()\n"
        "extras a mais:\n"
        "Foo | signature | public static Foo extra()\n"
        "Foo | import | import java.util.Map;\n"
        "```\n"
        "<!-- sdk-automation:gaps:end -->"
    )
    assert sectionOf() == expected


def test_renderSectionOfAnUnmeasuredSliceIsUnchanged():
    section = gapReport.renderSection(slice=sliceOf(entry=entryOf(similarity=None, state="ausente no real")))
    assert section == (
        "<!-- sdk-automation:gaps:start -->\n"
        "### Nao gerado / completar a mao\n"
        "Recurso Foo medido contra o sdk-java real em abc1234; so informa o revisor, nao altera o gate.\n"
        "nao medido: ausente no real\n"
        "<!-- sdk-automation:gaps:end -->"
    )


def sectionOf(**overrides):
    gaps = [("signature", SIG_A), ("constructor", CTOR), ("signature", SIG_B)]
    waived = [{"kind": "signature", "text": WAIVED_SIG, "reason": REASON}]
    extras = {"members": [{"kind": "signature", "text": "public static Foo extra()"}], "imports": ["import java.util.Map;"]}
    entry = entryOf(gaps=gaps, waived=waived)
    return gapReport.renderSection(slice=sliceOf(entry=entry, extras=extras, **overrides))


def test_renderSectionHasMarkersCountsAndSingleFencedBlock():
    section = sectionOf()
    assert section.startswith(gapReport.MARKER_START)
    assert section.endswith(gapReport.MARKER_END)
    assert "### Nao gerado / completar a mao" in section
    assert SHA in section
    assert "sdk-java real" in section
    assert "Gaps: 3" in section
    assert "muda-api-publica 1" in section
    assert "sem-fonte 1" in section
    assert "sem-classificar 1" in section
    assert "tem-fonte 0" in section
    assert "bloqueado-por-dependencia 0" in section
    assert "Dispensados (escritos a mao no real): 1" in section
    assert "fora da soma" in section
    assert "membros 1" in section
    assert "imports 1" in section
    assert section.count("```") == 2
    assert "Foo | signature | " in section
    assert "Foo | import | import java.util.Map;" in section


def test_renderSectionNeverLeaksReason():
    assert "REASON-SEGREDO" not in sectionOf()


def test_renderSectionWithoutGaps():
    section = gapReport.renderSection(slice=sliceOf(entry=entryOf(state="sem gaps")))
    assert "Nenhum gap contra o sdk-java real." in section
    assert "Gaps: 0" in section
    assert "```" not in section


def test_renderSectionOmitsExtrasLineWhenUnknown():
    section = gapReport.renderSection(slice=sliceOf(entry=entryOf(gaps=[("signature", SIG_A)])))
    assert "fora da soma" not in section


def test_renderSectionUnmeasuredIsOnlyTheHeaderAndTheState():
    section = gapReport.renderSection(slice=sliceOf(entry=entryOf(similarity=None, state="nao gerado")))
    assert "nao medido: nao gerado" in section
    assert "Gaps:" not in section
    assert "```" not in section
    assert section.startswith(gapReport.MARKER_START)
    assert section.endswith(gapReport.MARKER_END)


def test_renderSectionLimitsEachListToFiftyItems():
    section = gapReport.renderSection(slice=withGaps(count=80))
    assert "Gaps: 80" in section
    assert section.count("signature ausente: m") == 50
    assert "... +30 omitidos" in section


def test_renderSectionStaysUnderTheBodyLimitInTheWorstCase():
    huge = "x" * 5000
    waived = [{"kind": "signature", "text": f"{huge}{index}", "reason": REASON} for index in range(200)]
    gaps = [("signature", f"{huge}{index}") for index in range(200)]
    extras = {
        "members": [{"kind": "signature", "text": f"{huge}{index}"} for index in range(200)],
        "imports": [f"import {huge}{index};" for index in range(200)],
    }
    section = gapReport.renderSection(slice=sliceOf(entry=entryOf(gaps=gaps, waived=waived), classes={}, extras=extras))
    assert len(section) <= 60000


@pytest.mark.parametrize("raw", DANGEROUS)
def test_renderSectionNeverProducesASecondMarker(raw):
    entry = entryOf(
        resource="Foo",
        state=raw,
        detail=raw,
        gaps=[("signature", raw)],
        waived=[{"kind": "signature", "text": raw, "reason": raw}],
    )
    extras = {"members": [{"kind": "signature", "text": raw}], "imports": [raw]}
    section = gapReport.renderSection(slice=sliceOf(entry=entry, extras=extras, classes={}))
    assert section.count(gapReport.MARKER_START) == 1
    assert section.count(gapReport.MARKER_END) == 1
    assert "@" not in section
    assert section.count("```") == 2


def test_mergeBodyAppendsToABodyWithoutMarkers():
    section = sectionOf()
    assert gapReport.mergeBody(body="## Description\ntext\n\n", section=section) == f"## Description\ntext\n\n{section}\n"


def test_mergeBodyIntoEmptyBodyIsTheSection():
    section = sectionOf()
    assert gapReport.mergeBody(body="", section=section) == f"{section}\n"


def test_mergeBodyReplacesTheRegionAndPreservesTheRest():
    old = f"{gapReport.MARKER_START}\nvelho\n{gapReport.MARKER_END}"
    body = f"antes\n\n  {old}\n\ndepois é\t\n"
    section = sectionOf()
    assert gapReport.mergeBody(body=body, section=section) == f"antes\n\n  {section}\n\ndepois é\t\n"


def test_mergeBodyIsIdempotent():
    section = sectionOf()
    once = gapReport.mergeBody(body="corpo\n", section=section)
    assert gapReport.mergeBody(body=once, section=section) == once
    other = sectionOf(classes={})
    twice = gapReport.mergeBody(body=once, section=other)
    assert gapReport.mergeBody(body=twice, section=other) == twice
    assert twice.count(gapReport.MARKER_START) == 1


@pytest.mark.parametrize("body", [
    f"a {gapReport.MARKER_START} b",
    f"a {gapReport.MARKER_END} b",
    f"{gapReport.MARKER_END} x {gapReport.MARKER_START}",
    f"{gapReport.MARKER_START}{gapReport.MARKER_END}{gapReport.MARKER_START}{gapReport.MARKER_END}",
    f"{gapReport.MARKER_START}{gapReport.MARKER_START}{gapReport.MARKER_END}",
])
def test_mergeBodyRejectsMalformedMarkers(body):
    with pytest.raises(ValueError, match="marcador"):
        gapReport.mergeBody(body=body, section=sectionOf())


@pytest.mark.parametrize("section", [
    "sem marcadores",
    f"{gapReport.MARKER_START}\nx",
    f"x\n{gapReport.MARKER_START}\n{gapReport.MARKER_END}",
    f"{gapReport.MARKER_START}\n{gapReport.MARKER_END}\nx",
    f"{gapReport.MARKER_START}\n{gapReport.MARKER_START}\n{gapReport.MARKER_END}",
])
def test_mergeBodyRejectsMalformedSection(section):
    with pytest.raises(ValueError, match="secao"):
        gapReport.mergeBody(body="corpo", section=section)


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "real"
    generated = tmp_path / "gen"
    for root, text in ((source, REAL_JAVA), (generated, GENERATED_JAVA)):
        directory = root / "src/main/java/com/starkbank"
        directory.mkdir(parents=True)
        (directory / "Foo.java").write_text(text, encoding="utf-8")
    classes = tmp_path / "classes.yaml"
    classes.write_text(
        "MerchantPurchase:\n  - kind: signature\n    text: \"signature ausente: public static X gone()\"\n    class: sem-fonte\n",
        encoding="utf-8",
    )
    entries = [
        entryOf(gaps=[("signature", SIG_A), ("signature", SIG_B)], waived=[{"kind": "signature", "text": WAIVED_SIG, "reason": REASON}]),
        entryOf(resource="MerchantPurchase", similarity=None, state="nao gerado"),
    ]
    report = writeJson(tmp_path / "report.json", reportOf(entries))
    return {"tmp": tmp_path, "source": source, "generated": generated, "classes": classes, "report": report}


def runSlice(workspace, resource="Foo", out="slices/Foo.json", report=None, source=None):
    outPath = workspace["tmp"] / out
    outPath.parent.mkdir(exist_ok=True)
    code = gapReport.main([
        "slice",
        "--report", str(report or workspace["report"]),
        "--resource", resource,
        "--generated", str(workspace["generated"]),
        "--source", str(source or workspace["source"]),
        "--out", str(outPath),
        "--classes", str(workspace["classes"]),
    ])
    return code, outPath


def test_cliEndToEndExitsZeroEvenWithGaps(workspace, capsys):
    code, fooPath = runSlice(workspace)
    assert code == 0
    code, _ = runSlice(workspace, resource="MerchantPurchase", out="slices/MerchantPurchase.json")
    assert code == 0

    consolidated = workspace["tmp"] / "consolidated.json"
    assert gapReport.main([
        "consolidate", "--slices", str(fooPath.parent), "--out", str(consolidated), "--classes", str(workspace["classes"]),
    ]) == 0
    data = json.loads(consolidated.read_text(encoding="utf-8"))
    assert data["gapsTotal"] == 2
    assert data["measured"] == 1
    assert data["extras"] == {"members": 1, "imports": 1}
    assert data["obsoleteClasses"] == []

    capsys.readouterr()
    assert gapReport.main(["section", "--slice", str(fooPath)]) == 0
    section = capsys.readouterr().out.rstrip("\n")
    assert section.startswith(gapReport.MARKER_START)
    assert section.endswith(gapReport.MARKER_END)
    assert "[INFO]" not in section
    assert "REASON-SEGREDO" not in section

    bodyPath = workspace["tmp"] / "body.md"
    sectionPath = workspace["tmp"] / "section.md"
    bodyPath.write_text("## Description\ntexto\n", encoding="utf-8")
    sectionPath.write_text(section, encoding="utf-8")
    assert gapReport.main(["merge-body", "--body", str(bodyPath), "--section", str(sectionPath)]) == 0
    merged = capsys.readouterr().out
    assert merged == f"## Description\ntexto\n\n{section}\n"


def test_cliSliceIsByteIdenticalAcrossRuns(workspace):
    _, first = runSlice(workspace, out="a.json")
    firstBytes = first.read_bytes()
    _, second = runSlice(workspace, out="b.json")
    assert second.read_bytes() == firstBytes
    assert firstBytes.endswith(b"}\n")


def test_cliSliceHasNullExtrasWhenGeneratedIsMissing(workspace):
    (workspace["generated"] / "src/main/java/com/starkbank/Foo.java").unlink()
    code, path = runSlice(workspace)
    assert code == 0
    assert json.loads(path.read_text(encoding="utf-8"))["extras"] is None


def test_cliSliceFailsOnUnknownResource(workspace, capsys):
    code, _ = runSlice(workspace, resource="Nope")
    assert code == 2
    assert "[ERROR]" in capsys.readouterr().out


def test_cliSliceFailsWhenRealRepositoryIsMissing(workspace, capsys):
    code, _ = runSlice(workspace, source=workspace["tmp"] / "inexistente")
    assert code == 2
    assert "[ERROR]" in capsys.readouterr().out


def test_cliSliceFailsOnInvalidReportJson(workspace, capsys):
    workspace["report"].write_text("{nao e json", encoding="utf-8")
    code, _ = runSlice(workspace)
    assert code == 2
    assert "[ERROR]" in capsys.readouterr().out


def test_cliConsolidateFailsOnMalformedSlice(workspace, capsys):
    directory = workspace["tmp"] / "bad"
    directory.mkdir()
    writeJson(directory / "x.json", {"resource": "X"})
    code = gapReport.main(["consolidate", "--slices", str(directory), "--out", str(workspace["tmp"] / "o.json")])
    assert code == 2
    assert "[ERROR]" in capsys.readouterr().out


def test_cliConsolidateFailsOnMissingDirectory(workspace, capsys):
    out = workspace["tmp"] / "o.json"
    code = gapReport.main(["consolidate", "--slices", str(workspace["tmp"] / "nope"), "--out", str(out)])
    assert code == 2
    assert "nao e um diretorio" in capsys.readouterr().out
    assert not out.exists()


def test_cliConsolidateFailsOnDirectoryWithoutSlices(workspace, capsys):
    directory = workspace["tmp"] / "empty"
    directory.mkdir()
    out = workspace["tmp"] / "o.json"
    code = gapReport.main(["consolidate", "--slices", str(directory), "--out", str(out)])
    assert code == 2
    assert "nenhuma fatia *.json" in capsys.readouterr().out
    assert not out.exists()


def test_cliMergeBodyFailsOnMalformedBody(workspace, capsys):
    _, fooPath = runSlice(workspace)
    gapReport.main(["section", "--slice", str(fooPath)])
    sectionPath = workspace["tmp"] / "section.md"
    sectionPath.write_text(sectionOf(), encoding="utf-8")
    bodyPath = workspace["tmp"] / "body.md"
    bodyPath.write_text(f"x {gapReport.MARKER_START} y", encoding="utf-8")
    capsys.readouterr()
    assert gapReport.main(["merge-body", "--body", str(bodyPath), "--section", str(sectionPath)]) == 2
    assert "[ERROR]" in capsys.readouterr().out


def test_cliSliceFailsOnReportWithoutResources(workspace, capsys):
    writeJson(workspace["report"], {"sha": SHA})
    code, _ = runSlice(workspace)
    assert code == 2
    assert "[ERROR]" in capsys.readouterr().out
