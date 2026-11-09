import re
import shutil
import pytest
import subprocess

from conftest import PYTHON_SDK, REPO_ROOT, requiresGenerator, requiresJavac, requiresJavaSdk, requiresPythonSdk
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText, _splitParameters

PARSE_RESOURCES = ("Event", "CorporatePurchase")
SIGNATURE = re.compile(r"^    public static (\w+) parse\((.*?)\)\s*throws Exception\s*\{", re.M)
EVENT_PLAIN_BODY = (
    'content = Parse.verify(content, signature, Settings.user); Gson gson = GsonEvent.getInstance(); '
    'return gson.fromJson( new Gson().fromJson(content, JsonObject.class).get("event").getAsJsonObject(), '
    'Event.class );'
)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _parseSignatures(source: str) -> list:
    """Tipos dos parametros de cada sobrecarga de `parse`, sem os nomes, na ordem de menos para mais argumentos."""
    found = [[javaType for javaType, _ in _splitParameters(parameters)]
             for _, parameters in SIGNATURE.findall(source)]
    return sorted(found, key=len)


def _parseBodies(source: str) -> dict:
    """Corpo normalizado de cada sobrecarga de `parse`, por quantidade de parametros."""
    bodies = {}
    for match in re.finditer(r"^    public static \w+ parse\((.*?)\)\s*throws Exception\s*\{\n(.*?)^    \}\n",
                             source, re.M | re.S):
        bodies[len(_splitParameters(match.group(1)))] = _normalized(match.group(2))
    return bodies


def _realSource(resource: str) -> str:
    return (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    from conftest import _loadTool
    buildResource = _loadTool("build-resource.py")
    return {resource: _generated(buildResource, tmp_path_factory.mktemp(resource.lower()), resource,
                                 _realConventionsText())
            for resource in (*PARSE_RESOURCES, "Transfer")}


# 1. o extractor deriva a flag e a doc

@requiresPythonSdk
def test_extractorDerivesTheParseFlagAndDocsForEventAndCorporatePurchaseOnly(extractSchema):
    flagged = []
    for package in sorted((PYTHON_SDK / "starkbank").iterdir()):
        if not (package / "__init__.py").is_file():
            continue
        operations = extractSchema.declaredOperations(PYTHON_SDK, package.name)
        if "x-sdk-parse" in operations["flags"]:
            flagged.append(package.name)

    assert flagged == ["corporatepurchase", "event"]


@requiresPythonSdk
@pytest.mark.parametrize("resource", PARSE_RESOURCES)
def test_extractorWritesTheParseDocstringIntoTheDocs(extractSchema, resource):
    docs = extractSchema.declaredDocs(PYTHON_SDK, resource)["parse"]

    assert [(param["name"], param["section"]) for param in docs["params"]] == [
        ("content", "required"), ("signature", "required"), ("user", "optional"),
    ]
    assert docs["returns"] == [f"Parsed {resource} object"]


def test_parseDocsComeAfterTheOtherOperationsSoNoOtherSchemaChangesOrder(extractSchema):
    assert list(extractSchema.OPERATION_FLAGS)[-1] == "parse"
    assert extractSchema.OPERATION_FLAGS["parse"] == "x-sdk-parse"


def test_theAppliedSchemasAndTheSpecCarryTheFlagOnlyForTheTwoResources(buildResource):
    spec = buildResource.loadFast((REPO_ROOT / buildResource.SPEC_FILE).read_text(encoding="utf-8"))
    flagged = sorted(name for name, entry in spec["components"]["schemas"].items() if "x-sdk-parse" in entry)

    assert flagged == sorted(PARSE_RESOURCES)
    assert not [path for path in spec["paths"] if "parse" in path]


# 2. o codigo gerado

@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", PARSE_RESOURCES)
def test_generatedParseHasTheTwoSignaturesOfTheRealOverloads(generated, resource):
    assert _parseSignatures(generated[resource]) == _parseSignatures(_realSource(resource)) == [
        ["String", "String"], ["String", "String", "User"],
    ]


@requiresGenerator
@requiresJavaSdk
def test_generatedPlainParseComesBeforeTheUserOne(generated):
    for resource in PARSE_RESOURCES:
        order = [len(_splitParameters(parameters)) for _, parameters in SIGNATURE.findall(generated[resource])]

        assert order == [2, 3]


@requiresGenerator
def test_generatedEventVerifiesAndReadsTheEventKeyInThePlainOverloadAndForwardsTheUserInTheOther(generated):
    bodies = _parseBodies(generated["Event"])

    assert bodies[2] == EVENT_PLAIN_BODY
    assert bodies[3] == "return Parse.parseAndVerify(data, content, signature, user);"


@requiresGenerator
def test_generatedCorporatePurchasePassesNullUserInThePlainOverloadAndTheGivenOneInTheOther(generated):
    bodies = _parseBodies(generated["CorporatePurchase"])

    assert bodies[2] == "return Parse.parseAndVerify(data, content, signature, null);"
    assert bodies[3] == "return Parse.parseAndVerify(data, content, signature, user);"


@requiresGenerator
@pytest.mark.parametrize("resource", PARSE_RESOURCES)
def test_generatedParseDocumentsTheParametersTheReturnAndTheThrows(generated, resource):
    javadocs = re.findall(r"(/\*\*(?:(?!\*/).)*?\*/)\s*public static \w+ parse\(", generated[resource], re.S)

    assert len(javadocs) == 2
    for javadoc in javadocs:
        assert "@param content [string]: response content" in javadoc
        assert "@param signature [string]: base-64 digital signature" in javadoc
        assert f"@return Parsed {resource} object" in javadoc
        assert "@throws Exception error in the request" in javadoc
    assert "@param user" not in javadocs[0]
    assert "@param user [Organization/Project object" in javadocs[1]


@requiresGenerator
def test_aResourceWithoutTheFlagEmitsNoParseAndImportsNoParse(generated):
    assert " parse(" not in generated["Transfer"]
    assert "import com.starkbank.utils.Parse;" not in generated["Transfer"]


@requiresGenerator
@pytest.mark.parametrize("resource", PARSE_RESOURCES)
def test_aResourceWithTheFlagImportsParseOnce(generated, resource):
    assert generated[resource].count("import com.starkbank.utils.Parse;\n") == 1


# 3. compila com os irmaos reais

@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedEventAndCorporatePurchaseCompileWithTheRealSiblings(
    builtOnce, tmp_path_factory, javaRuntimeClasspath
):
    eventCode, eventOut, eventTarget = builtOnce("Event", "java", "--advisory")
    assert eventCode == 0, eventOut
    purchaseCode, purchaseOut, purchaseTarget = builtOnce("CorporatePurchase", "java")
    assert purchaseCode == 0, purchaseOut
    work = tmp_path_factory.mktemp("parse-compile")
    sources = []
    for resource, target in (("Event", eventTarget), ("CorporatePurchase", purchaseTarget)):
        destination = work / f"src/com/starkbank/{resource}.java"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(target / f"src/main/java/com/starkbank/{resource}.java", destination)
        sources.append(str(destination))

    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath", str(REAL_SOURCES.parent.parent),
         *sources],
        capture_output=True, text=True,
    )

    assert compiled.returncode == 0, compiled.stderr
