import yaml
import pytest
from pathlib import Path

from conftest import REPO_ROOT, runTool


def _writeSpec(tmpPath: Path, schemas: dict) -> Path:
    specPath = tmpPath / "spec.yaml"
    specPath.write_text(yaml.safe_dump({"components": {"schemas": schemas}}), encoding="utf-8")
    return specPath


def _modelled() -> dict:
    return {
        "Widget": {
            "type": "object",
            "x-sdk-create": True,
            "x-sdk-get": True,
            "x-sdk-query": True,
            "properties": {"id": {"type": "string"}, "amount": {"type": "integer"}, "created": {"type": "string"}},
        },
        "WidgetCreate": {"type": "object", "properties": {"amount": {"type": "integer"}}},
    }


def _readOnly() -> dict:
    return {
        "Widget": {
            "type": "object",
            "x-sdk-get": True,
            "x-sdk-query": True,
            "x-sdk-page": True,
            "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "status": {"type": "string"}},
        },
    }


def test_modelledResourceIsGeneratable(lintSpec, tmpPath):
    specPath = _writeSpec(tmpPath, _modelled())
    report = lintSpec.inspectResource("Widget", _modelled(), specPath, {})
    assert report.generatable
    assert report.readProps == 3
    assert report.createProps == 1


def test_subObjectsAreCounted(lintSpec):
    props = {
        "rules": {"type": "array", "items": {"type": "object"}},
        "payment": {"type": "object"},
        "amount": {"type": "integer"},
    }
    assert lintSpec.countSubObjects(props) == 2


def test_externalRefIsResolved(lintSpec, tmpPath):
    (tmpPath / "schemas").mkdir()
    (tmpPath / "schemas" / "widget.yaml").write_text(
        yaml.safe_dump({"components": {"schemas": {"Widget": {"properties": {"a": {}, "b": {}, "c": {}}}}}}),
        encoding="utf-8",
    )
    specPath = tmpPath / "spec.yaml"
    specPath.write_text("{}", encoding="utf-8")

    schema = {"$ref": "./schemas/widget.yaml#/components/schemas/Widget"}
    assert len(lintSpec.schemaProps(schema, specPath)) == 3


def test_flagsSurviveTheExternalRef(lintSpec, tmpPath):
    (tmpPath / "schemas").mkdir()
    (tmpPath / "schemas" / "widget.yaml").write_text(
        "# fonte: starkbank/sdk-python@abc1234 · starkbank/widget/__widget.py\n" + yaml.safe_dump(
            {"components": {"schemas": {"Widget": {"properties": {"id": {}, "b": {}, "c": {}}}}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    specPath = tmpPath / "spec.yaml"
    specPath.write_text("{}", encoding="utf-8")

    schemas = {
        "Widget": {
            "x-sdk-get": True,
            "x-sdk-query": True,
            "$ref": "./schemas/widget.yaml#/components/schemas/Widget",
        },
    }
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert report.operations == ["x-sdk-get", "x-sdk-query"]
    assert report.readProps == 3
    assert report.generatable


def test_noResourceDeclaresPutWhileRestPutIsMissing():
    spec = yaml.safe_load(Path("apis/spec-v2.openapi.yaml").read_text(encoding="utf-8"))
    schemas = (spec.get("components") or {}).get("schemas") or {}

    declaram = [name for name, schema in schemas.items()
                if isinstance(schema, dict) and schema.get("x-sdk-put")]

    assert declaram == [], f"x-sdk-put sem primitivo no SDK real: {declaram}"


def test_firstBatchIsApprovedInTheRealSpec():
    code, out = runTool("lint-spec.py", "--quiet", "--require", "Transaction,Transfer,Invoice")
    assert code == 0
    assert "[OK]" in out


def test_inventoryDoesNotFail():
    code, out = runTool("lint-spec.py", "--quiet")
    assert code == 0
    assert "modo inventário" in out


def test_emptyCreateIsScaffolding(lintSpec, tmpPath):
    schemas = _modelled()
    schemas["WidgetCreate"] = {"type": "object", "properties": {}}
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})
    assert not report.generatable
    assert any("WidgetCreate" in message for _, message in report.reasons)


def test_onlyIdAndCreatedIsScaffolding(lintSpec, tmpPath):
    schemas = {
        "Widget": {
            "x-sdk-get": True,
            "properties": {"id": {"type": "string"}, "created": {"type": "string"}},
        },
        "WidgetCreate": {"properties": {"amount": {"type": "integer"}}},
    }
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})
    assert not report.generatable
    assert "2 propriedades" in report.reasons[0][1]


def test_requiredStubFailsInTheRealSpec():
    code, out = runTool("lint-spec.py", "--quiet", "--require", "Account")
    assert code == 1
    assert "SCAFFOLDING" in out


def test_unknownResourceFails():
    code, out = runTool("lint-spec.py", "--quiet", "--require", "NaoExiste")
    assert code == 1
    assert "UNDECLARED" in out


def test_missingSpecReturnsTwo():
    code, out = runTool("lint-spec.py", "--spec", "apis/nao-existe.yaml")
    assert code == 2
    assert "[ERROR]" in out


def test_brokenRefFailsLoudly(lintSpec, tmpPath):
    specPath = tmpPath / "spec.yaml"
    specPath.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        lintSpec.schemaProps({"$ref": "./schemas/ausente.yaml#/components/schemas/X"}, specPath)

    with pytest.raises(ValueError):
        lintSpec.schemaProps({"$ref": "./schemas/widget.yaml"}, specPath)


def test_idOutOfFirstPositionFailsWithItsOwnCode(lintSpec, tmpPath):
    schemas = {
        "Widget": {"x-sdk-get": True, "properties": {"amount": {}, "id": {}, "status": {}}},
        "WidgetCreate": {"properties": {"amount": {}}},
    }
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert not report.generatable
    assert report.reasons[0][0] == lintSpec.CODE_ID_ORDER
    assert "primeira propriedade" in report.reasons[0][1]


def test_readOnlyResourceDoesNotRequireCreate(lintSpec, tmpPath):
    schemas = _readOnly()
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert report.generatable
    assert report.createProps == 0


def test_declaredCreateRequiresNonEmptyCreateSchema(lintSpec, tmpPath):
    schemas = _readOnly()
    schemas["Widget"]["x-sdk-create"] = True
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert not report.generatable
    assert any("x-sdk-create" in message for _, message in report.reasons)


def test_declaredPutRequiresNonEmptyCreateSchema(lintSpec, tmpPath):
    schemas = _readOnly()
    schemas["Widget"]["x-sdk-put"] = True
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert not report.generatable
    assert any("x-sdk-put" in message for _, message in report.reasons)


def test_dataOnlyResourceIsGeneratableWhenDeclared(lintSpec, tmpPath):
    schemas = _readOnly()
    del schemas["Widget"]["x-sdk-get"]
    del schemas["Widget"]["x-sdk-query"]
    del schemas["Widget"]["x-sdk-page"]
    schemas["Widget"]["x-sdk-data-only"] = True
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert report.generatable
    assert report.operations == ["x-sdk-data-only"]


def test_resourceWithoutDeclaredOperationIsScaffolding(lintSpec, tmpPath):
    schemas = _readOnly()
    del schemas["Widget"]["x-sdk-get"]
    del schemas["Widget"]["x-sdk-query"]
    del schemas["Widget"]["x-sdk-page"]
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert not report.generatable
    assert report.reasons[0][0] == lintSpec.CODE_NO_OPERATION


def test_declaredOperationsAppearInTheReport(lintSpec, tmpPath):
    schemas = _modelled()
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert report.operations == ["x-sdk-create", "x-sdk-get", "x-sdk-query"]


def test_splitProfileIsGeneratableInTheRealSpec():
    code, out = runTool("lint-spec.py", "--quiet", "--require", "SplitProfile")
    assert code == 0
    assert "[OK]" in out


def test_schemaWithoutIdDoesNotTriggerIdOrder(lintSpec, tmpPath):
    schemas = {
        "Widget": {"x-sdk-get": True, "properties": {"amount": {}, "status": {}, "extra": {}}},
        "WidgetCreate": {"properties": {"amount": {}}},
    }
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert [code for code, _ in report.reasons] == [lintSpec.CODE_NO_ID]


def test_refWithoutProvenanceIsRejected(lintSpec, tmpPath):
    (tmpPath / "schemas").mkdir()
    (tmpPath / "schemas" / "widget.yaml").write_text(
        yaml.safe_dump({"components": {"schemas": {"Widget": {"properties": {"id": {}, "b": {}, "c": {}}}}}}),
        encoding="utf-8",
    )
    specPath = tmpPath / "spec.yaml"
    specPath.write_text("{}", encoding="utf-8")

    schemas = {"Widget": {"x-sdk-get": True, "$ref": "./schemas/widget.yaml#/components/schemas/Widget"}}
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert not report.generatable
    assert report.reasons[0][0] == lintSpec.CODE_NO_PROVENANCE


def test_refWithProvenanceIsAccepted(lintSpec, tmpPath):
    (tmpPath / "schemas").mkdir()
    body = yaml.safe_dump({"components": {"schemas": {"Widget": {"properties": {"id": {}, "b": {}, "c": {}}}}}},
                          sort_keys=False)
    (tmpPath / "schemas" / "widget.yaml").write_text(
        "# fonte: starkbank/sdk-python@abc1234 · starkbank/widget/__widget.py\n" + body,
        encoding="utf-8",
    )
    specPath = tmpPath / "spec.yaml"
    specPath.write_text("{}", encoding="utf-8")

    schemas = {"Widget": {"x-sdk-get": True, "$ref": "./schemas/widget.yaml#/components/schemas/Widget"}}
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert report.generatable


def test_everyAppliedSchemaDeclaresItsProvenance():
    for path in sorted((REPO_ROOT / "apis/schemas").glob("*.yaml")):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("# fonte: starkbank/sdk-python@"), f"{path.name} sem procedencia"
        assert "@desconhecido" not in first, f"{path.name} com procedencia vazia"


def test_schemaWithoutIdIsNotGeneratable(lintSpec, tmpPath):
    schemas = {
        "Widget": {"x-sdk-query": True,
                   "properties": {"code": {}, "name": {}, "number": {}}},
    }
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert not report.generatable
    assert report.reasons[0][0] == lintSpec.CODE_NO_ID


def test_theFourSubResourcesAreBlockedInTheRealSpec():
    for resource in ("CardMethod", "Institution", "MerchantCategory", "MerchantCountry"):
        code, out = runTool("lint-spec.py", "--quiet", "--require", resource)
        assert code == 1, f"{resource} passou no lint sem ter id"
        assert "NO_ID" in out
