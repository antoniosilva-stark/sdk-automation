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
    """Um $ref para arquivo externo deve contar as props do destino.

    Sem isto, os 3 recursos que vivem em apis/schemas/*.yaml apareceriam como
    scaffolding — falso positivo que bloquearia geração legítima.
    """
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
    """Na spec real as flags x-sdk-* são irmãs do $ref, não vivem no arquivo apontado.

    Resolver o $ref sem mesclar as chaves locais apagava as operações e reprovava
    Transaction/Invoice/Transfer como se não declarassem nada.
    """
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
    """Decisao 43 (2026-09-11): `Rest.java` do sdk-java @ c7f40b8 nao tem `put` de
    entidade — so `patch` e os `*Raw`. Declarar `x-sdk-put` em qualquer recurso gera
    chamada a `Rest.put`, que nao compila, e o alvo nao tem CI para pegar.

    Este teste cobre os 60 recursos, nao so o piloto. Apagar quando o `Rest.put`
    entrar upstream.
    """
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
    """$ref inválido deve levantar, nunca ser contado como schema vazio.

    Contar como vazio transformaria um recurso real em scaffolding silenciosamente.
    """
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
    """DictKey, Institution e afins não têm create em nenhum SDK.

    Exigir XCreate deles obrigava a inventar schema fictício na fonte da verdade
    só para passar no gate.
    """
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
    """Forma do SplitProfile: put também manda payload, então exige o schema."""
    schemas = _readOnly()
    schemas["Widget"]["x-sdk-put"] = True
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert not report.generatable
    assert any("x-sdk-put" in message for _, message in report.reasons)


def test_dataOnlyResourceIsGeneratableWhenDeclared(lintSpec, tmpPath):
    """CorporateRule tem zero `public static` no sdk-java real: e classe so de dados,
    usada como sub-objeto de CorporateCard. Bloquear por "sem operacao" impede paridade
    de um arquivo que existe no SDK de destino.
    """
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
    """Toda operação do template é fechada por flag x-sdk.

    Sem nenhuma flag o gerador emite classe só com campos e nenhum método — código
    plausível e inútil, que é o que o gate existe para barrar.
    """
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
    """Recurso de put, cujo Create é real — a flexibilização não pode afrouxá-lo."""
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

    assert report.generatable


def test_refWithoutProvenanceIsRejected(lintSpec, tmpPath):
    """Decisao 59: sem o SHA de origem, "passou ontem e reprova hoje" nao e diagnosticavel."""
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
