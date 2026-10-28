import yaml
import pytest
from pathlib import Path

from conftest import PYTHON_SDK, REPO_ROOT, WIDGET_OPERATIONS, _loadTool, requiresPythonSdk, runTool


def _schemaDir(tmpPath: Path, resource: str, document: dict) -> Path:
    directory = tmpPath / "schemas"
    directory.mkdir(exist_ok=True)
    (directory / f"{resource.lower()}.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    return directory


def _appliedResources() -> list[str]:
    names = []
    for path in (REPO_ROOT / "apis/schemas").glob("*.yaml"):
        schemas = yaml.safe_load(path.read_text(encoding="utf-8"))["components"]["schemas"]
        names.append(next(name for name in schemas if name.lower() == path.stem))
    return names


def _realSpecEntry(resource: str) -> dict:
    spec = yaml.safe_load((REPO_ROOT / "apis/spec-v2.openapi.yaml").read_text(encoding="utf-8"))
    return spec["components"]["schemas"][resource]


def _applied(resource: str) -> dict:
    path = REPO_ROOT / f"apis/schemas/{resource.lower()}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@requiresPythonSdk
def test_appliedSchemaIsInSync():
    code, out = runTool("detect-drift.py", "DictKey", "--from", str(PYTHON_SDK))

    assert code == 0, out
    assert "em sincronia" in out


@requiresPythonSdk
def test_forgedFieldIsDetected(tmpPath):
    document = _applied("DictKey")
    schema = document["components"]["schemas"]["DictKey"]
    schema["properties"]["inventado"] = {"type": "string"}
    directory = _schemaDir(tmpPath, "DictKey", document)

    code, out = runTool("detect-drift.py", "DictKey", "--from", str(PYTHON_SDK), "--schemas", str(directory))

    assert code == 1
    assert "campo saiu do sdk-python" in out
    assert "inventado" in out


@requiresPythonSdk
def test_changedTypeIsDetected(tmpPath):
    document = _applied("DictKey")
    schema = document["components"]["schemas"]["DictKey"]
    field = sorted(schema["properties"])[0]
    schema["properties"][field] = {"type": "boolean"}
    directory = _schemaDir(tmpPath, "DictKey", document)

    code, out = runTool("detect-drift.py", "DictKey", "--from", str(PYTHON_SDK), "--schemas", str(directory))

    assert code == 1
    assert f"DictKey.{field}" in out


def test_resourceWithoutVersionedSchemaHasItsOwnCode(tmpPath):
    code, out = runTool("detect-drift.py", "NaoExiste", "--schemas", str(tmpPath))

    assert code == 3
    assert "nada a comparar" in out


def test_extractionFailureIsNotSilentSuccess(tmpPath):
    document = {"components": {"schemas": {"Widget": {"properties": {}}}}}
    directory = _schemaDir(tmpPath, "Widget", document)

    code, out = runTool("detect-drift.py", "Widget", "--from", str(tmpPath), "--schemas", str(directory))

    assert code == 2
    assert "extração falhou" in out


@requiresPythonSdk
@pytest.mark.parametrize("resource", sorted(_appliedResources()))
def test_everyAppliedSchemaIsInSyncWithThePython(resource):
    code, out = runTool("detect-drift.py", resource, "--from", str(PYTHON_SDK))

    assert code == 0, out


@requiresPythonSdk
def test_onlyTheDriftedResourceIsBlocked(tmpPath):
    directory = tmpPath / "schemas"
    directory.mkdir()
    for resource in ("DictKey", "Balance"):
        document = _applied(resource)
        (directory / f"{resource.lower()}.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    drifted = yaml.safe_load((directory / "dictkey.yaml").read_text(encoding="utf-8"))
    drifted["components"]["schemas"]["DictKey"]["properties"]["inventado"] = {"type": "string"}
    (directory / "dictkey.yaml").write_text(yaml.safe_dump(drifted), encoding="utf-8")

    blocked, _ = runTool("detect-drift.py", "DictKey", "--from", str(PYTHON_SDK), "--schemas", str(directory))
    free, _ = runTool("detect-drift.py", "Balance", "--from", str(PYTHON_SDK), "--schemas", str(directory))

    assert blocked == 1
    assert free == 0


@requiresPythonSdk
def test_thePilotResourceHasAVersionedSchema():
    code, out = runTool("detect-drift.py", "SplitProfile", "--from", str(PYTHON_SDK))

    assert code == 0, out
    assert "em sincronia" in out


def _specWith(tmpPath: Path, resource: str, entry: dict) -> Path:
    path = tmpPath / "spec.yaml"
    path.write_text(yaml.safe_dump({"components": {"schemas": {resource: entry}}}), encoding="utf-8")
    return path


def test_operationExportedByThePythonButNotDeclaredIsDrift(tmpPath):
    drift = _loadTool("detect-drift.py")

    differences = drift.compareOperations(
        "Widget",
        {"x-sdk-get": True, "x-sdk-query": True, "x-sdk-update": True},
        {"$ref": "./schemas/widget.yaml", "x-sdk-get": True, "x-sdk-query": True},
    )

    assert differences == ["Widget.x-sdk-update: o sdk-python exporta, a spec nao declara"]


def test_operationRemovedFromThePythonIsDrift():
    drift = _loadTool("detect-drift.py")

    differences = drift.compareOperations("Widget", {"x-sdk-get": True}, {"x-sdk-get": True, "x-sdk-delete": True})

    assert differences == ["Widget.x-sdk-delete: a spec declara, o sdk-python nao exporta mais"]


def test_logEntityChangeIsDrift():
    drift = _loadTool("detect-drift.py")

    differences = drift.compareOperations(
        "Widget",
        {"x-sdk-log": True, "x-sdk-log-entity": "profile"},
        {"x-sdk-log": True, "x-sdk-log-entity": "widget"},
    )

    assert differences == ["Widget.x-sdk-log-entity: widget → profile"]


def test_flagsTheTemplateIgnoresAreNotDrift():
    drift = _loadTool("detect-drift.py")

    assert drift.compareOperations("Widget", {"x-sdk-get": True, "x-sdk-parse": True}, {"x-sdk-get": True}) == []


@requiresPythonSdk
def test_missingOperationFlagInTheSpecIsDetectedEndToEnd(tmpPath):
    entry = {key: value for key, value in _realSpecEntry("DictKey").items() if key != "x-sdk-get"}
    specPath = _specWith(tmpPath, "DictKey", entry)

    code, out = runTool("detect-drift.py", "DictKey", "--from", str(PYTHON_SDK), "--spec", str(specPath))

    assert code == 1
    assert "DictKey.x-sdk-get" in out



@requiresPythonSdk
def test_unexpectedFailureIsAnErrorNotDrift(tmpPath):
    directory = tmpPath / "schemas"
    directory.mkdir()
    (directory / "dictkey.yaml").write_text("components: [", encoding="utf-8")

    code, out = runTool("detect-drift.py", "DictKey", "--from", str(PYTHON_SDK), "--schemas", str(directory))

    assert code == 2, out
    assert "[ERROR]" in out


WIDGET_CLASS = '''
from starkcore.utils.resource import Resource


class Widget(Resource):
    """# Widget object
    A Widget does widget things.
    ## Parameters (required):
    - amount [integer]: amount in cents. ex: 1234
    ## Attributes (return-only):
    - id [string]: unique id returned on creation. ex: "5656"
    """

    def __init__(self, amount, id=None):
        Resource.__init__(self, id=id)
        self.amount = amount
'''


WIDGET_LOG_OPERATIONS = '''
def get(id, user=None):
    """# Retrieve a specific widget.Log
    Receive a single Widget.Log object previously created by the Stark Bank API by its id
    ## Parameters (required):
    - id [string]: object unique id. ex: "5656565656565656"
    ## Return:
    - widget.Log object with updated attributes
    """
    return rest.get_id(resource=_resource, id=id, user=user)
'''


def _appliedWidget(tmpPath: Path, operations: str = WIDGET_OPERATIONS, log: bool = False) -> tuple[Path, Path, Path]:
    root = tmpPath / "sdk-python"
    package = root / "starkbank" / "widget"
    package.mkdir(parents=True)
    (package / "__widget.py").write_text(WIDGET_CLASS + operations, encoding="utf-8")
    (package / "__init__.py").write_text("from .__widget import get, query, page\n", encoding="utf-8")
    if log:
        (package / "log").mkdir()
        (package / "log" / "__init__.py").write_text("from .__log import get\n", encoding="utf-8")
        (package / "log" / "__log.py").write_text(WIDGET_LOG_OPERATIONS, encoding="utf-8")
    spec = tmpPath / "spec.yaml"
    spec.write_text("openapi: 3.1.0\ninfo:\n  title: Test\n  version: 1.0.0\npaths: {}\n"
                    "components:\n  schemas: {}\n", encoding="utf-8")
    schemas = tmpPath / "schemas"
    schemas.mkdir()
    code, out = runTool("apply-schema.py", "Widget", "--from", str(root), "--spec", str(spec), "--schemas", str(schemas))

    assert code == 0, out
    return (root, spec, schemas)


def _drift(root: Path, spec: Path, schemas: Path) -> tuple[int, str]:
    return runTool("detect-drift.py", "Widget", "--from", str(root), "--spec", str(spec), "--schemas", str(schemas))


def test_unchangedOperationDocstringsAreNotDrift(tmpPath):
    code, out = _drift(*_appliedWidget(tmpPath))

    assert code == 0, out
    assert "em sincronia" in out


def test_onlyAnOperationDocstringChangingIsDrift(tmpPath):
    root, spec, schemas = _appliedWidget(tmpPath)
    module = root / "starkbank" / "widget" / "__widget.py"
    module.write_text(module.read_text(encoding="utf-8").replace("# Retrieve Widgets", "# Retrieve all Widgets"),
                      encoding="utf-8")

    code, out = _drift(root, spec, schemas)

    assert code == 1, out
    assert "Widget.x-sdk-docs.query: docstring mudou (summary)" in out
    assert "1 divergência" in out


def test_aParameterChangingInThePageDocstringIsDrift(tmpPath):
    root, spec, schemas = _appliedWidget(tmpPath)
    module = root / "starkbank" / "widget" / "__widget.py"
    module.write_text(module.read_text(encoding="utf-8").replace(
        "- cursor [string, default None]:", "- cursor [string, default \"\"]:"), encoding="utf-8")

    code, out = _drift(root, spec, schemas)

    assert code == 1, out
    assert "Widget.x-sdk-docs.page: docstring mudou (params)" in out


def test_operationLosingItsDocstringIsDrift(tmpPath):
    root, spec, schemas = _appliedWidget(tmpPath)
    module = root / "starkbank" / "widget" / "__widget.py"
    module.write_text(WIDGET_CLASS + "\n\ndef get(id, user=None):\n    return id\n"
                      + WIDGET_OPERATIONS[WIDGET_OPERATIONS.index("def query"):], encoding="utf-8")

    code, out = _drift(root, spec, schemas)

    assert code == 1, out
    assert "Widget.x-sdk-docs.get: o schema versionado documenta, o sdk-python nao documenta mais" in out


def test_operationGainingADocstringIsDrift(tmpPath):
    undocumented = WIDGET_OPERATIONS.replace(
        '''    """# Retrieve a specific Widget
    Receive a single Widget object previously created in the Stark Bank API by its id
    ## Parameters (required):
    - id [string]: object unique id. ex: "5656565656565656"
    ## Parameters (optional):
    - user [Organization/Project object, default None]: Organization or Project object. Not necessary if starkbank.user
        was set before function call
    ## Return:
    - Widget object with updated attributes
    """
''', "", 1)
    root, spec, schemas = _appliedWidget(tmpPath, undocumented)
    documented = yaml.safe_load((schemas / "widget.yaml").read_text(encoding="utf-8"))
    assert set(documented["components"]["schemas"]["Widget"]["x-sdk-docs"]) == {"query", "page"}
    module = root / "starkbank" / "widget" / "__widget.py"
    module.write_text(WIDGET_CLASS + WIDGET_OPERATIONS, encoding="utf-8")

    code, out = _drift(root, spec, schemas)

    assert code == 1, out
    assert "Widget.x-sdk-docs.get: o sdk-python documenta, o schema versionado nao tem" in out
    assert "1 divergência" in out


def test_logDocstringChangingIsDriftUnderTheLogPrefix(tmpPath):
    root, spec, schemas = _appliedWidget(tmpPath, log=True)
    assert _drift(root, spec, schemas)[0] == 0
    module = root / "starkbank" / "widget" / "log" / "__log.py"
    module.write_text(module.read_text(encoding="utf-8").replace("# Retrieve a specific widget.Log", "# Get a widget.Log"),
                      encoding="utf-8")

    code, out = _drift(root, spec, schemas)

    assert code == 1, out
    assert "Widget.x-sdk-docs.log.get: docstring mudou (summary)" in out
    assert "1 divergência" in out

