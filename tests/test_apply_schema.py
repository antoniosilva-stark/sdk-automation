import os
import yaml
import subprocess
import pytest
from pathlib import Path

from conftest import runTool


WIDGET_MODULE = '''
from starkcore.utils.resource import Resource


class Widget(Resource):
    """# Widget object
    A Widget does widget things.
    ## Parameters (required):
    - amount [integer]: amount in cents. ex: 1234
    - external_id [string]: unique id. ex: "abc"
    ## Attributes (return-only):
    - id [string]: unique id returned on creation. ex: "5656"
    - status [string]: current status. ex: "created"
    - created [datetime.datetime]: creation datetime. ex: datetime(2020, 3, 10)
    """

    def __init__(self, amount, external_id, id=None, status=None, created=None):
        Resource.__init__(self, id=id)
        self.amount = amount
        self.external_id = external_id
        self.status = status
        self.created = created
'''

STUB_SPEC = """openapi: 3.1.0
info:
  title: Test
  version: 1.0.0
paths:
  /widget:
    get:
      operationId: queryWidget
      responses:
        '200':
          description: ok
components:
  schemas:
    Widget:
      type: object
      description: A Widget resource
      properties:
        id:
          type: string
          description: Unique ID
        created:
          type: string
          format: date-time
          description: Creation timestamp
      required:
      - id
      - created
    WidgetCreate:
      type: object
      description: Widget creation request
      properties: {}
"""


def _sdk(tmpPath: Path, exports: str = "create, get, query, page") -> Path:
    """A referencia real e sempre um clone, entao a forjada tambem: o schema aplicado
    carrega o SHA de origem e o lint reprova quem nao declara procedencia."""
    root = tmpPath / "sdk-python"
    package = root / "starkbank" / "widget"
    package.mkdir(parents=True)
    (package / "__widget.py").write_text(WIDGET_MODULE, encoding="utf-8")
    (package / "__init__.py").write_text(f"from .__widget import {exports}\n", encoding="utf-8")

    environment = {"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@local",
                   "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@local",
                   "PATH": os.environ.get("PATH", "")}
    for command in (["git", "init", "--quiet"], ["git", "add", "."],
                    ["git", "commit", "--quiet", "-m", "forged reference"]):
        subprocess.run(command, cwd=root, env=environment, check=True, capture_output=True)
    return root


def _workspace(tmpPath: Path) -> tuple[Path, Path]:
    spec = tmpPath / "spec.yaml"
    spec.write_text(STUB_SPEC, encoding="utf-8")
    schemas = tmpPath / "schemas"
    schemas.mkdir()
    return (spec, schemas)


def _apply(tmpPath: Path, resource: str = "Widget", exports: str = "create, get, query, page", *extra: str):
    root = _sdk(tmpPath, exports)
    spec, schemas = _workspace(tmpPath)
    code, out = runTool("apply-schema.py", resource, "--from", str(root),
                        "--spec", str(spec), "--schemas", str(schemas), *extra)
    return (code, out, spec, schemas)


def test_schemaIsWrittenToTheSchemasDirectory(tmpPath):
    code, out, _, schemas = _apply(tmpPath)

    assert code == 0, out
    applied = yaml.safe_load((schemas / "widget.yaml").read_text(encoding="utf-8"))
    assert applied["components"]["schemas"]["Widget"]["properties"]["amount"]["type"] == "integer"


def test_writtenFileIsIdenticalToExtracted(tmpPath):
    """O golden compara os dois; escrever algo diferente do extrator quebraria o guard."""
    root = _sdk(tmpPath)
    spec, schemas = _workspace(tmpPath)
    runTool("apply-schema.py", "Widget", "--from", str(root), "--spec", str(spec), "--schemas", str(schemas))
    code, extracted = runTool("extract-schema.py", "Widget", "--from", str(root))

    assert code == 0
    assert (schemas / "widget.yaml").read_text(encoding="utf-8") == extracted


def test_stubBecomesRefWithSiblingFlags(tmpPath):
    code, out, spec, _ = _apply(tmpPath)
    assert code == 0, out

    schemas = yaml.safe_load(spec.read_text(encoding="utf-8"))["components"]["schemas"]

    assert schemas["Widget"]["$ref"].endswith("widget.yaml#/components/schemas/Widget")
    assert schemas["Widget"]["x-sdk-get"] is True
    assert schemas["WidgetCreate"]["$ref"].endswith("widget.yaml#/components/schemas/WidgetCreate")


def test_putIsSuppressedWithReason(tmpPath):
    """Decisao 43: Rest.put nao existe no sdk-java. Declarar geraria codigo que nao compila."""
    code, out, spec, _ = _apply(tmpPath, exports="put, get, query, page")

    assert code == 0, out
    assert "x-sdk-put" not in spec.read_text(encoding="utf-8")
    assert "put" in out and "suprimid" in out


def test_onlyFlagWithTemplateSectionIsWritten(tmpPath):
    """Escrever flag que o template nao produz faria o lint considerar completo um
    recurso cujo gerado nao tem a operacao — plausivel e errado."""
    code, out, spec, _ = _apply(tmpPath, exports="put, get, query, delete, pdf")

    assert code == 0, out
    written = yaml.safe_load(spec.read_text(encoding="utf-8"))["components"]["schemas"]["Widget"]
    flags = {key for key in written if key.startswith("x-sdk-")}

    assert flags == {"x-sdk-get", "x-sdk-query", "x-sdk-delete", "x-sdk-pdf"}


def test_resourceMissingInPythonWritesNothing(tmpPath):
    root = _sdk(tmpPath)
    spec, schemas = _workspace(tmpPath)
    before = spec.read_text(encoding="utf-8")
    code, out = runTool("apply-schema.py", "NaoExiste", "--from", str(root),
                        "--spec", str(spec), "--schemas", str(schemas))

    assert code != 0
    assert spec.read_text(encoding="utf-8") == before
    assert list(schemas.iterdir()) == []


def _emptySpec(tmpPath: Path) -> tuple[Path, Path]:
    spec = tmpPath / "spec.yaml"
    spec.write_text("openapi: 3.1.0\ninfo:\n  title: Test\n  version: 1.0.0\npaths: {}\n"
                    "components:\n  schemas: {}\n", encoding="utf-8")
    schemas = tmpPath / "schemas"
    schemas.mkdir()
    return (spec, schemas)


def test_missingResourceIsCreatedInTheSpec(tmpPath):
    """15 recursos do Python nao estao entre os 60 nomes da spec. Sem criar, eles nunca
    chegam ao Java, por mais que o tooling os alcance."""
    root = _sdk(tmpPath)
    spec, schemas = _emptySpec(tmpPath)

    code, out = runTool("apply-schema.py", "Widget", "--from", str(root),
                        "--spec", str(spec), "--schemas", str(schemas))

    assert code == 0, out
    written = yaml.safe_load(spec.read_text(encoding="utf-8"))
    assert written["components"]["schemas"]["Widget"]["$ref"].endswith("widget.yaml#/components/schemas/Widget")
    assert written["components"]["schemas"]["WidgetCreate"]["$ref"].endswith("#/components/schemas/WidgetCreate")


def test_createdResourceGetsPathsDerivedFromFlags(tmpPath):
    root = _sdk(tmpPath, exports="create, get, query, page")
    spec, schemas = _emptySpec(tmpPath)

    code, out = runTool("apply-schema.py", "Widget", "--from", str(root),
                        "--spec", str(spec), "--schemas", str(schemas))

    assert code == 0, out
    paths = yaml.safe_load(spec.read_text(encoding="utf-8"))["paths"]

    assert set(paths) == {"/widget", "/widget/{id}"}
    assert set(paths["/widget"]) == {"post", "get"}
    assert set(paths["/widget/{id}"]) == {"get"}
    assert paths["/widget"]["post"]["operationId"] == "createWidget"


def test_pathsReflectOnlyDeclaredOperations(tmpPath):
    root = _sdk(tmpPath, exports="get, query")
    spec, schemas = _emptySpec(tmpPath)
    runTool("apply-schema.py", "Widget", "--from", str(root), "--spec", str(spec), "--schemas", str(schemas))

    paths = yaml.safe_load(spec.read_text(encoding="utf-8"))["paths"]

    assert "post" not in paths["/widget"]
    assert set(paths["/widget/{id}"]) == {"get"}


def test_compoundNameBecomesKebabPath(tmpPath):
    root = _sdk(tmpPath)
    (root / "starkbank" / "merchantcard").mkdir()
    (root / "starkbank" / "merchantcard" / "__merchantcard.py").write_text(
        WIDGET_MODULE.replace("Widget", "MerchantCard"), encoding="utf-8")
    (root / "starkbank" / "merchantcard" / "__init__.py").write_text(
        "from .__merchantcard import get, query\n", encoding="utf-8")
    spec, schemas = _emptySpec(tmpPath)

    code, out = runTool("apply-schema.py", "MerchantCard", "--from", str(root),
                        "--spec", str(spec), "--schemas", str(schemas))

    assert code == 0, out
    paths = yaml.safe_load(spec.read_text(encoding="utf-8"))["paths"]
    assert "/merchant-card" in paths


def test_invalidSpecDoesNotReplaceTheOriginal(tmpPath):
    """Escreve em copia e valida antes de trocar: a spec real tem 5700 linhas."""
    root = _sdk(tmpPath)
    spec = tmpPath / "spec.yaml"
    spec.write_text("components:\n  schemas:\n    Widget: {type: object\n", encoding="utf-8")
    schemas = tmpPath / "schemas"
    schemas.mkdir()
    before = spec.read_text(encoding="utf-8")

    code, _ = runTool("apply-schema.py", "Widget", "--from", str(root),
                      "--spec", str(spec), "--schemas", str(schemas))

    assert code == 2
    assert spec.read_text(encoding="utf-8") == before


def test_twoApplicationsProduceTheSameResult(tmpPath):
    root = _sdk(tmpPath)
    spec, schemas = _workspace(tmpPath)
    args = ("apply-schema.py", "Widget", "--from", str(root), "--spec", str(spec), "--schemas", str(schemas))

    runTool(*args)
    first = spec.read_text(encoding="utf-8")
    runTool(*args)

    assert spec.read_text(encoding="utf-8") == first


def test_appliedResourcePassesTheLint(tmpPath):
    code, out, spec, _ = _apply(tmpPath)
    assert code == 0, out

    code, out = runTool("lint-spec.py", "--spec", str(spec), "--quiet", "--require", "Widget")
    assert code == 0, out
