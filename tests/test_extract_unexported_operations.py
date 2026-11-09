import ast
import shutil
import subprocess
import pytest
from pathlib import Path

from conftest import (
    PYTHON_SDK, REPO_ROOT, WIDGET_OPERATIONS, requiresGenerator, requiresJavaSdk, requiresJavac, requiresPythonSdk,
)
from test_extract_schema import WIDGET_MODULE, _fakeSdk, _withInit

REAL_CORPORATE_PURCHASE = REPO_ROOT / "_references/sdk-java/src/main/java/com/starkbank/CorporatePurchase.java"
SOURCEPATH = REPO_ROOT / "_references/sdk-java/src/main/java"


def _defined(tmpPath: Path, extra: str, exports: str = "Widget", log: bool = False) -> Path:
    root = _fakeSdk(tmpPath, body=WIDGET_MODULE + extra)
    return _withInit(root, "widget", exports, log=log)


def _topLevelFunctions(path: Path) -> set:
    return {node.name for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.FunctionDef)}


def _exportedNames(path: Path) -> set:
    return {alias.asname or alias.name for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.ImportFrom) for alias in node.names}


def _packages() -> list:
    return sorted(init.parent for init in (PYTHON_SDK / "starkbank").rglob("__init__.py") if init.parent.name != "starkbank")


def _definedButNotExported(extractSchema) -> set:
    found = set()
    for package in _packages():
        module = package / f"__{package.name}.py"
        if not module.is_file():
            continue
        names = _topLevelFunctions(module) & set(extractSchema.OPERATION_FLAGS)
        missing = names - _exportedNames(package / "__init__.py")
        relative = package.relative_to(PYTHON_SDK / "starkbank").as_posix()
        found |= {(relative, name) for name in missing}
    return found


def _exportedOnlyFlags(extractSchema, resource: str) -> list:
    package = PYTHON_SDK / "starkbank" / resource.lower()
    exported = {name for name in _exportedNames(package / "__init__.py") if name[:1].islower()}
    flags = {extractSchema.OPERATION_FLAGS[name] for name in exported if name in extractSchema.OPERATION_FLAGS}
    if (package / "log").is_dir():
        flags |= {"x-sdk-log"}
    return sorted(flags) or ["x-sdk-data-only"]


def test_aFunctionDefinedInTheResourceModuleButNotExportedIsDeclaredWithItsDocs(extractSchema, tmpPath):
    root = _defined(tmpPath, WIDGET_OPERATIONS)

    operations = extractSchema.declaredOperations(root, "Widget")

    assert "x-sdk-page" in operations["flags"]
    assert operations["unsupported"] == []
    assert "page" in extractSchema.declaredDocs(root, "Widget")


def test_definedFunctionsOutsideTheOperationSetNestedOrPrivateAreNotDeclared(extractSchema, tmpPath):
    root = _defined(tmpPath, '''
def helper(id):
    """Not an operation."""


def _page(cursor=None):
    """Private."""


def wrapper():
    def delete(id):
        """Nested in a function."""


class Holder:
    def cancel(self, id):
        """Method of a class."""
''')

    operations = extractSchema.declaredOperations(root, "Widget")

    assert operations["flags"] == ["x-sdk-data-only"]
    assert operations["unsupported"] == []
    assert extractSchema.declaredDocs(root, "Widget") == {}


def test_anExportedOperationIsDeclaredOnceAndKeepsTheOperationOrder(extractSchema, tmpPath):
    root = _defined(tmpPath, WIDGET_OPERATIONS, exports="Widget, query, get, page")

    operations = extractSchema.declaredOperations(root, "Widget")

    assert operations["flags"] == ["x-sdk-get", "x-sdk-page", "x-sdk-query"]
    assert list(extractSchema.declaredDocs(root, "Widget")) == ["get", "query", "page"]


@requiresPythonSdk
def test_noRealLogOrNestedModuleDefinesAnOperationItDoesNotExport(extractSchema):
    for package in _packages():
        if package.parent.name == "starkbank":
            continue
        module = package / f"__{package.name}.py"
        exported = {name for name in _exportedNames(package / "__init__.py") if name in extractSchema.OPERATION_FLAGS}
        defined = _topLevelFunctions(module) & set(extractSchema.OPERATION_FLAGS) if module.is_file() else set()
        assert defined <= exported, package


@requiresPythonSdk
def test_theOnlyOperationDefinedButNotExportedInTheRealSdkPythonIsCorporatePurchasePage(extractSchema):
    assert _packages()
    assert _definedButNotExported(extractSchema) == {("corporatepurchase", "page")}


@requiresPythonSdk
def test_theRealCorporatePurchaseDeclaresPage(extractSchema):
    operations = extractSchema.declaredOperations(PYTHON_SDK, "CorporatePurchase")

    assert "x-sdk-page" in operations["flags"]
    assert "page" in extractSchema.declaredDocs(PYTHON_SDK, "CorporatePurchase")


@requiresPythonSdk
def test_everyOtherResourceKeepsTheFlagsOfTheExportedOnlyRule(extractSchema):
    resources = [package.name for package in (PYTHON_SDK / "starkbank").iterdir()
                 if (package / "__init__.py").is_file()]
    changed = [name for name in resources
               if extractSchema.declaredOperations(PYTHON_SDK, name)["flags"] != _exportedOnlyFlags(extractSchema, name)]

    assert resources
    assert changed == ["corporatepurchase"]


@requiresJavaSdk
def test_theRealJavaCorporatePurchaseDeclaresAllFourPageOverloads():
    source = REAL_CORPORATE_PURCHASE.read_text(encoding="utf-8")

    assert source.count("public static CorporatePurchase.Page page(") == 4


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedCorporatePurchaseHasThePageAndCompilesWithTheRealSiblings(
    builtOnce, tmp_path_factory, javaRuntimeClasspath,
):
    code, out, target = builtOnce("CorporatePurchase", "java")
    assert code == 0, out
    assert "DEAD_IMPORT" not in out
    generated = target / "src/main/java/com/starkbank/CorporatePurchase.java"
    source = generated.read_text(encoding="utf-8")
    assert source.count("public static CorporatePurchase.Page page(") == 4
    assert "public final static class Page" in source
    assert "public List<CorporatePurchase> corporatePurchases;" in source
    assert "public Page(List<CorporatePurchase> corporatePurchases, String cursor)" in source
    work = tmp_path_factory.mktemp("corporatepurchase-page")
    copied = work / "src/com/starkbank/CorporatePurchase.java"
    copied.parent.mkdir(parents=True)
    shutil.copy(generated, copied)

    compiled = subprocess.run(
        ["javac", "-Xlint:-options", "-d", str(work / "out"), "-cp", javaRuntimeClasspath,
         "-sourcepath", str(SOURCEPATH), str(copied)],
        capture_output=True, text=True,
    )

    assert compiled.returncode == 0, compiled.stderr
