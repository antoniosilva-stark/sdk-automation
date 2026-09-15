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
    """Vale para schema de REQUISICAO. Na resposta o mesmo acrescimo nao quebra ninguem —
    a versao anterior deste teste nao fazia a distincao e produzia 181 alarmes falsos.
    """
    previous = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount"]), "Widget")
    current = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount", "taxId"]), "Widget")
    changes = detector.detectBreakingChanges(current, previous)

    assert [change["type"] for change in changes] == ["required_field_added"]
    assert changes[0]["field"] == "taxId"
    assert changes[0]["rule"] == "required field added"


def test_relaxedRequiredIsNotBreaking(detector):
    """Afrouxar a REQUISICAO e compativel: quem ja mandava o campo continua podendo."""
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
                   detector.RULE_TYPE, detector.RULE_REQUIRED, detector.RULE_REQUIRED_GONE}

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


def test_removedEnumValueIsDetected(detector):
    """Tirar um valor de enum quebra quem passa aquele valor — nao estava em SHAPE_KEYS."""
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
    """Exigir parametro novo numa operacao existente quebra quem ja chama sem ele."""
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
    """Distinguir requisicao de resposta pelo sufixo do nome seria convencao; o `paths`
    diz quem e quem.
    """
    spec = _withPaths({"Widget": {}, "WidgetCreate": {}}, requestSchema="WidgetCreate")

    assert detector.requestSchemas(spec) == {"WidgetCreate"}


def test_requiredAddedToARequestIsBreaking(detector):
    """Exigir campo novo no corpo da requisicao quebra quem ja chama sem ele."""
    previous = _withPaths(_widget({"taxId": {"type": "string"}}), "Widget")
    current = _withPaths(_widget({"taxId": {"type": "string"}}, ["taxId"]), "Widget")
    changes = detector.detectBreakingChanges(current, previous)

    assert [c["type"] for c in changes] == ["required_field_added"]


def test_requiredAddedToAResponseIsNotBreaking(detector):
    """O servidor promete *mais*: o campo passa a vir sempre. Quem consome so ganha.

    181 dos 217 achados contra `development` eram deste caso — 75% do total, ruido que
    tornava a lista grande demais para alguem ler.
    """
    previous = _withPaths(_widget({"amount": {"type": "integer"}}))
    current = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount"]))

    assert detector.detectBreakingChanges(current, previous) == []


def test_requiredRemovedFromAResponseIsBreaking(detector):
    """O inverso e que quebra: o campo deixou de ser garantido e quem lia sem checar quebra."""
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
    """Campo que sumiu inteiro ja e `removed_field`; contar tambem como `required_field_removed`
    duplica a mesma mudanca sob duas regras e infla a lista que alguem precisa ler.
    """
    previous = _withPaths(_widget({"amount": {"type": "integer"}, "legacy": {"type": "string"}},
                                  ["amount", "legacy"]))
    current = _withPaths(_widget({"amount": {"type": "integer"}}, ["amount"]))
    changes = detector.detectBreakingChanges(current, previous)

    assert [c["type"] for c in changes] == ["removed_field"]


APPROVAL = """
approvals:
  - signature: "removed_operation /widget post"
    reason: "Rest.put nao existe no sdk-java"
    approvedBy: "elias"
    date: "2026-09-15"
"""


def _approvalFile(tmpPath, body: str = APPROVAL):
    path = tmpPath / "bc-approvals.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_signatureIdentifiesAFindingExactly(detector):
    """A aprovacao casa por assinatura, nunca por curinga — mesma regra das dispensas."""
    change = {"type": "removed_operation", "path": "/widget", "method": "post"}
    assert detector.signature(change) == "removed_operation /widget post"

    schemaChange = {"type": "type_changed", "schema": "Invoice", "field": "amount"}
    assert detector.signature(schemaChange) == "type_changed Invoice.amount"


def test_approvedChangeDoesNotBlock(detector, tmpPath):
    approvals = detector.parseApprovals(_approvalFile(tmpPath))
    changes = [{"type": "removed_operation", "path": "/widget", "method": "post"}]

    blocking, approved, unused = detector.applyApprovals(changes, approvals)

    assert blocking == []
    assert approved[0]["approvedBy"] == "elias"
    assert unused == []


def test_unapprovedChangeStillBlocks(detector, tmpPath):
    approvals = detector.parseApprovals(_approvalFile(tmpPath))
    changes = [{"type": "removed_schema", "schema": "Invoice"}]

    blocking, approved, _ = detector.applyApprovals(changes, approvals)

    assert len(blocking) == 1
    assert approved == []


def test_unusedApprovalIsReported(detector, tmpPath):
    """Aprovacao que parou de casar apodrece igual dispensa: tem de sair da lista."""
    approvals = detector.parseApprovals(_approvalFile(tmpPath))

    _, _, unused = detector.applyApprovals([], approvals)

    assert len(unused) == 1


def test_approvalWithoutReasonOrApproverIsRejected(detector, tmpPath):
    for body in ('approvals:\n  - signature: "removed_schema X"\n    approvedBy: "elias"\n',
                 'approvals:\n  - signature: "removed_schema X"\n    reason: "porque sim"\n'):
        with pytest.raises(ValueError):
            detector.parseApprovals(_approvalFile(tmpPath, body))


def test_approvalWithWildcardIsRejected(detector, tmpPath):
    body = ('approvals:\n  - signature: "required_field_added *"\n'
            '    reason: "tudo"\n    approvedBy: "elias"\n')
    with pytest.raises(ValueError, match="curinga"):
        detector.parseApprovals(_approvalFile(tmpPath, body))


def test_approvalsAreAppliedEndToEnd(tmpPath):
    """O caminho completo: aprovar a remocao do POST /split_profile tira ela
    da lista bloqueante e a anuncia com quem aprovou.
    """
    approvals = tmpPath / "approvals.yaml"
    approvals.write_text(
        'approvals:\n'
        '  - signature: "removed_operation /split_profile post"\n'
        '    reason: "Rest.put nao existe no sdk-java @ c7f40b8"\n'
        '    approvedBy: "elias"\n'
        '    date: "2026-09-15"\n',
        encoding="utf-8")

    code, out = runTool("breaking-change-detector.py", "--base", "origin/development",
                        "--approvals", str(approvals))

    assert "[INFO] aprovado: removed_operation /split_profile post" in out
    assert "elias" in out
    assert "removed_operation" not in out.split("[INFO] aprovado")[0], "aprovado ainda bloqueia"


def test_unusedApprovalIsAnnouncedByTheTool(tmpPath):
    approvals = tmpPath / "approvals.yaml"
    approvals.write_text(
        'approvals:\n'
        '  - signature: "removed_schema NaoExisteMais"\n'
        '    reason: "ja foi"\n'
        '    approvedBy: "elias"\n',
        encoding="utf-8")

    _, out = runTool("breaking-change-detector.py", "--base", "origin/development",
                     "--approvals", str(approvals))

    assert "aprovação não utilizada" in out
