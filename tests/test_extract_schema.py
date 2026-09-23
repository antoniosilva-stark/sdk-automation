import yaml
import pytest
from pathlib import Path

from conftest import PYTHON_SDK, REPO_ROOT, requiresPythonSdk, runTool

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


BARE_MODULE = '''
from starkcore.utils.resource import Resource
from starkcore.utils.checks import check_datetime, check_datetime_or_date


class Widget(Resource):
    """# Widget object
    Check out our API Documentation at https://starkbank.com/docs/api#widget
    """

    def __init__(self, id=None, ending=None, holder_name=None, status=None, tags=None,
                 expiration=None, created=None):
        Resource.__init__(self, id=id)
        self.ending = ending
        self.holder_name = holder_name
        self.status = status
        self.tags = tags
        self.expiration = check_datetime_or_date(expiration)
        self.created = check_datetime(created)
'''


def _fakeSdk(tmpPath: Path, module: str = "widget", body: str = WIDGET_MODULE) -> Path:
    root = tmpPath / "sdk-python"
    package = root / "starkbank" / module
    package.mkdir(parents=True)
    (package / f"__{module}.py").write_text(body, encoding="utf-8")
    return root


def _withInit(root: Path, module: str, exports: str, log: bool = False) -> Path:
    package = root / "starkbank" / module
    (package / "__init__.py").write_text(f"from .__{module} import {exports}\n", encoding="utf-8")
    if log:
        (package / "log").mkdir()
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


def test_parseTypeMapsBasicTypes(extractSchema):
    assert extractSchema.parseType("integer") == {"type": "integer"}
    assert extractSchema.parseType("string") == {"type": "string"}
    assert extractSchema.parseType("float") == {"type": "number", "format": "float"}
    assert extractSchema.parseType("boolean") == {"type": "boolean"}
    assert extractSchema.parseType("datetime.datetime") == {"type": "string", "format": "date-time"}


def test_parseTypeLists(extractSchema):
    assert extractSchema.parseType("list of strings") == {"type": "array", "items": {"type": "string"}}
    assert extractSchema.parseType("list of Widget.Rules") == {"type": "array", "items": {"type": "object"}}
    assert extractSchema.parseType("list of dictionaries") == {"type": "array", "items": {"type": "object"}}


def test_parseTypeAlternativeTakesTheFirst(extractSchema):
    resolved = extractSchema.parseType("datetime.datetime or datetime.date or string, default now + 2 days")
    assert resolved == {"type": "string", "format": "date-time"}


def test_parseTypeIgnoresDefault(extractSchema):
    assert extractSchema.parseType("integer, default 5097600 (59 days)") == {"type": "integer"}


def test_fieldWithEmptyDefaultIsNotLost(extractSchema):
    _, fields = extractSchema.parseDocstring(
        "## Parameters (optional):\n- tags [list of strings, default []]: list of strings\n"
    )
    assert [f["name"] for f in fields] == ["tags"]
    assert fields[0]["schema"] == {"type": "array", "items": {"type": "string"}}


def test_idComesFirst(extractSchema):
    fields = [{"name": "amount"}, {"name": "id"}, {"name": "tags"}]
    assert [f["name"] for f in extractSchema.orderFields(fields)][0] == "id"


def test_sectionsBecomeRequiredness(extractSchema, tmpPath):
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


def test_everyDocstringFieldIsExtracted(extractSchema, tmpPath):
    root = _fakeSdk(tmpPath)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))
    properties = yaml.safe_load(out)["components"]["schemas"]["Widget"]["properties"]

    assert set(properties) == {"id", "amount", "externalId", "tags", "due", "expiration", "rules", "created"}
    assert properties["due"]["format"] == "date-time"
    assert properties["rules"] == {"type": "array", "items": {"type": "object"},
                                   "description": "list of Widget.Rule objects"}


def test_writesFileWithOut(extractSchema, tmpPath):
    root = _fakeSdk(tmpPath)
    destination = tmpPath / "widget.yaml"
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root), "--out", str(destination))

    assert code == 0
    assert "[OK]" in out
    assert yaml.safe_load(destination.read_text(encoding="utf-8"))["components"]["schemas"]["Widget"]


def test_rootWithoutStarkbankReturnsTwo(tmpPath):
    infra = tmpPath / "sdk-python"
    (infra / "starkinfra").mkdir(parents=True)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(infra))

    assert code == 2
    assert "não contém starkbank/" in out


def test_docstringWithoutFieldsFallsBackToInit(tmpPath):
    root = _fakeSdk(tmpPath, body=BARE_MODULE)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 0, out
    properties = yaml.safe_load(out)["components"]["schemas"]["Widget"]["properties"]

    assert list(properties) == ["id", "ending", "holderName", "status", "tags", "expiration", "created"]


def test_inferredTypeIsMarkedAsInferred(tmpPath):
    root = _fakeSdk(tmpPath, body=BARE_MODULE)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    properties = yaml.safe_load(out)["components"]["schemas"]["Widget"]["properties"]

    assert properties["created"]["format"] == "date-time"
    assert "inferido" in properties["status"]["description"]


def test_fallbackDoesNotOverrideExistingDocstring(tmpPath):
    root = _fakeSdk(tmpPath)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    properties = yaml.safe_load(out)["components"]["schemas"]["Widget"]["properties"]

    assert properties["amount"]["type"] == "integer"
    assert "inferido" not in properties["amount"]["description"]


READONLY_MODULE = '''
from starkcore.utils.resource import Resource


class Widget(Resource):
    """# Widget object
    A Widget does widget things.
    ## Attributes (return-only):
    - id [string]: unique id. ex: "5656"
    - status [string]: current status. ex: "created"
    - created [string]: creation datetime. ex: "2020-03-10"
    """

    def __init__(self, id=None, status=None, created=None):
        Resource.__init__(self, id=id)
        self.status = status
        self.created = created
'''


CONDITIONAL_MODULE = '''
from starkcore.utils.resource import Resource


class Widget(Resource):
    """# Widget object
    A Widget does widget things.
    ## Parameters (conditionally required):
    - code [string, default None]: category code. ex: "fastFood"
    - type [string, default None]: category type. ex: "food"
    ## Attributes (return only):
    - id [string]: unique id. ex: "5656"
    """

    def __init__(self, code=None, type=None, id=None):
        Resource.__init__(self, id=id)
        self.code = code
        self.type = type
'''


def test_lessCommonSectionsAreNotLost(tmpPath):
    root = _fakeSdk(tmpPath, body=CONDITIONAL_MODULE)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 0, out
    properties = yaml.safe_load(out)["components"]["schemas"]["Widget"]["properties"]

    assert set(properties) == {"id", "code", "type"}


def test_resourceWithoutCreateFieldsEmitsEmptyObjectNotNull(tmpPath):
    root = _fakeSdk(tmpPath, body=READONLY_MODULE)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 0, out
    create = yaml.safe_load(out)["components"]["schemas"]["WidgetCreate"]

    assert create["properties"] == {}


def test_conventionAppliesInt64OnMonetaryFields(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("integerFormats:\n  amount: int64\n", encoding="utf-8")

    assert extractSchema.integerFormat("amount", conventions) == "int64"
    assert extractSchema.integerFormat("fee", conventions) is None


def test_missingConventionDoesNotBreak(extractSchema, tmpPath):
    assert extractSchema.integerFormat("amount", tmpPath / "nao-existe.yaml") is None


def test_writtenSchemaCarriesTheConventionFormat(tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "create, get, query, page")
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 0, out
    schema = yaml.safe_load(out)["components"]["schemas"]["Widget"]
    assert schema["properties"]["amount"]["format"] == "int64"


def test_operationsComeFromTheInitExports(extractSchema, tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "create, get, query, page")

    operations = extractSchema.declaredOperations(root, "Widget")

    assert operations["flags"] == ["x-sdk-create", "x-sdk-get", "x-sdk-page", "x-sdk-query"]
    assert operations["unsupported"] == []


def test_putIsDerivedWhenPythonExportsIt(extractSchema, tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "put, get, query, page")

    assert "x-sdk-put" in extractSchema.declaredOperations(root, "Widget")["flags"]


def test_operationWithoutFlagIsAnnouncedNotIgnored(extractSchema, tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "get, query, parse, response")

    operations = extractSchema.declaredOperations(root, "Widget")

    assert operations["flags"] == ["x-sdk-get", "x-sdk-query"]
    assert operations["unsupported"] == ["parse", "response"]


def test_logComesFromTheDirectoryPresence(extractSchema, tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "get", log=True)

    assert "x-sdk-log" in extractSchema.declaredOperations(root, "Widget")["flags"]


def test_classWithoutOperationsBecomesDataOnly(extractSchema, tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "Widget, parse_rules")

    operations = extractSchema.declaredOperations(root, "Widget")

    assert operations["flags"] == ["x-sdk-data-only"]
    assert operations["unsupported"] == ["parse_rules"]


def test_exportedLogIsNotCountedAsFlaglessOperation(extractSchema, tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "get, query, log", log=True)

    operations = extractSchema.declaredOperations(root, "Widget")

    assert "x-sdk-log" in operations["flags"]
    assert operations["unsupported"] == []


def test_missingInitInventsNoOperation(extractSchema, tmpPath):
    root = _fakeSdk(tmpPath)

    operations = extractSchema.declaredOperations(root, "Widget")

    assert operations["flags"] == []
    assert operations["missingInit"] is True


def test_documentDoesNotChangeWithTheOperations(tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "create, get, query, page")
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 0
    assert "x-sdk" not in out


def test_operationsAreEmittedWithTheDedicatedFlag(tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "create, get, query, page")
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root), "--operations")

    assert code == 0
    assert yaml.safe_load(out) == {
        "x-sdk-create": True, "x-sdk-get": True, "x-sdk-page": True, "x-sdk-query": True,
    }


def test_operationsStdoutIsPureYaml(tmpPath):
    root = _withInit(_fakeSdk(tmpPath), "widget", "get, query, parse, response")
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root), "--operations")

    assert code == 0, out
    assert yaml.safe_load(out) == {"x-sdk-get": True, "x-sdk-query": True}


@requiresPythonSdk
def test_realDictKeyOperations(extractSchema):
    operations = extractSchema.declaredOperations(PYTHON_SDK, "DictKey")

    assert operations["flags"] == ["x-sdk-get", "x-sdk-page", "x-sdk-query"]


def test_missingRootSaysItDoesNotExist(tmpPath):
    code, out = runTool("extract-schema.py", "Widget", "--from", str(tmpPath / "nao-existe"))

    assert code == 2
    assert "não existe" in out
    assert "Stark Infra" not in out


def test_defaultRootIsTheVendoredClone(extractSchema, monkeypatch):
    monkeypatch.delenv("SDK_PYTHON", raising=False)

    assert extractSchema.resolveRoot(None) == extractSchema.VENDORED_ROOT


def test_envOverridesTheVendoredClone(extractSchema, tmpPath, monkeypatch):
    local = tmpPath / "meu-clone"
    (local / "starkbank").mkdir(parents=True)
    monkeypatch.setenv("SDK_PYTHON", str(local))

    assert extractSchema.resolveRoot(None) == local


def test_defaultRootIgnoresInvalidEnv(extractSchema, tmpPath, monkeypatch):
    infra = tmpPath / "sdk-python"
    (infra / "starkinfra").mkdir(parents=True)
    monkeypatch.setenv("SDK_PYTHON", str(infra))

    assert extractSchema.resolveRoot(None) != infra


def test_explicitRootWinsTheResolution(extractSchema, tmpPath, monkeypatch):
    explicit = tmpPath / "explicita"
    (explicit / "starkbank").mkdir(parents=True)
    monkeypatch.setenv("SDK_PYTHON", str(tmpPath / "outra"))

    assert extractSchema.resolveRoot(str(explicit)) == explicit


def test_missingModuleReturnsTwo(tmpPath):
    root = _fakeSdk(tmpPath)
    code, out = runTool("extract-schema.py", "NaoExiste", "--from", str(root))

    assert code == 2
    assert "módulo não encontrado" in out


def test_classMissingFromTheModuleFails(tmpPath):
    root = _fakeSdk(tmpPath, module="widget", body="class Outra:\n    pass\n")
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 1
    assert "não encontrada" in out


def test_classWithoutDocstringFails(tmpPath):
    root = _fakeSdk(tmpPath, body="class Widget:\n    def __init__(self):\n        pass\n")
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 1
    assert "não tem docstring" in out


def test_docstringWithoutFieldsFails(tmpPath):
    root = _fakeSdk(tmpPath, body='class Widget:\n    """# Widget object\n    Sem secoes.\n    """\n')
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 1
    assert "não declara campos" in out


def test_warnsAboutDocumentedFieldMissingFromInit(tmpPath):
    body = WIDGET_MODULE.replace(
        "def __init__(self, amount, external_id, tags=None, due=None, expiration=None, rules=None, id=None, created=None):",
        "def __init__(self, amount, external_id):",
    )
    root = _fakeSdk(tmpPath, body=body)
    code, out = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 0
    assert "[WARN]" in out
    assert "tags" in out


def appliedSchemas() -> list[tuple[str, Path]]:
    applied = []
    for path in sorted((REPO_ROOT / "apis/schemas").glob("*.yaml")):
        schemas = yaml.safe_load(path.read_text(encoding="utf-8"))["components"]["schemas"]
        resource = next(name for name in schemas if not name.endswith("Create"))
        applied.append((resource, path))
    return applied


def test_everyAppliedSchemaEntersTheGolden():
    resources = [resource for resource, _ in appliedSchemas()]

    assert len(resources) >= 4
    assert "DictKey" in resources


@requiresPythonSdk
@pytest.mark.parametrize("resource", [resource for resource, _ in appliedSchemas()])
def test_extractedMatchesTheAppliedSchema(resource, tmpPath):
    destination = tmpPath / "out.yaml"
    code, _ = runTool("extract-schema.py", resource, "--from", str(PYTHON_SDK), "--out", str(destination))
    assert code == 0

    applied = REPO_ROOT / f"apis/schemas/{resource.lower()}.yaml"
    extracted = yaml.safe_load(destination.read_text(encoding="utf-8"))
    current = yaml.safe_load(applied.read_text(encoding="utf-8"))

    assert extracted == current, f"{applied} divergiu do SDK Python — reextraia"

@requiresPythonSdk
def test_monetaryFieldInferredFromInitFollowsTheConvention():
    code, out = runTool("extract-schema.py", "DynamicBrcode", "--from", str(PYTHON_SDK))
    assert code == 0, out

    schema = yaml.safe_load(out)["components"]["schemas"]["DynamicBrcode"]
    amount = schema["properties"]["amount"]
    assert (amount["type"], amount["format"]) == ("integer", "int64")


def test_noAppliedSchemaDeclaresMoneyAsString():
    conventions = yaml.safe_load((REPO_ROOT / "apis/type-conventions.yaml").read_text(encoding="utf-8"))
    monetarios = set(conventions["integerFormats"])

    erradas = []
    for path in sorted((REPO_ROOT / "apis/schemas").glob("*.yaml")):
        for name, schema in yaml.safe_load(path.read_text(encoding="utf-8"))["components"]["schemas"].items():
            for field, definition in (schema.get("properties") or {}).items():
                if field in monetarios and (definition or {}).get("type") != "integer":
                    erradas.append(f"{path.name}:{name}.{field} = {(definition or {}).get('type')}")

    assert erradas == [], f"valor monetario fora da convencao: {erradas}"


def test_typeUnionPicksTheAlternativeTheSdkCanExpress(extractSchema):
    assert extractSchema.parseType("DateInterval or integer") == {"type": "integer"}
    assert extractSchema.parseType("string or integer") == {"type": "string"}
    assert extractSchema.parseType("DateInterval or Coisa") == {"type": "string"}


@requiresPythonSdk
def test_splitProfileDelayStaysInteger():
    code, out = runTool("extract-schema.py", "SplitProfile", "--from", str(PYTHON_SDK))
    assert code == 0, out

    schema = yaml.safe_load(out)["components"]["schemas"]["SplitProfile"]
    assert schema["properties"]["delay"]["type"] == "integer"
    assert schema["properties"]["interval"]["type"] == "string"


@requiresPythonSdk
def test_positionalParameterIsRequiredEvenWhenTheDocstringSaysOptional():
    code, out = runTool("extract-schema.py", "SplitProfile", "--from", str(PYTHON_SDK))
    assert code == 0, out

    schemas = yaml.safe_load(out)["components"]["schemas"]
    assert set(schemas["SplitProfileCreate"].get("required") or []) == {"delay", "interval"}


@requiresPythonSdk
def test_defaultedParameterStaysOptional():
    code, out = runTool("extract-schema.py", "SplitProfile", "--from", str(PYTHON_SDK))

    schemas = yaml.safe_load(out)["components"]["schemas"]
    assert "tags" not in (schemas["SplitProfileCreate"].get("required") or [])


def test_typeAlternativesSeparatedByCommaAreConsidered(extractSchema):
    resolved = extractSchema.parseType("datetime.date, datetime.datetime or string, default now")

    assert resolved == {"type": "string", "format": "date-time"}


def test_dateTimeWinsOverDateWhenBothAreAccepted(extractSchema):
    assert extractSchema.parseType("datetime.date or datetime.datetime")["format"] == "date-time"
    assert extractSchema.parseType("datetime.date")["format"] == "date"


def test_defaultClauseIsNotATypeAlternative(extractSchema):
    assert extractSchema.parseType("list of strings, default []") == {
        "type": "array", "items": {"type": "string"}}


def test_floatCarriesItsFormat(extractSchema):
    assert extractSchema.parseType("float") == {"type": "number", "format": "float"}


def test_optionsBecomeAnEnum(extractSchema):
    doc = '- interval [string]: frequency, default "week". Options: "day", "week", "month"'
    _, fields = extractSchema.parseDocstring("## Parameters (optional):\n" + doc)

    assert fields[0]["schema"]["enum"] == ["day", "week", "month"]


def test_exampleNeverBecomesAnEnum(extractSchema):
    doc = '- status [string]: current transfer status. ex: "success" or "failed"'
    _, fields = extractSchema.parseDocstring("## Attributes (return-only):\n" + doc)

    assert "enum" not in fields[0]["schema"]
