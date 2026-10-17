import os
import sys
import yaml
import pytest
import subprocess

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
    spec = tmpPath / "spec.yaml"
    spec.write_text(yaml.safe_dump(_spec(_widget({"amount": {"type": "integer"}}))), encoding="utf-8")
    monkeypatch.setattr(detector, "SPEC_FILE", str(spec))

    current = detector.loadSpec()
    assert detector.detectBreakingChanges(current, current,
                                          detector.resolveSchemas(current, lambda _: {}),
                                          detector.resolveSchemas(current, lambda _: {})) == []


def test_invalidYamlPropagates(detector, tmpPath, monkeypatch):
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
    previous = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount"]), "Widget")
    current = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount", "taxId"]), "Widget")
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["required_field_added"]
    assert changes[0]["field"] == "taxId"
    assert changes[0]["rule"] == "required field added"


def test_relaxedRequiredIsNotBreaking(detector):
    previous = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount", "taxId"]), "Widget")
    current = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount"]), "Widget")

    assert detector.detectBreakingChanges(current, previous) == []


def test_removedFieldIsDetected(detector):
    previous = _spec(_widget({"amount": {"type": "integer"}, "legacy": {"type": "string"}}))
    current = _spec(_widget({"amount": {"type": "integer"}}))
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["removed_field"]
    assert changes[0]["field"] == "legacy"


def test_changeInsideTheRefIsSeen(detector, tmpPath, monkeypatch):
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
    declared = (REPO_ROOT / "governance.md").read_text(encoding="utf-8")
    line = next(part for part in declared.splitlines() if part.startswith("BC ="))
    promised = [rule.strip() for rule in line.removeprefix("BC =").split(",")]

    implemented = {detector.RULE_OPERATION, detector.RULE_SCHEMA, detector.RULE_PARAM,
                   detector.RULE_TYPE, detector.RULE_REQUIRED, detector.RULE_REQUIRED_GONE}

    assert set(promised) == implemented, f"governance.md promete {promised}"


def test_baseIsRequiredBecauseHeadComparesTheSpecWithItself():
    code, _ = runTool("breaking-change-detector.py")

    assert code == 2, "sem --base o tool tem de recusar, nao assumir HEAD"


def _historyWithABreakingChange(tmpPath) -> str:
    """Repositorio descartavel com duas versoes da spec; devolve o commit anterior."""
    # sem GIT_DIR/GIT_INDEX_FILE herdados (pytest chamado por hook) e sem assinatura/hooks da maquina
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@local",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@local"})

    def git(*args: str) -> str:
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                                cwd=tmpPath, capture_output=True, text=True, env=environment)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    git("init", "-q")
    specFile = tmpPath / "apis/spec-v2.openapi.yaml"
    specFile.parent.mkdir()
    specFile.write_text(yaml.safe_dump(_spec(_widget({"amount": {"type": "integer"}}))), encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "before")
    previous = git("rev-parse", "HEAD")
    specFile.write_text(yaml.safe_dump(_spec(_widget({"amount": {"type": "string"}}))), encoding="utf-8")
    git("commit", "-q", "-am", "after")
    return previous


def _detectIn(directory, base: str) -> tuple[int, str]:
    result = subprocess.run([sys.executable, str(REPO_ROOT / "tools/breaking-change-detector.py"), "--base", base],
                            cwd=directory, capture_output=True, text=True)
    return result.returncode, result.stdout


def test_realHistoryWithBreakingChangeIsReported(tmpPath):
    previous = _historyWithABreakingChange(tmpPath)

    code, out = _detectIn(tmpPath, previous)

    assert code == 0, "BC e informativo, nao bloqueia"
    assert "[WARN]" in out
    assert "type_changed" in out
    assert "governance.md" in out


def test_theToolReportsAVerdictAgainstRealHistory(tmpPath):
    previous = _historyWithABreakingChange(tmpPath)

    code, out = _detectIn(tmpPath, previous)

    assert code == 0, out
    assert "verificando breaking changes" in out
    assert "regra violada, governance.md" in out


def test_historyWithoutBreakingChangeReportsNone(tmpPath):
    previous = _historyWithABreakingChange(tmpPath)

    code, out = _detectIn(tmpPath, "HEAD")

    assert code == 0, out
    assert "[WARN]" not in out


def test_removedEnumValueIsDetected(detector):
    previous = _spec(_widget({"interval": {"type": "string", "enum": ["day", "week", "month"]}}))
    current = _spec(_widget({"interval": {"type": "string", "enum": ["day", "week"]}}))
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["type_changed"]
    assert changes[0]["rule"] == "type changes"


def test_addedEnumValueIsNotBreaking(detector):
    previous = _spec(_widget({"interval": {"type": "string", "enum": ["day", "week"]}}))
    current = _spec(_widget({"interval": {"type": "string", "enum": ["day", "week", "month"]}}))

    assert detector.detectBreakingChanges(current, previous) == []


def test_parameterThatBecomesRequiredIsDetected(detector):
    antes = {"name": "cursor", "in": "query"}
    depois = {"name": "cursor", "in": "query", "required": True}
    previous = _spec({}, {"/widget": {"get": {"parameters": [antes]}}})
    current = _spec({}, {"/widget": {"get": {"parameters": [depois]}}})
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["param_became_required"]
    assert changes[0]["rule"] == "required field added"


def test_brandNewRequiredParameterIsDetected(detector):
    previous = _spec({}, {"/widget": {"get": {"parameters": []}}})
    current = _spec({}, {"/widget": {"get": {"parameters": [{"name": "taxId", "in": "query", "required": True}]}}})
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["param_became_required"]


def _withPaths(schemas: dict, requestSchema: str | None = None) -> dict:
    paths = {"/widget": {"get": {"responses": {"200": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/Widget"}}}}}}}}
    if requestSchema:
        paths["/widget"]["post"] = {"requestBody": {"content": {"application/json": {
            "schema": {"type": "array", "items": {"$ref": f"#/components/schemas/{requestSchema}"}}}}}}
    return {"paths": paths, "components": {"schemas": schemas}}


def test_requestSchemasComeFromTheRequestBody(detector):
    spec = _withPaths({"Widget": {}, "WidgetCreate": {}}, requestSchema="WidgetCreate")

    assert detector.requestSchemas(spec) == {"WidgetCreate"}


def test_requiredAddedToARequestIsBreaking(detector):
    previous = _withPaths(_widget({"taxId": {"type": "string"}}), "Widget")
    current = _withPaths(_widget({"taxId": {"type": "string"}}, ["taxId"]), "Widget")
    changes = detector.detectBreakingChanges(current, previous)

    assert [c["type"] for c in changes] == ["required_field_added"]


def test_requiredAddedToAResponseIsNotBreaking(detector):
    previous = _withPaths(_widget({"amount": {"type": "integer"}}))
    current = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount"]))

    assert detector.detectBreakingChanges(current, previous) == []


def test_requiredRemovedFromAResponseIsBreaking(detector):
    previous = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount"]))
    current = _withPaths(_widget({"amount": {"type": "integer"}}, []))
    changes = detector.detectBreakingChanges(current, previous)

    assert [c["type"] for c in changes] == ["required_field_removed"]
    assert changes[0]["rule"] == "removes required field"


def test_requiredRemovedFromARequestIsNotBreaking(detector):
    previous = _withPaths(_widget({"taxId": {"type": "string"}}, ["taxId"]), "Widget")
    current = _withPaths(_widget({"taxId": {"type": "string"}}, []), "Widget")

    assert detector.detectBreakingChanges(current, previous) == []


def test_removedFieldIsNotAlsoReportedAsRequiredRemoved(detector):
    previous = _withPaths(_widget({"amount": {"type": "integer"}, "legacy": {"type": "string"}},
                                  ["amount", "legacy"]))
    current = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount"]))
    changes = detector.detectBreakingChanges(current, previous)

    assert [c["type"] for c in changes] == ["removed_field"]


def test_unresolvableBaseIsAnErrorNotAFreePass():
    code, out = runTool("breaking-change-detector.py", "--base", "origin/nao-existe")

    assert code == 2
    assert "não resolve" in out


def test_baseThatResolvesWithoutTheSpecIsStillTheFirstCommit(detector, tmpPath, monkeypatch):
    monkeypatch.setattr(detector, "SPEC_FILE", "apis/inexistente.yaml")

    assert detector.loadSpec("HEAD") is None
