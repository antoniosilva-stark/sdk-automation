import json
import pytest
from pathlib import Path

from conftest import requiresPythonSdk, runTool

MODULE = '''
from starkcore.utils.resource import Resource


class Widget(Resource):
    """# Widget object
    A Widget does widget things.
    ## Parameters (required):
    - amount [integer]: amount in cents. ex: 1234
    - external_id [string]: unique id. ex: "abc"
    ## Attributes (return-only):
    - id [string]: unique id. ex: "5656"
    """

    def __init__(self, amount, external_id, id=None):
        Resource.__init__(self, id=id)
        self.amount = amount
        self.external_id = external_id
'''


def _sdk(tmpPath: Path, exports: str) -> Path:
    root = tmpPath / "sdk-python"
    package = root / "starkbank" / "widget"
    package.mkdir(parents=True)
    (package / "__widget.py").write_text(MODULE, encoding="utf-8")
    (package / "__init__.py").write_text(f"from .__widget import {exports}\n", encoding="utf-8")
    return root


def _report(tmpPath: Path, exports: str) -> dict:
    code, out = runTool("coverage-report.py", "--python", str(_sdk(tmpPath, exports)), "--json")
    assert code == 0, out
    return json.loads(out)


def test_fullySupportedOperationsCountAsFullParity(tmpPath):
    report = _report(tmpPath, "create, get, query, page")

    assert report["fullParity"] == ["Widget"]
    assert report["withPending"] == []


def test_operationWithoutTemplateSectionBecomesPendingNotExcluded(tmpPath):
    report = _report(tmpPath, "get, query, validate")

    assert report["fullParity"] == []
    assert report["withPending"] == [{"resource": "Widget", "pending": ["validate"]}]


def test_classWithoutCrudIsReachableAsDataOnly(tmpPath):
    report = _report(tmpPath, "parse_rules")

    assert report["fullParity"] == []
    assert report["withPending"] == [{"resource": "Widget", "pending": ["parse_rules"]}]


def test_flagsComeFromTemplateNotAParallelList(coverageReport):
    flags = coverageReport.templateFlags()

    assert "x-sdk-page" in flags
    assert "x-sdk-cancel" in flags
    assert "x-sdk-invented" not in flags
    assert "x-sdk-data-only" in flags


def test_newTemplateSectionEntersTheReportOnItsOwn(coverageReport, tmpPath):
    template = tmpPath / "model.mustache"
    template.write_text("{{#vendorExtensions.x-sdk-get}}x{{/vendorExtensions.x-sdk-get}}", encoding="utf-8")

    assert coverageReport.templateFlags(template) == {"x-sdk-get", "x-sdk-data-only"}


@requiresPythonSdk
def test_realReachMeetsTheDeliveryTarget():
    code, out = runTool("coverage-report.py", "--json")
    assert code == 0, out

    report = json.loads(out)
    reachable = len(report["fullParity"]) + len(report["withPending"])

    assert report["total"] == 43
    assert reachable == 42
    assert [entry["resource"] for entry in report["outOfReach"]] == ["request"]


def test_subresourceFlagIsNotAnOperationSection(coverageReport, tmpPath):
    template = tmpPath / "model.mustache"
    section = "x-sdk-subresource"
    template.write_text("{{#vendorExtensions.%s}}x{{/vendorExtensions.%s}}" % (section, section), encoding="utf-8")
    flags = coverageReport.templateFlags(template)
    extractSchema = coverageReport.loadTool("extract-schema")

    assert "x-sdk-subresource" not in extractSchema.OPERATION_FLAGS.values()
    assert flags & set(extractSchema.OPERATION_FLAGS.values()) == set()
