import re

import pytest

from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText
from test_compare_with_real import compare
from conftest import REPO_ROOT, requiresGenerator, requiresJavaSdk

NOISE_PATH = REPO_ROOT / "apis/java-unused-imports.yaml"
WAIVERS_PATH = REPO_ROOT / "apis/java-handwritten.yaml"
PIN = "f60902a"
IMPORT_LINE = re.compile(r"^\s*import ([\w.]+)\.(\w+);\s*$")
GAP_PREFIX = "require ausente: import "


def realNoise() -> dict:
    return compare.loadNoise(NOISE_PATH)


def realSource(resource: str) -> str:
    return (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8", errors="replace")


def fqcnOf(gapText: str) -> str:
    return gapText[len(GAP_PREFIX):-1]


def importedNames(source: str) -> dict[str, int]:
    """Nome simples -> linha (1-based) do import; o curinga nao entra, nao ha nome a provar."""
    lines = compare.loadTool("derive-contract").withoutNoise(source)
    declared = {}
    for number, line in enumerate(lines, 1):
        match = IMPORT_LINE.match(line)
        if match:
            declared.setdefault(f"{match.group(1)}.{match.group(2)}", number)
    return declared


def isUnusedImport(source: str, fqcn: str) -> bool:
    """Ruido: o import existe e o nome simples nao aparece em nenhum outro lugar, sem comentario nem string."""
    if fqcn not in importedNames(source):
        raise ValueError(f"{fqcn}: o import nao existe no arquivo")

    simpleName = fqcn.rsplit(".", 1)[1]
    lines = compare.loadTool("derive-contract").withoutNoise(source)
    code = "\n".join(line for line in lines if not IMPORT_LINE.match(line))
    return re.search(rf"\b{simpleName}\b", code) is None


def unusedImports(source: str) -> list[str]:
    return [fqcn for fqcn in importedNames(source) if isUnusedImport(source, fqcn)]


def schemaResources() -> list[str]:
    return compare.loadTool("parity-report").schemaResources()


def resourcesWithUnusedImports() -> list[str]:
    if not REAL_SOURCES.is_dir():
        return []

    return [
        resource for resource in schemaResources()
        if (REAL_SOURCES / f"{resource}.java").is_file() and unusedImports(realSource(resource))
    ]


def test_realNoiseFileLoads():
    noise = realNoise()
    assert noise
    assert all(entries for entries in noise.values())


def test_everyReasonCitesThePinnedRealFile():
    for resource, entries in realNoise().items():
        for entry in entries:
            assert PIN in entry["reason"], (resource, entry["text"])
            assert f"{resource}.java" in entry["reason"], (resource, entry["text"])


def sharedTexts(noise: dict, handwritten: dict) -> dict[str, set[str]]:
    shared = {
        resource: {entry["text"] for entry in entries} & {entry["text"] for entry in handwritten.get(resource, [])}
        for resource, entries in noise.items()
    }
    return {resource: texts for resource, texts in shared.items() if texts}


def usedEntriesOf(entries: list[dict], source: str) -> list[str]:
    return [entry["text"] for entry in entries if not isUnusedImport(source, fqcnOf(entry["text"]))]


def test_noGapTextHasTwoOwners():
    assert sharedTexts(realNoise(), compare.loadWaivers(WAIVERS_PATH)) == {}


def test_aTextInBothFilesIsSeenAsTwoOwners():
    text = f"{GAP_PREFIX}java.io.InputStream;"
    entry = {"kind": "require", "text": text, "count": 1, "reason": "x"}
    assert sharedTexts({"Workspace": [entry]}, {"Workspace": [entry]}) == {"Workspace": {text}}
    assert sharedTexts({"Workspace": [entry]}, {"Transfer": [entry]}) == {}


def test_theProofHelperRejectsAUsedImport():
    source = "import java.util.Map;\nimport java.io.InputStream;\n\nclass A {\n    Map<String, String> data;\n}\n"
    assert isUnusedImport(source, "java.util.Map") is False
    assert isUnusedImport(source, "java.io.InputStream") is True


def test_theProofHelperIgnoresCommentsAndStringsButNotCode():
    source = (
        "import java.io.InputStream;\nimport java.util.List;\n\n"
        "/** Reads an InputStream. */\nclass A {\n"
        "    // InputStream here\n    /* InputStream */\n"
        "    String text = \"InputStream\";\n    List<String> names;\n}\n"
    )
    assert isUnusedImport(source, "java.io.InputStream") is True
    assert isUnusedImport(source, "java.util.List") is False


def test_theProofHelperMatchesWholeWordsOnly():
    source = "import java.io.InputStream;\n\nclass A {\n    ByteArrayInputStream stream;\n}\n"
    assert isUnusedImport(source, "java.io.InputStream") is True


def test_theProofHelperRefusesAnImportThatIsNotDeclared():
    with pytest.raises(ValueError):
        isUnusedImport("class A {\n}\n", "java.util.Map")


def test_theProofHelperSkipsWildcardImports():
    assert unusedImports("import java.util.*;\n\nclass A {\n}\n") == []


def test_aNoiseEntryForAUsedImportIsRejected():
    source = "import java.util.Map;\n\nclass A {\n    Map<String, String> data;\n}\n"
    entry = {"kind": "require", "text": f"{GAP_PREFIX}java.util.Map;", "count": 1, "reason": "x"}
    assert usedEntriesOf([entry], source) == [entry["text"]]


@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(realNoise()))
def test_everyEntryIsAnImportTheRealFileNeverUses(resource):
    assert usedEntriesOf(realNoise()[resource], realSource(resource)) == []


@requiresJavaSdk
@pytest.mark.parametrize("resource", resourcesWithUnusedImports())
def test_noUnusedImportOfTheRealIsLeftOutOfTheFile(buildResource, tmpPath, resource):
    generated = _generated(buildResource, tmpPath, resource, _realConventionsText())
    gaps = compare.gapsOf(realSource=realSource(resource), generatedSource=generated, filePath=f"{resource}.java")
    unused = set(unusedImports(realSource(resource)))
    handwritten = {entry["text"] for entry in compare.loadWaivers(WAIVERS_PATH).get(resource, [])}
    noiseGaps = [
        text for kind, text in gaps
        if kind == "require" and fqcnOf(text) in unused and text not in handwritten
    ]
    listed = {entry["text"] for entry in realNoise().get(resource, [])}
    assert [text for text in noiseGaps if text not in listed] == []


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(realNoise()))
def test_everyNoiseEntryIsStillAGapInTheGeneratedFile(buildResource, tmpPath, resource):
    generated = _generated(buildResource, tmpPath, resource, _realConventionsText())
    measured = compare.measureSources(
        resource=resource,
        realSource=realSource(resource),
        generatedSource=generated,
        filePath=f"{resource}.java",
    )
    [applied], obsolete = compare.applyNoise(results=[measured], noise=realNoise())
    assert obsolete == []
    assert len(applied["noiseWaived"]) == sum(entry["count"] for entry in realNoise()[resource])
