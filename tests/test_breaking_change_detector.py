import yaml
import pytest

from conftest import REPO_ROOT, runTool


def _paths(*names: str) -> dict:
    return {"paths": {name: {} for name in names}}


def test_removedPathIsDetected(detector):
    changes = detector.detectBreakingChanges(_paths("/invoice"), _paths("/invoice", "/transfer"))
    assert len(changes) == 1
    assert changes[0] == {"type": "removed_path", "severity": "MAJOR",
                          "rule": "removes operation", "path": "/transfer"}


def test_multipleRemovedPathsAreOrdered(detector):
    previous = _paths("/invoice", "/transfer", "/balance")
    changes = detector.detectBreakingChanges(_paths("/invoice"), previous)
    assert [c["path"] for c in changes] == ["/balance", "/transfer"]


def test_currentWithoutPathsRemovesEverything(detector):
    changes = detector.detectBreakingChanges({}, _paths("/invoice", "/transfer"))
    assert len(changes) == 2
    

def test_withoutPreviousNothingIsReported(detector):
    assert detector.detectBreakingChanges(_paths("/invoice"), None) == []


def test_identicalSpecReportsNothing(detector):
    current = _paths("/invoice", "/transfer")
    assert detector.detectBreakingChanges(current, current) == []


def test_addedPathIsNotBreaking(detector):
    changes = detector.detectBreakingChanges(_paths("/invoice", "/novo"), _paths("/invoice"))
    assert changes == []


# --- contrato de saida ---

def test_specWithoutChangeReportsNothing(detector, tmpPath, monkeypatch):
    """Substitui a versao que rodava sem `--base`, comparando a spec com ela mesma: o [OK]
    era garantido qualquer que fosse o conteudo.

    Fixar um SHA tambem nao serve — a arvore de trabalho muda, e o teste passaria a medir
    a branch em vez da ferramenta.
    """
    spec = tmpPath / "spec.yaml"
    spec.write_text(yaml.safe_dump(_spec(_widget({"amount": {"type": "integer"}}))), encoding="utf-8")
    monkeypatch.setattr(detector, "SPEC_FILE", str(spec))

    current = detector.loadSpec()
    assert detector.detectBreakingChanges(current, current,
                                          detector.resolveSchemas(current, lambda _: {}),
                                          detector.resolveSchemas(current, lambda _: {})) == []


def test_invalidYamlPropagates(detector, tmpPath, monkeypatch):
    """YAML malformado deve levantar, não ser lido como "nada para comparar".

    O código anterior usava `except Exception` e devolvia None nesse caso, o que
    fazia o detector passar verde com a spec quebrada.
    """
    broken = tmpPath / "broken.yaml"
    broken.write_text("paths: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(detector, "SPEC_FILE", str(broken))

    with pytest.raises(yaml.YAMLError):
        detector.loadSpec()

def _spec(schemas: dict, paths: dict | None = None) -> dict:
    return {"paths": paths or {"/widget": {}}, "components": {"schemas": schemas}}


def _widget(properties: dict, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties}
    if required is not None:
        schema["required"] = required
    return {"Widget": schema}


def test_removedOperationOnASurvivingPathIsDetected(detector):
    previous = _spec({}, {"/widget": {"get": {}, "delete": {}}})
    current = _spec({}, {"/widget": {"get": {}}})
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["removed_operation"]
    assert changes[0]["method"] == "delete"
    assert changes[0]["rule"] == "removes operation"


def test_addedOperationIsNotBreaking(detector):
    previous = _spec({}, {"/widget": {"get": {}}})
    current = _spec({}, {"/widget": {"get": {}, "post": {}}})

    assert detector.detectBreakingChanges(current, previous) == []


def test_removedSchemaIsDetected(detector):
    previous = _spec({"Widget": {"type": "object"}, "Gone": {"type": "object"}})
    current = _spec({"Widget": {"type": "object"}})
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["removed_schema"]
    assert changes[0]["schema"] == "Gone"
    assert changes[0]["rule"] == "removes schema"


def test_addedSchemaIsNotBreaking(detector):
    previous = _spec({"Widget": {"type": "object"}})
    current = _spec({"Widget": {"type": "object"}, "Novo": {"type": "object"}})

    assert detector.detectBreakingChanges(current, previous) == []


def test_removedRequiredParamIsDetected(detector):
    parameter = {"name": "id", "in": "path", "required": True}
    previous = _spec({}, {"/widget": {"get": {"parameters": [parameter]}}})
    current = _spec({}, {"/widget": {"get": {"parameters": []}}})
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["removed_required_param"]
    assert changes[0]["param"] == "id"
    assert changes[0]["rule"] == "removes required param"


def test_removedOptionalParamIsNotBreaking(detector):
    parameter = {"name": "cursor", "in": "query"}
    previous = _spec({}, {"/widget": {"get": {"parameters": [parameter]}}})
    current = _spec({}, {"/widget": {"get": {"parameters": []}}})

    assert detector.detectBreakingChanges(current, previous) == []


def test_changedTypeIsDetected(detector):
    previous = _spec(_widget({"amount": {"type": "integer"}}))
    current = _spec(_widget({"amount": {"type": "string"}}))
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["type_changed"]
    assert changes[0]["rule"] == "type changes"


def test_addedFormatIsBreakingForTheJavaClient(detector):
    """`format: int64` em `integer` faz `Integer` virar `Long` no sdk-java: e BC de cliente."""
    previous = _spec(_widget({"balance": {"type": "integer"}}))
    current = _spec(_widget({"balance": {"type": "integer", "format": "int64"}}))
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["type_changed"]
    assert changes[0]["now"] == {"type": "integer", "format": "int64"}


def test_arrayBecomingObjectIsDetected(detector):
    previous = _spec(_widget({"tags": {"type": "array", "items": {"type": "string"}}}))
    current = _spec(_widget({"tags": {"type": "object"}}))
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["type_changed"]


def test_unchangedTypeIsNotBreaking(detector):
    schemas = _widget({"amount": {"type": "integer", "format": "int64"}})

    assert detector.detectBreakingChanges(_spec(schemas), _spec(schemas)) == []


def test_addedRequiredFieldIsDetected(detector):
    previous = _spec(_widget({"amount": {"type": "integer"}}, ["amount"]))
    current = _spec(_widget({"amount": {"type": "integer"}}, ["amount", "taxId"]))
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["required_field_added"]
    assert changes[0]["field"] == "taxId"
    assert changes[0]["rule"] == "required field added"


def test_relaxedRequiredIsNotBreaking(detector):
    previous = _spec(_widget({"amount": {"type": "integer"}}, ["amount", "taxId"]))
    current = _spec(_widget({"amount": {"type": "integer"}}, ["amount"]))

    assert detector.detectBreakingChanges(current, previous) == []


def test_removedFieldIsDetected(detector):
    previous = _spec(_widget({"amount": {"type": "integer"}, "legacy": {"type": "string"}}))
    current = _spec(_widget({"amount": {"type": "integer"}}))
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["removed_field"]
    assert changes[0]["field"] == "legacy"


def test_changeInsideTheRefIsSeen(detector, tmpPath, monkeypatch):
    """Decisao 67: comparar o texto do `$ref` nao veria mudanca no arquivo apontado."""
    (tmpPath / "schemas").mkdir()
    schemaPath = tmpPath / "schemas" / "widget.yaml"
    schemaPath.write_text(
        yaml.safe_dump({"components": {"schemas": {"Widget": {"properties": {"amount": {"type": "integer"}}}}}}),
        encoding="utf-8",
    )
    specPath = tmpPath / "spec.yaml"
    specPath.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(detector, "SPEC_FILE", str(specPath))

    reference = {"Widget": {"$ref": "./schemas/widget.yaml#/components/schemas/Widget"}}
    previous = detector.resolveSchemas(_spec(reference), detector.refLoader(None))

    schemaPath.write_text(
        yaml.safe_dump({"components": {"schemas": {"Widget": {"properties": {"amount": {"type": "string"}}}}}}),
        encoding="utf-8",
    )
    current = detector.resolveSchemas(_spec(reference), detector.refLoader(None))
    changes = detector.detectBreakingChanges(_spec(reference), _spec(reference), current, previous)

    assert [change["type"] for change in changes] == ["type_changed"]


def test_everyGovernanceRuleIsImplemented(detector):
    """`governance.md` prometia 5 regras e o detector implementava 1."""
    declared = (REPO_ROOT / "governance.md").read_text(encoding="utf-8")
    line = next(part for part in declared.splitlines() if part.startswith("BC ="))
    promised = [rule.strip() for rule in line.removeprefix("BC =").split(",")]

    implemented = {detector.RULE_OPERATION, detector.RULE_SCHEMA, detector.RULE_PARAM,
                   detector.RULE_TYPE, detector.RULE_REQUIRED}

    assert set(promised) == implemented, f"governance.md promete {promised}"


def test_baseIsRequiredBecauseHeadComparesTheSpecWithItself():
    """`--base HEAD` compara a arvore com ela mesma: em PR o gate nunca acusa nada.

    Era o estado real ate 2026-09-15 — as 5 regras existiam e jamais rodaram contra
    historia de verdade.
    """
    code, _ = runTool("breaking-change-detector.py")

    assert code == 2, "sem --base o tool tem de recusar, nao assumir HEAD"


def test_realHistoryWithBreakingChangeFails():
    """Prova que o gate morde: `4c8d50a` e o commit anterior ao mapeamento dos 40 recursos,
    onde Invoice.amount virou int64 e 12 campos sairam.
    """
    code, out = runTool("breaking-change-detector.py", "--base", "4c8d50a")

    assert code == 1
    assert "type_changed" in out
    assert "governance.md" in out


def test_theToolReportsAVerdictAgainstRealHistory():
    """Contra historia real o veredito e util em qualquer direcao: o que nao pode e passar
    sem olhar. Nao se afirma "zero BC" aqui porque isso seria medir a branch, nao o tool.
    """
    code, out = runTool("breaking-change-detector.py", "--base", "567ce54")

    assert code in (0, 1), out
    assert "verificando breaking changes" in out
    if code == 1:
        assert "regra violada, governance.md" in out
