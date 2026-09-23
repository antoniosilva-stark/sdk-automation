import yaml
import pytest
from pathlib import Path

from conftest import PYTHON_SDK, REPO_ROOT, requiresPythonSdk, runTool


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
