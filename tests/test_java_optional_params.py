import re
import shutil
import subprocess

import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavaSdk, requiresJavac
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText

SPEC_FILE = REPO_ROOT / "apis/spec-v2.openapi.yaml"
SOURCEPATH = REPO_ROOT / "_references/sdk-java/src/main/java"
EXPECTED_OPERATIONS = {
    ("Boleto", "pdf"): ["layout", "hiddenFields"],
    ("CorporateCard", "get"): ["expand"],
    ("CorporateHolder", "get"): ["expand"],
}
ID_OPERATIONS = re.compile(
    r"^    /\*\*\n((?:     \*[^\n]*\n)*)     \*/\n"
    r"    public static ([\w<>]+) (get|pdf)\(([^)]*)\) throws Exception ?\{\n"
    r"((?:(?!    \}\n)[^\n]*\n)*)    \}\n",
    re.M,
)
REAL_ID_OPERATION = re.compile(
    r"^    public static ([\w<>]+) (get|pdf)\(([^)]*)\) throws Exception ?\{\n"
    r"((?:(?!    \}\n)[^\n]*\n)*)    \}\n",
    re.M,
)
GET_ORDER = ["String, Map<String, Object>, User", "String, Map<String, Object>", "String, User", "String"]
PDF_ORDER = ["String", "String, Map<String, Object>", "String, User", "String, Map<String, Object>, User"]


def _types(parameters: str) -> str:
    """Assinatura sem nomes de parametro: `String id, Map<String, Object> params` vira `String, Map<String, Object>`."""
    declarations = re.split(r",\s*(?![^<]*>)", " ".join(parameters.split()))
    return ", ".join(declaration.rsplit(" ", 1)[0] for declaration in declarations if declaration)


def _generatedOperations(source: str, operation: str) -> list:
    return [(_types(parameters), javadoc.splitlines(), " ".join(body.split()))
            for javadoc, _, kind, parameters, body in ID_OPERATIONS.findall(source) if kind == operation]


def _realOperations(resource: str, operation: str) -> list:
    source = (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")
    return [(_types(parameters), " ".join(body.split()))
            for _, kind, parameters, body in REAL_ID_OPERATION.findall(source) if kind == operation]


def _specDocs(buildResource) -> dict:
    spec = buildResource.loadFast(SPEC_FILE.read_text(encoding="utf-8"))
    return {
        name: (buildResource.resolveRef(spec, name, REPO_ROOT / "apis") or {}).get("x-sdk-docs") or {}
        for name in spec["components"]["schemas"]
    }


def test_theResourcesWithAnOptionalParameterBesidesTheUserAreExactlyTheKnownOnes(buildResource):
    found = {}
    for name, docs in _specDocs(buildResource).items():
        for operation in ("get", "pdf"):
            optional = [param["name"] for param in (docs.get(operation) or {}).get("params") or []
                        if param.get("section") != "required" and param["name"] != "user"]
            if optional:
                found[(name, operation)] = optional

    assert found == EXPECTED_OPERATIONS


@pytest.mark.parametrize("operation, params, expected", [
    ("get", [{"name": "id", "section": "required"}, {"name": "expand", "section": "optional"}], True),
    ("pdf", [{"name": "id", "section": "required"}, {"name": "layout", "section": "optional"}], True),
    ("get", [{"name": "id", "section": "required"}, {"name": "user", "section": "optional"}], False),
    ("get", [{"name": "uuid", "section": "required"}, {"name": "user", "section": "optional"}], False),
    ("delete", [{"name": "id", "section": "required"}, {"name": "expand", "section": "optional"}], False),
    ("get", [], False),
])
def test_onlyAGetOrPdfWithAnOptionalParameterBesidesTheUserGetsTheMapOverloads(javaDocs, operation, params, expected):
    assert javaDocs.hasOptionalParamOverloads(operation, {"params": params}) is expected


@requiresGenerator
@pytest.mark.parametrize("resource", ["CorporateCard", "CorporateHolder"])
def test_theGetDeclaresTheFourOverloadsInTheOrderAndWithTheBodiesOfTheRealSdk(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())
    generated = _generatedOperations(source, "get")
    real = _realOperations(resource, "get")

    assert [signature for signature, _, _ in generated] == GET_ORDER
    assert [signature for signature, _ in real] == GET_ORDER
    assert [(signature, body) for signature, _, body in generated] == real
    assert generated[0][2] == "return Rest.getId(data, id, params, user);"


@requiresGenerator
@pytest.mark.parametrize("resource", ["CorporateCard", "CorporateHolder"])
def test_theGetJavadocListsExpandOnlyOnTheMapOverloads(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())
    javadocs = {signature: "\n".join(javadoc) for signature, javadoc, _ in _generatedOperations(source, "get")}

    for signature in GET_ORDER[:2]:
        assert "     * @param params map of parameters\n     * expand [list of strings, default null]" in javadocs[signature]
    for signature in GET_ORDER[2:]:
        assert "expand" not in javadocs[signature]
        assert "@param params" not in javadocs[signature]
    assert "@param user" in javadocs[GET_ORDER[0]] and "@param user" in javadocs[GET_ORDER[2]]
    assert "@param user" not in javadocs[GET_ORDER[1]] and "@param user" not in javadocs[GET_ORDER[3]]
    assert all("@param id [string]: object unique id" in javadoc for javadoc in javadocs.values())


@requiresGenerator
def test_theBoletoPdfDeclaresTheFourOverloadsInTheOrderAndWithTheBodiesOfTheRealSdk(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Boleto", _realConventionsText())
    generated = _generatedOperations(source, "pdf")
    real = _realOperations("Boleto", "pdf")

    assert [signature for signature, _, _ in generated] == PDF_ORDER
    assert [signature for signature, _ in real] == PDF_ORDER
    assert [(signature, body) for signature, _, body in generated] == real
    assert generated[3][2] == 'return Rest.getContent(data, id, "pdf", user, options);'


@requiresGenerator
def test_theBoletoPdfJavadocListsLayoutAndHiddenFieldsOnlyOnTheOptionsOverloads(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Boleto", _realConventionsText())
    javadocs = {signature: "\n".join(javadoc) for signature, javadoc, _ in _generatedOperations(source, "pdf")}

    for signature in (PDF_ORDER[1], PDF_ORDER[3]):
        assert "     * @param options [Map of String: Object]: PDF generation options\n" in javadocs[signature]
        assert "     * layout [string]: Layout specification." in javadocs[signature]
        assert "     * hiddenFields [list of strings, default null]: " in javadocs[signature]
    for signature in (PDF_ORDER[0], PDF_ORDER[2]):
        assert "layout" not in javadocs[signature] and "hiddenFields" not in javadocs[signature]
        assert "@param options" not in javadocs[signature]


@requiresGenerator
@pytest.mark.parametrize("resource, operation, plain", [
    ("Transfer", "get", ["String", "String, User"]),
    ("Invoice", "pdf", ["String", "String, User"]),
    ("UtilityPayment", "pdf", ["String", "String, User"]),
    ("DynamicBrcode", "get", ["String", "String, User"]),
])
def test_aResourceWithoutOptionalParametersKeepsTheTwoOverloadsAndNoMap(
    buildResource, tmpPath, resource, operation, plain,
):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())
    generated = _generatedOperations(source, operation)

    assert [signature for signature, _, _ in generated] == plain
    assert not any("Map<String, Object>" in signature for signature, _, _ in generated)
    assert not any("@param params" in line or "@param options" in line
                   for _, javadoc, _ in generated for line in javadoc)


@requiresGenerator
@requiresJavac
@requiresJavaSdk
@pytest.mark.parametrize("resource", ["CorporateCard", "CorporateHolder", "Boleto"])
def test_theGeneratedResourceCompilesWithTheRealSiblingsAndHasNoDeadImport(
    builtOnce, tmp_path_factory, javaRuntimeClasspath, resource,
):
    code, out, target = builtOnce(resource, "java", "--advisory")
    assert code == 0, out
    assert "DEAD_IMPORT" not in out
    work = tmp_path_factory.mktemp(f"{resource}-optional-params")
    generated = work / f"src/com/starkbank/{resource}.java"
    generated.parent.mkdir(parents=True)
    shutil.copy(target / f"src/main/java/com/starkbank/{resource}.java", generated)

    compiled = subprocess.run(
        ["javac", "-Xlint:-options", "-d", str(work / "out"), "-cp", javaRuntimeClasspath,
         "-sourcepath", str(SOURCEPATH), str(generated)],
        capture_output=True, text=True,
    )

    assert compiled.returncode == 0, compiled.stderr
