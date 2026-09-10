import yaml
import pytest
from pathlib import Path

from conftest import REPO_ROOT, runTool

SDK_PYTHON = Path.home() / "workspace/bank/sdk-python"

WIDGET_MODULE = '''
from starkcore.utils.resource import Resource


class Widget(Resource):
    """# Widget object
    A Widget does widget things.
    ## Parameters (required):
    - amount [integer]: amount in cents. ex: 1234
    - external_id [string]: unique id to avoid duplicates. ex: "abc"
    ## Parameters (optional):
    - tags [list of strings, default []]: list of strings for reference. ex: ["a"]
    - due [datetime.datetime or datetime.date or string, default now]: due date
    - expiration [integer or datetime.timedelta, default 5097600 (59 days)]: seconds
    - rules [list of Widget.Rules, default []]: list of Widget.Rule objects
    ## Attributes (return-only):
    - id [string]: unique id returned when Widget is created. ex: "5656"
    - created [datetime.datetime]: creation datetime
    """

    def __init__(self, amount, external_id, tags=None, due=None, expiration=None, rules=None, id=None, created=None):
        Resource.__init__(self, id=id)
        self.amount = amount
'''


def _fakeSdk(tmpPath: Path, module: str = "widget", body: str = WIDGET_MODULE) -> Path:
    root = tmpPath / "sdk-python"
    package = root / "starkbank" / module
    package.mkdir(parents=True)
    (package / f"__{module}.py").write_text(body, encoding="utf-8")
    return root


def test_camelCase(extractSchema):
    assert extractSchema.camelCase("external_id") == "externalId"
    assert extractSchema.camelCase("tax_id") == "taxId"
    assert extractSchema.camelCase("transaction_ids") == "transactionIds"
    assert extractSchema.camelCase("id") == "id"


def test_modulePath(extractSchema):
    root = Path("/ref")
    assert extractSchema.modulePath(root, "Transaction").as_posix().endswith("starkbank/transaction/__transaction.py")
    assert extractSchema.modulePath(root, "SplitProfile").as_posix().endswith("starkbank/splitprofile/__splitprofile.py")


def test_parseTypeMapeiaTiposBasicos(extractSchema):
    assert extractSchema.parseType("integer") == {"type": "integer"}
    assert extractSchema.parseType("string") == {"type": "string"}
    assert extractSchema.parseType("float") == {"type": "number"}
    assert extractSchema.parseType("boolean") == {"type": "boolean"}
    assert extractSchema.parseType("datetime.datetime") == {"type": "string", "format": "date-time"}


def test_parseTypeListas(extractSchema):
    assert extractSchema.parseType("list of strings") == {"type": "array", "items": {"type": "string"}}
    assert extractSchema.parseType("list of Widget.Rules") == {"type": "array", "items": {"type": "object"}}
    assert extractSchema.parseType("list of dictionaries") == {"type": "array", "items": {"type": "object"}}


def test_parseTypeAlternativaTomaAPrimeira(extractSchema):
    resolved = extractSchema.parseType("datetime.datetime or datetime.date or string, default now + 2 days")
    assert resolved == {"type": "string", "format": "date-time"}


def test_parseTypeIgnoraDefault(extractSchema):
    assert extractSchema.parseType("integer, default 5097600 (59 days)") == {"type": "integer"}


def test_campoComDefaultVazioNaoEhPerdido(extractSchema):
    _, fields = extractSchema.parseDocstring(
        "## Parameters (optional):\n- tags [list of strings, default []]: list of strings\n"
    )
    assert [f["name"] for f in fields] == ["tags"]
    assert fields[0]["schema"] == {"type": "array", "items": {"type": "string"}}


def test_idVemPrimeiro(extractSchema):
    fields = [{"name": "amount"}, {"name": "id"}, {"name": "tags"}]
    assert [f["name"] for f in extractSchema.orderFields(fields)][0] == "id"


def test_secoesViramObrigatoriedade(extractSchema, tmpPath):
    root = _fakeSdk(tmpPath)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))
    assert code == 0, out

    schemas = yaml.safe_load(out)["components"]["schemas"]
    read, create = schemas["Widget"], schemas["WidgetCreate"]

    assert list(read["properties"])[0] == "id"
    assert set(read["required"]) == {"id", "amount", "externalId", "created"}
    assert "tags" not in read["required"]
    assert set(create["properties"]) == {"amount", "externalId", "tags", "due", "expiration", "rules"}
    assert "id" not in create["properties"]
    assert "created" not in create["properties"]


def test_todosOsCamposDoDocstringSaoExtraidos(extractSchema, tmpPath):
    root = _fakeSdk(tmpPath)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))
    properties = yaml.safe_load(out)["components"]["schemas"]["Widget"]["properties"]

    assert set(properties) == {"id", "amount", "externalId", "tags", "due", "expiration", "rules", "created"}
    assert properties["due"]["format"] == "date-time"
    assert properties["rules"] == {"type": "array", "items": {"type": "object"},
                                   "description": "list of Widget.Rule objects"}


def test_escreveArquivoComOut(extractSchema, tmpPath):
    root = _fakeSdk(tmpPath)
    destination = tmpPath / "widget.yaml"
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root), "--out", str(destination))

    assert code == 0
    assert "[OK]" in out
    assert yaml.safe_load(destination.read_text(encoding="utf-8"))["components"]["schemas"]["Widget"]


def test_raizSemStarkbankRetornaDois(tmpPath):
    infra = tmpPath / "sdk-python"
    (infra / "starkinfra").mkdir(parents=True)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(infra))

    assert code == 2
    assert "não contém starkbank/" in out


def test_moduloInexistenteRetornaDois(tmpPath):
    root = _fakeSdk(tmpPath)
    code, out = runTool("extract-schema.py", "NaoExiste", "--from", str(root))

    assert code == 2
    assert "módulo não encontrado" in out


def test_classeAusenteNoModuloReprova(tmpPath):
    root = _fakeSdk(tmpPath, module="widget", body="class Outra:\n    pass\n")
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 1
    assert "não encontrada" in out


def test_classeSemDocstringReprova(tmpPath):
    root = _fakeSdk(tmpPath, body="class Widget:\n    def __init__(self):\n        pass\n")
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 1
    assert "não tem docstring" in out


def test_docstringSemCamposReprova(tmpPath):
    root = _fakeSdk(tmpPath, body='class Widget:\n    """# Widget object\n    Sem secoes.\n    """\n')
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 1
    assert "não declara campos" in out


def test_avisaCampoDocumentadoAusenteNoInit(tmpPath):
    body = WIDGET_MODULE.replace(
        "def __init__(self, amount, external_id, tags=None, due=None, expiration=None, rules=None, id=None, created=None):",
        "def __init__(self, amount, external_id):",
    )
    root = _fakeSdk(tmpPath, body=body)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 0
    assert "[WARN]" in out
    assert "tags" in out


@pytest.mark.skipif(not (SDK_PYTHON / "starkbank").is_dir(), reason="sdk-python do Stark Bank não clonado")
@pytest.mark.parametrize("resource", ["Transaction", "Transfer", "Invoice"])
def test_extraidoBateComOSchemaAplicado(resource, tmpPath):
    destination = tmpPath / "out.yaml"
    code, _ = runTool("extract-schema.py", resource, "--from", str(SDK_PYTHON), "--out", str(destination))
    assert code == 0

    applied = REPO_ROOT / f"apis/schemas/{resource.lower()}.yaml"
    extracted = yaml.safe_load(destination.read_text(encoding="utf-8"))
    current = yaml.safe_load(applied.read_text(encoding="utf-8"))

    assert extracted == current, f"{applied} divergiu do SDK Python — reextraia"