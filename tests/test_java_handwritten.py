import re

import pytest
import yaml

from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText
from test_compare_with_real import compare
from conftest import REPO_ROOT, requiresGenerator, requiresJavaSdk

WAIVERS_PATH = REPO_ROOT / "apis/java-handwritten.yaml"
PIN = "f60902a"
NEVER_WAIVED_METHODS = {"get", "query", "page", "pdf", "update", "delete", "put"}


def loadTool(name: str):
    return compare.loadTool(name)


def realWaivers() -> dict:
    return compare.loadWaivers(WAIVERS_PATH)


def realSource(resource: str) -> str:
    return (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8", errors="replace")


def obsoleteAgainst(resource: str, waivers: dict, generatedSource: str) -> tuple[dict, list[dict]]:
    measured = compare.measureSources(
        resource=resource,
        realSource=realSource(resource),
        generatedSource=generatedSource,
        filePath=f"{resource}.java",
    )
    [applied], obsolete = compare.applyWaivers(results=[measured], waivers=waivers)
    return applied, obsolete


def waivedCount(waivers: dict, resource: str) -> int:
    return sum(entry["count"] for entry in waivers[resource])


def tamper(waivers: dict, resource: str) -> dict:
    first, *rest = waivers[resource]
    return {**waivers, resource: [{**first, "text": first["text"] + " "}, *rest]}


def methodName(signatureText: str) -> str:
    declaration = signatureText.split("(", 1)[0]
    return declaration.split()[-1]


def test_realWaiverFileLoads():
    waivers = realWaivers()
    assert waivers
    assert all(entries for entries in waivers.values())


def test_everyReasonCitesThePinnedRealFile():
    for resource, entries in realWaivers().items():
        for entry in entries:
            assert PIN in entry["reason"], (resource, entry["text"])
            assert f"{resource}.java" in entry["reason"], (resource, entry["text"])


def eventEntriesIn(path) -> list:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [entry for name, entries in raw.items() if name.lower() == "event" for entry in entries]


def test_eventIsNeverWaived():
    assert eventEntriesIn(WAIVERS_PATH) == []


def test_theEventScanSeesAnEventEntryInACopy(tmp_path):
    copy = tmp_path / "copy.yaml"
    eventEntry = "\nEvent:\n  - kind: field\n    text: x\n    reason: y\n"
    copy.write_text(WAIVERS_PATH.read_text(encoding="utf-8") + eventEntry)
    assert len(eventEntriesIn(copy)) == 1


def test_noWaivedSignatureIsAGeneratableShape():
    for resource, entries in realWaivers().items():
        for entry in entries:
            if entry["kind"] != "signature":
                continue

            assert methodName(entry["text"]) not in NEVER_WAIVED_METHODS, (resource, entry["text"])
            assert "Log" not in entry["text"], (resource, entry["text"])
            assert "Page" not in entry["text"], (resource, entry["text"])


def test_everyWaivedTextIsAMissingMemberNotADivergentOne():
    for resource, entries in realWaivers().items():
        for entry in entries:
            assert entry["text"].startswith(f"{entry['kind']} ausente: "), (resource, entry["text"])


def declarationRange(lines: list[str], declaration: str) -> tuple[int, int]:
    """Linhas (1-based) do membro que começa em `declaration`, até a chave que fecha o corpo."""
    start = next(number for number, line in enumerate(lines, 1) if line.strip().startswith(declaration))
    depth, opened = 0, False
    for number in range(start, len(lines) + 1):
        depth += lines[number - 1].count("{") - lines[number - 1].count("}")
        opened = opened or "{" in lines[number - 1]
        if opened and depth == 0:
            return (start, number)
    raise AssertionError(f"{declaration}: corpo sem fechamento")


def usesOutsideWaivedMembers(resource: str, entries: list[dict]) -> dict[str, list[int]]:
    """Por require dispensado, as linhas de código do real que usam o nome fora dos membros dispensados."""
    lines = loadTool("derive-contract").withoutNoise(realSource(resource))
    ranges = [
        declarationRange(lines, entry["text"].split(": ", 1)[1])
        for entry in entries
        if entry["kind"] in ("signature", "inner", "constructor")
    ]
    outside = {}
    for entry in [entry for entry in entries if entry["kind"] == "require"]:
        simpleName = entry["text"].rstrip(";").rsplit(".", 1)[1]
        uses = [
            number for number, line in enumerate(lines, 1)
            if not line.lstrip().startswith("import ") and re.search(rf"\b{simpleName}\b", line)
        ]
        outside[entry["text"]] = [number for number in uses if not any(low <= number <= high for low, high in ranges)]
        assert uses, (resource, entry["text"], "o import nao e usado no real")
    return outside


@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(realWaivers()))
def test_everyWaivedRequireIsUsedOnlyInsideTheWaivedMembers(resource):
    outside = usesOutsideWaivedMembers(resource, realWaivers()[resource])
    assert {text: lines for text, lines in outside.items() if lines} == {}


@requiresJavaSdk
def test_aRequireAlsoUsedByAGeneratableMemberIsFlagged():
    entries = realWaivers()["CorporatePurchase"]
    extra = {"kind": "require", "text": "require ausente: import java.util.Map;", "count": 1, "reason": "x"}
    outside = usesOutsideWaivedMembers("CorporatePurchase", [*entries, extra])
    assert outside["require ausente: import java.util.Map;"]
    assert not outside["require ausente: import com.google.gson.Gson;"]


@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(realWaivers()))
def test_everyWaivedMemberExistsInTheRealFile(resource):
    waivers = realWaivers()
    applied, obsolete = obsoleteAgainst(resource, waivers, generatedSource="class Nothing {}")
    assert obsolete == []
    assert len(applied["waived"]) == waivedCount(waivers, resource)


@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(realWaivers()))
def test_aWaiverWithAlteredTextIsObsoleteAgainstTheRealFile(resource):
    waivers = realWaivers()
    applied, obsolete = obsoleteAgainst(resource, tamper(waivers, resource), generatedSource="class Nothing {}")
    assert len(obsolete) == 1
    assert obsolete[0]["resource"] == resource
    assert len(applied["waived"]) == waivedCount(waivers, resource) - waivers[resource][0]["count"]


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(realWaivers()))
def test_everyWaivedGapIsStillAGapInTheGeneratedFile(buildResource, tmpPath, resource):
    waivers = realWaivers()
    generated = _generated(buildResource, tmpPath, resource, _realConventionsText())
    applied, obsolete = obsoleteAgainst(resource, waivers, generatedSource=generated)
    assert obsolete == []
    assert len(applied["waived"]) == waivedCount(waivers, resource)


@requiresGenerator
@requiresJavaSdk
def test_aWaiverForAMemberTheGeneratorAlreadyEmitsIsObsolete(buildResource, tmpPath):
    emitted = "signature ausente: public static InputStream pdf(String id)"
    waivers = {"Invoice": [{"kind": "signature", "text": emitted, "count": 1, "reason": "x"}]}
    generated = _generated(buildResource, tmpPath, "Invoice", _realConventionsText())
    applied, obsolete = obsoleteAgainst("Invoice", waivers, generatedSource=generated)
    assert len(obsolete) == 1
    assert applied["waived"] == []
