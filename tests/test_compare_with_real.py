import os
import json
import hashlib
import importlib.util
from pathlib import Path

import pytest

from conftest import TOOLS_DIR, REPO_ROOT, runWithClosedStdout

spec = importlib.util.spec_from_file_location("compare_with_real", TOOLS_DIR / "compare-with-real.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)

MAIN = "src/main/java/com/starkbank"
REFERENCE_JAVA = REPO_ROOT / "_references/sdk-java"

REAL = """\
package com.starkbank;

import java.util.List;
import java.util.Map;

/**
 * Widget object.
 * Stores a widget.
 */
public final class Widget extends Resource {

    public String name;
    public Integer amount;

    /**
     * Widget constructor.
     */
    public Widget(String name, Integer amount) {
        super(null);
        this.name = name;
        this.amount = amount;
    }

    public Widget(String id) {
        super(id);
    }

    /**
     * Retrieve a widget.
     */
    public static Widget get(String id) throws Exception {
        return null;
    }

    public static List<Widget> query() throws Exception {
        return null;
    }
}
"""

SPLIT = REAL.replace("Widget", "Split")


@pytest.fixture(autouse=True)
def noRealWaivers(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "WAIVERS_FILE", tmp_path / "no-waivers.yaml")
    monkeypatch.setattr(compare, "NOISE_FILE", tmp_path / "no-noise.yaml")


def plant(root: Path, resource: str, source: str) -> Path:
    path = root / MAIN / f"{resource}.java"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def trees(tmp_path, real=None, generated=None, resource="Widget"):
    generatedRoot, sourceRoot = tmp_path / "generated", tmp_path / "real"
    (generatedRoot / MAIN).mkdir(parents=True, exist_ok=True)
    (sourceRoot / MAIN).mkdir(parents=True, exist_ok=True)
    if real is not None:
        plant(sourceRoot, resource, real)
    if generated is not None:
        plant(generatedRoot, resource, generated)
    return generatedRoot, sourceRoot


def measureOf(tmp_path, real, generated):
    generatedRoot, sourceRoot = trees(tmp_path, real=real, generated=generated)
    return compare.measureResource("Widget", generatedRoot, sourceRoot)


def digest(root: Path) -> dict:
    return {
        str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def result(resource, gaps=0, similarity=1.0, state="sem gaps"):
    return {
        "resource": resource,
        "state": state,
        "detail": "",
        "gaps": [["constructor", "constructor ausente: x"]] * gaps,
        "similarity": similarity,
        "importsMissing": [],
        "importsExtra": [],
        "importsInOrder": True,
        "memberOrderEqual": True,
        "docEqual": 1.0,
    }


def test_identicalSourceIsEqualWithPerfectMeasures(tmp_path):
    measured = measureOf(tmp_path, REAL, REAL)
    assert measured["state"] == "igual"
    assert measured["gaps"] == []
    assert measured["similarity"] == 1.0
    assert measured["importsMissing"] == [] and measured["importsExtra"] == []
    assert measured["importsInOrder"] is True
    assert measured["memberOrderEqual"] is True
    assert measured["docEqual"] == 1.0


def test_missingConstructorIsAGapWithKindFirst():
    generated = REAL.replace("    public Widget(String id) {\n        super(id);\n    }\n", "")
    gaps = compare.gapsOf(realSource=REAL, generatedSource=generated, filePath="Widget.java")
    assert [kind for kind, _ in gaps] == ["constructor"]
    assert gaps[0][1].startswith("constructor ausente")


def test_removedSignatureIsAccused():
    query = "    public static List<Widget> query() throws Exception {\n        return null;\n    }\n"
    generated = REAL.replace(query, "")
    gaps = compare.gapsOf(realSource=REAL, generatedSource=generated, filePath="Widget.java")
    assert ("signature", "signature ausente: public static List<Widget> query()") in gaps


def test_fieldWithDifferentTypeIsDivergent():
    generated = REAL.replace("public Integer amount;", "public Long amount;")
    gaps = compare.gapsOf(realSource=REAL, generatedSource=generated, filePath="Widget.java")
    fields = [message for kind, message in gaps if kind == "field"]
    assert len(fields) == 1
    assert fields[0].startswith("field divergente")


def test_gapsOnlyStateWhenGapsExist(tmp_path):
    generated = REAL.replace("public Integer amount;", "public Long amount;")
    measured = measureOf(tmp_path, REAL, generated)
    assert measured["state"] == "com gaps"
    assert measured["similarity"] < 1.0


def test_differentSourceHasSimilarityBelowOne():
    assert compare.similarityOf(realSource=REAL, generatedSource=REAL + "\n// x\nint y;\n") < 1.0
    assert compare.similarityOf(realSource=REAL, generatedSource=REAL.replace("null;", "null;  ")) == 1.0
    assert compare.similarityOf(realSource=REAL, generatedSource="class A {}") < 0.2


def test_similarityIgnoresCommentsAndBlankLines():
    noisy = REAL.replace("    public String name;", "\n    // nota\n    public String name;\n")
    assert compare.similarityOf(realSource=REAL, generatedSource=noisy) == 1.0


def test_missingAndExtraImportsAreListedSorted():
    generated = REAL.replace("import java.util.Map;", "import java.util.Set;\nimport java.util.Date;")
    imports = compare.importsOf(realSource=REAL, generatedSource=generated)
    assert imports["missing"] == ["import java.util.Map;"]
    assert imports["extra"] == ["import java.util.Date;", "import java.util.Set;"]


def test_importsInOrderDetectsSwappedCommonImports():
    swapped = REAL.replace(
        "import java.util.List;\nimport java.util.Map;",
        "import java.util.Map;\nimport java.util.List;",
    )
    assert compare.importsOf(realSource=REAL, generatedSource=swapped)["inOrder"] is False
    assert compare.importsOf(realSource=REAL, generatedSource=REAL)["inOrder"] is True


def test_importsInOrderIgnoresMissingOnes():
    generated = REAL.replace("import java.util.List;\n", "")
    assert compare.importsOf(realSource=REAL, generatedSource=generated)["inOrder"] is True


def test_membersInAnotherOrderAreDetected(tmp_path):
    generated = REAL.replace(
        "    public static List<Widget> query() throws Exception {\n        return null;\n    }\n", ""
    ).replace(
        "    public static Widget get(String id) throws Exception {",
        "    public static List<Widget> query() throws Exception {\n        return null;\n    }\n\n"
        "    public static Widget get(String id) throws Exception {",
    )
    assert compare.memberOrderEqualOf(realSource=REAL, generatedSource=generated) is False
    measured = measureOf(tmp_path, REAL, generated)
    assert measured["gaps"] == []
    assert measured["state"] == "sem gaps"
    assert measured["memberOrderEqual"] is False


def test_memberOrderIgnoresMembersMissingOnOneSide():
    generated = REAL.replace("    public Widget(String id) {\n        super(id);\n    }\n", "")
    assert compare.memberOrderEqualOf(realSource=REAL, generatedSource=generated) is True


def test_docFractionCountsRealBlocksWithIdenticalGeneratedBlock():
    generated = REAL.replace("Retrieve a widget.", "Fetch it.")
    assert compare.docEqualOf(realSource=REAL, generatedSource=generated) == round(2 / 3, 4)
    assert compare.docEqualOf(realSource=REAL, generatedSource=REAL) == 1.0


def test_docComparisonNormalizesStarsAndSpaces():
    generated = REAL.replace(" * Retrieve a widget.", "   *    Retrieve   a widget.")
    assert compare.docEqualOf(realSource=REAL, generatedSource=generated) == 1.0


def test_docIsOneWhenNeitherSideHasBlocks():
    assert compare.docEqualOf(realSource="class A {}", generatedSource="class B {}") == 1.0


def test_differentDocMakesStateSemGaps(tmp_path):
    measured = measureOf(tmp_path, REAL, REAL.replace("Retrieve a widget.", "Fetch it."))
    assert measured["gaps"] == []
    assert measured["state"] == "sem gaps"
    assert measured["docEqual"] < 1.0


def test_missingGeneratedFileIsNaoGerado(tmp_path):
    measured = measureOf(tmp_path, REAL, None)
    assert measured["state"] == "nao gerado"
    assert measured["similarity"] is None
    assert measured["gaps"] == []


def test_missingRealFileIsAusenteNoReal(tmp_path):
    measured = measureOf(tmp_path, None, REAL)
    assert measured["state"] == "ausente no real"
    assert measured["similarity"] is None
    assert measured["memberOrderEqual"] is None


def test_unreadableFileBecomesErrorAndOthersAreMeasured(tmp_path, capsys):
    if os.geteuid() == 0:
        pytest.skip("root le qualquer arquivo")
    generatedRoot, sourceRoot = trees(tmp_path, real=REAL, generated=REAL)
    plant(sourceRoot, "Gadget", REAL.replace("Widget", "Gadget"))
    blocked = plant(generatedRoot, "Gadget", REAL.replace("Widget", "Gadget"))
    blocked.chmod(0)
    try:
        measured = [
            compare.measureResource(name, generatedRoot, sourceRoot) for name in ("Gadget", "Widget")
        ]
    finally:
        blocked.chmod(0o644)
    assert measured[0]["state"] == "erro"
    assert measured[0]["detail"].startswith("gerado: ")
    assert "Gadget.java" in measured[0]["detail"]
    report = compare.buildReport(results=measured, sha="x")
    assert "Gadget.java" in next(e for e in report["resources"] if e["resource"] == "Gadget")["detail"]
    assert any("Gadget.java" in line and "erro" in line for line in compare.render(report).splitlines())
    assert measured[1]["state"] == "igual"


def test_reportOrdersByGapsThenSimilarityThenNameAndAggregates():
    results = [
        result("Zeta", gaps=0, similarity=0.5),
        result("Alpha", gaps=0, similarity=0.9),
        result("Beta", gaps=2, similarity=0.9, state="com gaps"),
        result("Gamma", gaps=0, similarity=0.5),
        result("Delta", gaps=0, similarity=1.0, state="igual"),
        result("Eps", gaps=0, similarity=None, state="nao gerado"),
    ]
    report = compare.buildReport(results=results, sha="abc123")
    assert [entry["resource"] for entry in report["resources"]] == ["Beta", "Gamma", "Zeta", "Alpha", "Delta", "Eps"]
    assert report["sha"] == "abc123"
    assert report["total"] == 6
    assert report["compared"] == 5
    assert report["zeroGaps"] == 4
    assert report["identical"] == 1
    assert report["meanSimilarity"] == round((0.5 + 0.9 + 0.9 + 0.5 + 1.0) / 5, 4)
    assert report["gapsByKind"] == {"constructor": 2}
    first = report["resources"][0]
    assert first["gapCount"] == 2
    assert first["gaps"][0] == ["constructor", "constructor ausente: x"]
    assert set(first) == {
        "resource", "state", "detail", "gaps", "gapCount", "waived", "waivedCount", "noiseWaived", "noiseWaivedCount",
        "similarity", "importsMissing", "importsExtra", "importsInOrder", "memberOrderEqual", "docEqual",
    }


def test_reportWithoutComparedHasZeroMean():
    report = compare.buildReport(results=[result("A", similarity=None, state="nao gerado")], sha="x")
    assert report["compared"] == 0
    assert report["meanSimilarity"] == 0.0
    assert report["zeroGaps"] == 0


def test_gapsByKindIsOrderedByCountThenName():
    results = [
        {**result("A", gaps=0), "gaps": [["signature", "s"], ["field", "f"], ["field", "f2"]]},
        {**result("B", gaps=0), "gaps": [["constructor", "c"]]},
    ]
    report = compare.buildReport(results=results, sha="x")
    assert list(report["gapsByKind"].items()) == [("field", 2), ("constructor", 1), ("signature", 1)]


def test_renderShowsHeaderSummaryTableAndGapsByKind():
    results = [
        result("Beta", gaps=2, similarity=0.5, state="com gaps"),
        result("Alpha", gaps=0, similarity=1.0, state="igual"),
    ]
    text = compare.render(compare.buildReport(results=results, sha="abc123"))
    lines = text.splitlines()
    assert lines[0] == "[INFO] gerado x real (sdk-java @ abc123)"
    assert lines[1] == "[INFO] 2 comparados de 2; 1 com 0 gaps; 1 idênticos; similaridade média 75.0%"
    assert lines[3].split() == ["recurso", "gaps", "similar.", "estado"]
    assert lines[3].index("estado") == lines[4].index("com gaps")
    assert any(line.split() == ["Beta", "2", "50.0%", "com", "gaps"] for line in lines)
    assert any(line.split() == ["Alpha", "0", "100.0%", "igual"] for line in lines)
    assert "[INFO] gaps por tipo:" in lines
    assert any(line.split() == ["constructor", "2"] for line in lines)
    assert "constructor ausente" not in text


def test_renderShowsDashForUnmeasured():
    text = compare.render(compare.buildReport(results=[result("A", similarity=None, state="nao gerado")], sha="x"))
    assert any(line.split() == ["A", "0", "-", "nao", "gerado"] for line in text.splitlines())


def test_mainMeasuresAndExitsZeroWithGaps(tmp_path, capsys):
    generated = SPLIT.replace("public Integer amount;", "public Long amount;")
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=generated, resource="Split")
    jsonPath = tmp_path / "out.json"
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--json", str(jsonPath),
        "Split",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "1 comparados de 1; 0 com 0 gaps; 0 idênticos" in out
    report = json.loads(jsonPath.read_text(encoding="utf-8"))
    assert report["resources"][0]["resource"] == "Split"
    assert report["resources"][0]["gapCount"] == 1


def test_mainRepeatedResourceIsMeasuredOnce(tmp_path, capsys):
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=SPLIT, resource="Split")
    code = compare.main(["--generated", str(generatedRoot), "--source", str(sourceRoot), "Split", "Split"])
    assert code == 0
    assert "1 comparados de 1" in capsys.readouterr().out


def test_mainDefaultsToAllSchemaResources(tmp_path, capsys):
    generatedRoot, sourceRoot = trees(tmp_path)
    code = compare.main(["--generated", str(generatedRoot), "--source", str(sourceRoot)])
    out = capsys.readouterr().out
    assert code == 0
    assert "0 comparados de 42" in out


@pytest.mark.parametrize("layout", ["missing", "without-main"])
def test_mainInvalidGeneratedIsUsageError(tmp_path, capsys, layout):
    _, sourceRoot = trees(tmp_path, real=REAL)
    generatedRoot = tmp_path / "nowhere"
    if layout == "without-main":
        generatedRoot = tmp_path / "bare"
        generatedRoot.mkdir()
    code = compare.main(["--generated", str(generatedRoot), "--source", str(sourceRoot), "Split"])
    assert code == 2
    assert "[ERROR]" in capsys.readouterr().out


def test_mainMissingSourceIsUsageError(tmp_path, capsys):
    generatedRoot, _ = trees(tmp_path, generated=REAL)
    code = compare.main(["--generated", str(generatedRoot), "--source", str(tmp_path / "nowhere")])
    assert code == 2
    assert "[ERROR]" in capsys.readouterr().out


def test_mainUnknownResourceIsUsageError(tmp_path, capsys):
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=SPLIT, resource="Split")
    code = compare.main(["--generated", str(generatedRoot), "--source", str(sourceRoot), "Nope"])
    assert code == 2
    out = capsys.readouterr().out
    assert "[ERROR]" in out and "Nope" in out


def test_mainJsonInMissingDirectoryIsUsageError(tmp_path, capsys):
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=SPLIT, resource="Split")
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(sourceRoot),
        "--json", str(tmp_path / "nowhere/out.json"),
        "Split",
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "--json" in out
    assert "comparados" not in out


def test_mainNeverWritesToEitherTree(tmp_path, capsys):
    generatedRoot, sourceRoot = trees(
        tmp_path,
        real=SPLIT,
        generated=SPLIT.replace("Retrieve", "Fetch"),
        resource="Split",
    )
    before = (digest(generatedRoot), digest(sourceRoot))
    code = compare.main(["--generated", str(generatedRoot), "--source", str(sourceRoot), "Split"])
    assert code == 0
    assert (digest(generatedRoot), digest(sourceRoot)) == before


@pytest.mark.skipif(not (REFERENCE_JAVA / MAIN).is_dir(), reason="sdk-java de referencia ausente")
def test_realSplitProfileAgainstItselfIsEqual(tmp_path, capsys):
    realFile = REFERENCE_JAVA / MAIN / "SplitProfile.java"
    generatedRoot = tmp_path / "generated"
    plant(generatedRoot, "SplitProfile", realFile.read_text(encoding="utf-8", errors="replace"))
    code = compare.main([
        "--generated", str(generatedRoot),
        "--source", str(REFERENCE_JAVA),
        "SplitProfile",
    ])
    assert code == 0
    measured = compare.measureResource("SplitProfile", generatedRoot, REFERENCE_JAVA)
    assert measured["state"] == "igual"
    assert measured["gaps"] == []
    assert measured["similarity"] == 1.0


def test_renderTruncatesLongErrorDetail():
    long = {**result("A", similarity=None, state="erro"), "detail": "x" * 500}
    text = compare.render(compare.buildReport(results=[long], sha="x"))
    assert max(len(line) for line in text.splitlines()) < 140


def sameWith(old, new):
    return REAL.replace(old, new)


def stateOf(tmp_path, generated):
    measured = measureOf(tmp_path, REAL, generated)
    return measured


def test_extraJavadocInGeneratedIsNotEqual(tmp_path):
    generated = sameWith(
        "    public Widget(String id) {",
        "    /**\n     * Extra.\n     */\n    public Widget(String id) {",
    )
    measured = stateOf(tmp_path, generated)
    assert measured["gaps"] == []
    assert measured["state"] == "sem gaps"
    assert measured["docEqual"] < 1.0


def test_swappedJavadocBlocksAreNotEqual(tmp_path):
    generated = (
        REAL.replace("Widget constructor.", "@@")
        .replace("Retrieve a widget.", "Widget constructor.")
        .replace("@@", "Retrieve a widget.")
    )
    measured = stateOf(tmp_path, generated)
    assert measured["state"] == "sem gaps"
    assert measured["docEqual"] < 1.0


def test_realBlockDuplicatedButOnceInGeneratedIsNotEqual(tmp_path):
    duplicated = REAL.replace(
        "    public Widget(String id) {",
        "    /**\n     * Widget constructor.\n     */\n    public Widget(String id) {",
    )
    generatedRoot, sourceRoot = trees(tmp_path, real=duplicated, generated=REAL)
    measured = compare.measureResource("Widget", generatedRoot, sourceRoot)
    assert measured["gaps"] == []
    assert measured["state"] == "sem gaps"
    assert measured["docEqual"] < 1.0


def test_realWithoutJavadocAndGeneratedWithIsNotEqual(tmp_path):
    bare = compare.re.sub(r"/\*\*.*?\*/\n", "", REAL, flags=compare.re.DOTALL)
    generatedRoot, sourceRoot = trees(tmp_path, real=bare, generated=REAL)
    measured = compare.measureResource("Widget", generatedRoot, sourceRoot)
    assert measured["gaps"] == []
    assert measured["state"] == "sem gaps"
    assert measured["docEqual"] == 0.0
    assert compare.docEqualOf(realSource="class A {}", generatedSource="class A {}") == 1.0


def test_onlyWhitespaceTabsAndCrlfStillEqual(tmp_path):
    generated = REAL.replace("    ", "\t").replace("\n", "\r\n")
    assert measureOf(tmp_path, REAL, generated)["state"] == "igual"


def test_changedStringLiteralIsNotEqual(tmp_path):
    real = REAL.replace("return null;\n    }\n\n    public static List", 'return "a";\n    }\n\n    public static List')
    generated = real.replace('"a"', '"b"')
    generatedRoot, sourceRoot = trees(tmp_path, real=real, generated=generated)
    measured = compare.measureResource("Widget", generatedRoot, sourceRoot)
    assert measured["state"] != "igual"
    assert measured["similarity"] < 1.0


def test_changedMethodBodyOnlyIsSemGaps(tmp_path):
    measured = measureOf(tmp_path, REAL, REAL.replace("this.amount = amount;", "this.amount = amount + 1;"))
    assert measured["gaps"] == []
    assert measured["state"] == "sem gaps"
    assert measured["similarity"] < 1.0
    assert measured["importsMissing"] == [] and measured["docEqual"] == 1.0


def test_fieldsInAnotherOrderBreakMemberOrder():
    generated = REAL.replace(
        "    public String name;\n    public Integer amount;",
        "    public Integer amount;\n    public String name;",
    )
    assert compare.memberOrderEqualOf(realSource=REAL, generatedSource=generated) is False


def test_bothFilesAbsentIsNaoGerado(tmp_path):
    assert measureOf(tmp_path, None, None)["state"] == "nao gerado"


def test_reportIdenticalDoesNotCountDifferentJavadoc():
    different = {**result("A", similarity=1.0, state="sem gaps"), "docEqual": 0.5}
    report = compare.buildReport(results=[different, result("B", state="igual")], sha="x")
    assert report["identical"] == 1


def test_jsonPointingToDirectoryIsUsageError(tmp_path, capsys):
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=SPLIT, resource="Split")
    code = compare.main([
        "--generated", str(generatedRoot), "--source", str(sourceRoot), "--json", str(tmp_path), "Split",
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "[ERROR] --json:" in out
    assert "comparados" not in out


def test_jsonWithoutPermissionIsUsageErrorAfterTable(tmp_path, capsys):
    if os.geteuid() == 0:
        pytest.skip("root grava em qualquer lugar")
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=SPLIT, resource="Split")
    target = tmp_path / "out.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o444)
    try:
        code = compare.main([
            "--generated", str(generatedRoot), "--source", str(sourceRoot), "--json", str(target), "Split",
        ])
    finally:
        target.chmod(0o644)
    out = capsys.readouterr().out
    assert code == 2
    assert "1 comparados de 1" in out
    assert "[ERROR] --json:" in out


def test_closedStdoutKeepsTheMeasuringExitCode(tmp_path):
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=SPLIT, resource="Split")
    result = runWithClosedStdout(
        [
            str(TOOLS_DIR / "compare-with-real.py"),
            "--generated", str(generatedRoot),
            "--source", str(sourceRoot),
            "Split",
        ],
        {},
    )
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


def test_realWithJavadocAndGeneratedWithoutIsNotEqual(tmp_path):
    bare = compare.re.sub(r"/\*\*.*?\*/\n", "", REAL, flags=compare.re.DOTALL)
    measured = measureOf(tmp_path, REAL, bare)
    assert measured["gaps"] == []
    assert measured["state"] == "sem gaps"
    assert measured["docEqual"] == 0.0


@pytest.mark.parametrize("side", ["generated", "real"])
def test_untraversableDirectoryOfOneSideIsErrorStateWithExitZero(tmp_path, capsys, side):
    if os.geteuid() == 0:
        pytest.skip("root atravessa qualquer diretório")
    generatedRoot, sourceRoot = trees(tmp_path, real=SPLIT, generated=SPLIT, resource="Split")
    jsonPath = tmp_path / "out.json"
    blocked = (generatedRoot if side == "generated" else sourceRoot) / MAIN
    blocked.chmod(0)
    try:
        code = compare.main([
            "--generated", str(generatedRoot),
            "--source", str(sourceRoot),
            "--json", str(jsonPath),
            "Split",
        ])
    finally:
        blocked.chmod(0o755)
    assert code == 0
    entry = json.loads(jsonPath.read_text(encoding="utf-8"))["resources"][0]
    assert entry["state"] == "erro"
    prefix = "gerado" if side == "generated" else "real"
    assert entry["detail"] == f"{prefix}: Permission denied: Split.java"
    assert f"erro ({prefix}: Permission denied: Split.java)" in capsys.readouterr().out


def test_generatedFileWithInvalidUtf8IsStillMeasured(tmp_path):
    generatedRoot, sourceRoot = trees(tmp_path, real=REAL, generated=REAL)
    path = generatedRoot / MAIN / "Widget.java"
    path.write_bytes(REAL.encode("utf-8").replace(b"Stores a widget.", b"Stores \xff\xfe widget."))
    measured = compare.measureResource("Widget", generatedRoot, sourceRoot)
    assert measured["state"] == "sem gaps"
    assert measured["docEqual"] < 1.0


def test_javadocStarsOnlyOnTheRealSideStillMatch():
    generated = REAL.replace(" * Retrieve a widget.", "   Retrieve a widget.")
    assert compare.docEqualOf(realSource=REAL, generatedSource=generated) == 1.0
    assert compare.docEqualOf(realSource=generated, generatedSource=REAL) == 1.0
