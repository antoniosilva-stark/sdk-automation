import re
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavaSdk, requiresJavac
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText

SPEC_FILE = REPO_ROOT / "apis/spec-v2.openapi.yaml"
SINGLETONS = ("Balance", "CorporateBalance")
GET_METHOD = re.compile(
    r"^    /\*\*\n((?:     \*[^\n]*\n)*)     \*/\n"
    r"    public static (\w+) get\(([^)]*)\) throws Exception ?\{\n"
    r"((?:(?!    \}\n)[^\n]*\n)*)    \}\n",
    re.M,
)


def _specResources(buildResource) -> dict:
    """Recurso -> (flags `x-sdk-*` da spec, `x-sdk-docs` do schema resolvido), o mesmo par que o stage do build le."""
    spec = buildResource.loadFast(SPEC_FILE.read_text(encoding="utf-8"))
    entries = spec["components"]["schemas"]
    return {
        name: ({key for key in entry if key.startswith("x-sdk-")},
               (buildResource.resolveRef(spec, name, REPO_ROOT / "apis") or {}).get("x-sdk-docs") or {})
        for name, entry in entries.items()
    }


def _selected(buildResource, javaDocs) -> set:
    resources = _specResources(buildResource)
    return {name for name, (flags, docs) in resources.items() if javaDocs.hasSingletonGet(flags, docs)}


def _realGetParameters(resource: str) -> list:
    source = (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")
    found = re.findall(rf"^    public static {resource} get\((.*?)\)", source, re.M | re.S)
    return [" ".join(parameters.split()) for parameters in found]


def _realHasNoIdGet(resource: str) -> bool:
    """Todo `get` do real sem parametro de id: o CorporateTransaction tem um `get()` a mais (descuido do real), mas
    tambem o `get(String id)`, entao nao e singleton."""
    parameters = _realGetParameters(resource)
    return bool(parameters) and not any("String" in each for each in parameters)


def _generatedGets(source: str) -> list:
    return [(" ".join(parameters.split()), javadoc.splitlines(), body.rstrip("\n"))
            for javadoc, _, parameters, body in GET_METHOD.findall(source)]


def _signatures(source: str) -> list:
    return [parameters for parameters, _, _ in _generatedGets(source)]


def test_theSelectionRuleChoosesExactlyBalanceAndCorporateBalanceFromTheSpec(buildResource, javaDocs):
    assert _selected(buildResource, javaDocs) == set(SINGLETONS)


@requiresJavaSdk
def test_theSelectionRuleAgreesWithTheRealSdkInBothDirections(buildResource, javaDocs):
    resources = _specResources(buildResource)
    realNoId = {name for name in resources if (REAL_SOURCES / f"{name}.java").is_file() and _realHasNoIdGet(name)}
    selected = _selected(buildResource, javaDocs)

    assert realNoId - selected == set()
    assert selected - realNoId == set()
    assert realNoId == set(SINGLETONS)


@pytest.mark.parametrize("flags, docs", [
    ({"x-sdk-get"}, {"get": {"params": [{"name": "id"}, {"name": "user"}]}}),
    ({"x-sdk-get"}, {"get": {"params": [{"name": "uuid"}, {"name": "user"}]}}),
    ({"x-sdk-get"}, {"get": {"params": [{"name": "id"}, {"name": "expand"}, {"name": "user"}]}}),
    ({"x-sdk-get"}, {"get": {"params": [{"name": "user"}, {"name": "id"}]}}),
    ({"x-sdk-get"}, {}),
    ({"x-sdk-get"}, {"get": {}}),
    ({"x-sdk-query"}, {"get": {"params": [{"name": "user"}]}}),
    (set(), {"get": {"params": [{"name": "user"}]}}),
])
def test_aGetWithAnIdentifierOrWithoutTheFlagStaysOnTheIdForm(javaDocs, flags, docs):
    assert javaDocs.hasSingletonGet(flags, docs) is False


@pytest.mark.parametrize("params", [[{"name": "user"}], []])
def test_aFlaggedGetThatTakesOnlyTheUserIsTheSingletonForm(javaDocs, params):
    assert javaDocs.hasSingletonGet({"x-sdk-get"}, {"get": {"params": params}}) is True


def test_theGetStreamImportsComeOnlyFromTheSingletonAndNeverTwice(buildResource):
    singleton = buildResource.javaImports({"x-sdk-get", "x-sdk-query", "x-sdk-page"}, hasSingletonGet=True)
    byId = buildResource.javaImports({"x-sdk-get"})

    assert singleton.count("usesGenerator=true") == 1
    assert singleton.count("usesList=true") == 1
    assert "usesGenerator=true" not in byId
    assert "usesList=true" not in byId


@requiresGenerator
@pytest.mark.parametrize("resource", SINGLETONS)
def test_theSingletonDeclaresGetUserAndGetAndNoGetById(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())

    assert sorted(_signatures(source)) == sorted(["User user", ""])
    assert sorted(_signatures(source)) == sorted(_realGetParameters(resource))
    assert "get(String" not in source
    assert "Rest.getId" not in source


@requiresGenerator
@pytest.mark.parametrize("resource", SINGLETONS)
def test_theSingletonGetBodyReadsTheStreamAndReturnsTheFirstElement(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())
    bodies = {parameters: body for parameters, _, body in _generatedGets(source)}
    variable = f"{resource[:1].lower()}{resource[1:]}"

    assert bodies[""] == f"        return {resource}.get(null);"
    assert bodies["User user"].splitlines() == [
        f"        List<{resource}> {variable}List = new ArrayList<>();",
        f"        Generator<{resource}> {variable}s = Rest.getStream(data, new HashMap<String, Object>(), user);",
        f"        for ({resource} {variable} : {variable}s) {{",
        f"            {variable}List.add({variable});",
        "        }",
        f"        return {variable}List.get(0);",
    ]


@requiresGenerator
@pytest.mark.parametrize("resource", SINGLETONS)
def test_theSingletonImportsGeneratorListAndArrayListOnceEach(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())

    for line in ("import com.starkbank.utils.Generator;", "import java.util.List;", "import java.util.ArrayList;",
                 "import java.util.HashMap;", "import com.starkbank.utils.Rest;"):
        assert source.count(f"{line}\n") == 1, line


@requiresGenerator
@pytest.mark.parametrize("resource", SINGLETONS)
def test_theSingletonGetJavadocComesFromTheSpecDocsWithTheUserParamOnlyOnTheUserOverload(
    buildResource, tmpPath, resource,
):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())
    javadocs = {parameters: javadoc for parameters, javadoc, _ in _generatedGets(source)}
    title = f"     * Retrieve the {resource} object"
    summary = f"     * Receive the {resource} object linked to your "

    assert javadocs["User user"][0] == title
    assert javadocs[""][0] == title
    assert summary in "\n".join(javadocs[""]) and summary in "\n".join(javadocs["User user"])
    assert not any("@param id" in line for line in javadocs["User user"] + javadocs[""])
    assert [line for line in javadocs["User user"] if "@param" in line] == [
        "     * @param user [Organization/Project object, default null]: Organization or Project object."
        " Not necessary if starkbank.Settings.user was set before function call"
    ]
    assert not any("@param" in line or "Parameters:" in line for line in javadocs[""])
    assert f"     * @return {resource} object with updated attributes" in javadocs[""]
    assert f"     * @return {resource} object with updated attributes" in javadocs["User user"]


@requiresGenerator
@pytest.mark.parametrize("resource", ["Deposit", "CorporateTransaction"])
def test_anIdGetResourceKeepsTheIdFormByteForByte(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())
    gets = _generatedGets(source)
    title = f"     * Retrieve a specific {resource}"

    assert [parameters for parameters, _, _ in gets] == ["String id", "String id, User user"]
    assert gets[0][1][0] == title and gets[1][1][0] == title
    assert gets[0][2] == f"        return {resource}.get(id, null);"
    assert gets[1][2] == "        return Rest.getId(data, id, user);"
    assert [line for line in gets[0][1] if line.startswith("     * @param id [string]: object unique id")]
    assert [line for line in gets[1][1] if line.startswith("     * @param id [string]: object unique id")]
    assert "getStream" not in "".join(body for _, _, body in gets)


@requiresGenerator
@requiresJavac
@pytest.mark.parametrize("resource", SINGLETONS)
def test_theGeneratedSingletonPassesTheJavacGate(builtOnce, resource):
    code, out, target = builtOnce(resource, "java")

    assert code == 0, out
    assert "sintaxe (javac)" in out
    assert "public static " + resource + " get(User user) throws Exception {" in (
        target / f"src/main/java/com/starkbank/{resource}.java").read_text(encoding="utf-8")
