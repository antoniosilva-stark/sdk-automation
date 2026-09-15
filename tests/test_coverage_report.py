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
    """Invoice gera hoje mesmo sem `qrcode`: tratar como fora de alcance seria mentira."""
    report = _report(tmpPath, "get, query, qrcode")

    assert report["fullParity"] == []
    assert report["withPending"] == [{"resource": "Widget", "pending": ["qrcode"]}]


def test_resourceWithOnlySuppressedOperationIsOutOfReach(tmpPath):
    """`put` e derivado do Python mas suprimido: Rest.put nao existe no sdk-java."""
    report = _report(tmpPath, "put")

    assert report["outOfReach"] == [{"resource": "Widget", "reason": "sem operação suportada"}]


def test_classWithoutCrudIsReachableAsDataOnly(tmpPath):
    """CorporateRule tem zero `public static` no Java real: classe so de dados conta."""
    report = _report(tmpPath, "parse_rules")

    assert report["fullParity"] == []
    assert report["withPending"] == [{"resource": "Widget", "pending": ["parse_rules"]}]


def test_flagsComeFromTemplateNotAParallelList(coverageReport):
    """Duas autoridades: o template diz o que pode ser produzido, o apply-schema o que
    pode ser escrito. Lista propria dessincronizaria das duas em silencio."""
    flags = coverageReport.templateFlags()

    assert "x-sdk-page" in flags
    assert "x-sdk-cancel" in flags
    assert "x-sdk-qrcode" not in flags
    assert "x-sdk-data-only" in flags


def test_newTemplateSectionEntersTheReportOnItsOwn(coverageReport, tmpPath):
    template = tmpPath / "model.mustache"
    template.write_text("{{#vendorExtensions.x-sdk-get}}x{{/vendorExtensions.x-sdk-get}}", encoding="utf-8")

    assert coverageReport.templateFlags(template) == {"x-sdk-get", "x-sdk-data-only"}


@requiresPythonSdk
def test_realReachMeetsTheDeliveryTarget():
    """Meta: >= 35 de 41, com o restante nomeado."""
    code, out = runTool("coverage-report.py", "--json")
    assert code == 0, out

    report = json.loads(out)
    reachable = len(report["fullParity"]) + len(report["withPending"])

    assert report["total"] == 41
    assert reachable == 40
    assert [entry["resource"] for entry in report["outOfReach"]] == ["request"]
