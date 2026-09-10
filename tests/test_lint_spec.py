import yaml
import pytest
from pathlib import Path

from conftest import runTool


def _writeSpec(tmpPath: Path, schemas: dict) -> Path:
    specPath = tmpPath / "spec.yaml"
    specPath.write_text(yaml.safe_dump({"components": {"schemas": schemas}}), encoding="utf-8")
    return specPath


def _modelled() -> dict:
    return {
        "Widget": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "amount": {"type": "integer"}, "created": {"type": "string"}},
        },
        "WidgetCreate": {"type": "object", "properties": {"amount": {"type": "integer"}}},
    }


def test_recursoModeladoEhGeravel(lintSpec, tmpPath):
    specPath = _writeSpec(tmpPath, _modelled())
    report = lintSpec.inspectResource("Widget", _modelled(), specPath, {})
    assert report.generatable
    assert report.readProps == 3
    assert report.createProps == 1


def test_subObjetosSaoContados(lintSpec):
    props = {
        "rules": {"type": "array", "items": {"type": "object"}},
        "payment": {"type": "object"},
        "amount": {"type": "integer"},
    }
    assert lintSpec.countSubObjects(props) == 2


def test_refExternoEhResolvido(lintSpec, tmpPath):
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


def test_loteUmEhAprovadoNaSpecReal():
    code, out = runTool("lint-spec.py", "--quiet", "--require", "Transaction,Transfer,Invoice")
    assert code == 0
    assert "[OK]" in out


def test_inventarioNaoReprova():
    code, out = runTool("lint-spec.py", "--quiet")
    assert code == 0
    assert "modo inventário" in out


def test_createVazioEhScaffolding(lintSpec, tmpPath):
    schemas = _modelled()
    schemas["WidgetCreate"] = {"type": "object", "properties": {}}
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})
    assert not report.generatable
    assert any("WidgetCreate" in message for _, message in report.reasons)


def test_apenasIdECreatedEhScaffolding(lintSpec, tmpPath):
    schemas = {
        "Widget": {"properties": {"id": {"type": "string"}, "created": {"type": "string"}}},
        "WidgetCreate": {"properties": {"amount": {"type": "integer"}}},
    }
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})
    assert not report.generatable
    assert "2 propriedades" in report.reasons[0][1]


def test_stubExigidoReprovaNaSpecReal():
    code, out = runTool("lint-spec.py", "--quiet", "--require", "Balance")
    assert code == 1
    assert "SCAFFOLDING" in out


def test_recursoInexistenteReprova():
    code, out = runTool("lint-spec.py", "--quiet", "--require", "NaoExiste")
    assert code == 1
    assert "UNDECLARED" in out


def test_specInexistenteRetornaDois():
    code, out = runTool("lint-spec.py", "--spec", "apis/nao-existe.yaml")
    assert code == 2
    assert "[ERROR]" in out


def test_refQuebradoFalhaAlto(lintSpec, tmpPath):
    """$ref inválido deve levantar, nunca ser contado como schema vazio.

    Contar como vazio transformaria um recurso real em scaffolding silenciosamente.
    """
    specPath = tmpPath / "spec.yaml"
    specPath.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        lintSpec.schemaProps({"$ref": "./schemas/ausente.yaml#/components/schemas/X"}, specPath)

    with pytest.raises(ValueError):
        lintSpec.schemaProps({"$ref": "./schemas/widget.yaml"}, specPath)


def test_idForaDaPrimeiraPosicaoReprovaComCodigoProprio(lintSpec, tmpPath):
    schemas = {
        "Widget": {"properties": {"amount": {}, "id": {}, "status": {}}},
        "WidgetCreate": {"properties": {"amount": {}}},
    }
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert not report.generatable
    assert report.reasons[0][0] == lintSpec.CODE_ID_ORDER
    assert "primeira propriedade" in report.reasons[0][1]


def test_schemaSemIdNaoDisparaIdOrder(lintSpec, tmpPath):
    schemas = {
        "Widget": {"properties": {"amount": {}, "status": {}, "extra": {}}},
        "WidgetCreate": {"properties": {"amount": {}}},
    }
    specPath = _writeSpec(tmpPath, schemas)
    report = lintSpec.inspectResource("Widget", schemas, specPath, {})

    assert report.generatable
