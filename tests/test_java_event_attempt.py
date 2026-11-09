import re
import shutil
import subprocess

import yaml
import pytest

from conftest import PYTHON_SDK, REPO_ROOT, requiresGenerator, requiresJavac, requiresJavaSdk, requiresPythonSdk, runTool
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText, _realInnerBlock, _splitParameters
from test_java_payment_request_log import _generatedBlock, _members

SOURCEPATH = REPO_ROOT / "_references/sdk-java/src/main/java"
SIGNATURE = re.compile(r"^        public static ([\w.<>, ]+?) (\w+)\((.*?)\)\s*throws Exception\s*\{", re.M)
EXPECTED_OPERATIONS = [
    ("Attempt", "get", ["String"]),
    ("Attempt", "get", ["String", "User"]),
    ("Generator<Attempt>", "query", ["Map<String, Object>"]),
    ("Generator<Attempt>", "query", ["User"]),
    ("Generator<Attempt>", "query", []),
    ("Generator<Attempt>", "query", ["Map<String, Object>", "User"]),
    ("Attempt.Page", "page", ["Map<String, Object>"]),
    ("Attempt.Page", "page", ["User"]),
    ("Attempt.Page", "page", []),
    ("Attempt.Page", "page", ["Map<String, Object>", "User"]),
]


def _signatures(block: str) -> list:
    """Retorno, nome e tipos dos parametros de cada operacao estatica, sem os nomes dos parametros."""
    return sorted((returnType, name, [javaType for javaType, _ in _splitParameters(parameters)])
                  for returnType, name, parameters in SIGNATURE.findall(block))


def _withoutPageClass(block: str) -> str:
    return re.sub(r"^        public final static class Page \{.*?^        \}\n", "", block, flags=re.M | re.S)


def _eventConventions(without: str | None = None) -> str:
    conventions = yaml.safe_load(_realConventionsText())
    if without:
        del conventions["javaInnerClasses"]["Event"][without]
        conventions["javaInnerClasses"]["Event"] = conventions["javaInnerClasses"]["Event"] or {}
    return yaml.safe_dump(conventions)


@pytest.fixture(scope="module")
def generatedEvent(tmp_path_factory):
    from conftest import _loadTool
    return _generated(_loadTool("build-resource.py"), tmp_path_factory.mktemp("event-attempt"), "Event",
                      _realConventionsText())


# 1. o extractor deriva o recurso aninhado e a doc

@requiresPythonSdk
def test_extractorDerivesTheNestedAttemptForEventOnly(extractSchema):
    nested = {}
    for package in sorted((PYTHON_SDK / "starkbank").iterdir()):
        if not (package / "__init__.py").is_file():
            continue
        nested[package.name] = extractSchema.declaredOperations(PYTHON_SDK, package.name)["nested"]

    assert {name: value for name, value in nested.items() if value} == {"event": "Attempt"}
    assert nested["transfer"] is None
    assert nested["merchantsession"] is None


@requiresPythonSdk
def test_extractorWritesTheAttemptDocstringsUnderTheSubpackageName(extractSchema):
    docs = extractSchema.declaredDocs(PYTHON_SDK, "Event")["attempt"]

    assert list(docs) == ["get", "query", "page"]
    assert docs["get"]["returns"] == ["event.Attempt object with updated attributes"]
    assert [param["name"] for param in docs["page"]["params"]][-2:] == ["webhookIds", "user"]


@requiresPythonSdk
def test_extractorOperationsEmitTheValuedNestedFlagOnlyForEvent():
    code, output = runTool("extract-schema.py", "Event", "--operations")
    _, other = runTool("extract-schema.py", "Transfer", "--operations")

    assert code == 0
    assert "x-sdk-nested: Attempt\n" in output
    assert "x-sdk-nested" not in other


def _writeSdk(root, files: dict) -> None:
    for path, text in files.items():
        target = root / "starkbank" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def test_extractorRefusesTwoNestedSubpackagesOfOneResourceNamingThem(extractSchema, tmp_path):
    _writeSdk(tmp_path, {
        "widget/__init__.py": "from .__widget import get\nfrom .one.__one import One\nfrom .two.__two import Two\n",
        "widget/one/__init__.py": "from .__one import get\n",
        "widget/two/__init__.py": "from .__two import get\n",
    })

    with pytest.raises(ValueError, match=r"Widget.*one.*two"):
        extractSchema.declaredOperations(tmp_path, "Widget")


def test_extractorRefusesANestedSubpackageWhoseClassTheResourceInitDoesNotExport(extractSchema, tmp_path):
    _writeSdk(tmp_path, {
        "widget/__init__.py": "from .__widget import get\n",
        "widget/one/__init__.py": "from .__one import get\n",
    })

    with pytest.raises(ValueError, match=r"Widget.*one"):
        extractSchema.declaredOperations(tmp_path, "Widget")


def test_subpackagesWithoutOperationsAreNotNested(extractSchema, tmp_path):
    _writeSdk(tmp_path, {
        "widget/__init__.py": "from .__widget import get\n",
        "widget/rule/__init__.py": "",
        "widget/log/__init__.py": "from .__log import get, query, page\n",
    })

    assert extractSchema.declaredOperations(tmp_path, "Widget")["nested"] is None


# 2. os dados versionados

def test_theSpecAndTheSchemaCarryTheNestedFlagAndDocsForEventOnly():
    spec = yaml.safe_load((REPO_ROOT / "apis/spec-v2.openapi.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    event = yaml.safe_load((REPO_ROOT / "apis/schemas/event.yaml").read_text(encoding="utf-8"))
    docs = event["components"]["schemas"]["Event"]["x-sdk-docs"]

    assert {name for name, entry in spec.items() if "x-sdk-nested" in entry} == {"Event"}
    assert spec["Event"]["x-sdk-nested"] == "Attempt"
    assert list(docs["attempt"]) == ["get", "query", "page"]


def test_theAttemptEntryExtendsResourceAndFollowsTheRealConstructorOrder():
    attempt = yaml.safe_load(_realConventionsText())["javaInnerClasses"]["Event"]["Attempt"]

    assert attempt["extends"] == "Resource"
    assert list(attempt["fields"]) == ["code", "message", "eventId", "webhookId", "created"]
    assert set(attempt["fields"].values()) == {"String"}
    assert attempt["constructors"] == [["id", "code", "message", "eventId", "webhookId", "created"], []]
    assert attempt["classData"] == "EventAttempt"


@requiresJavaSdk
def test_theRealAttemptHasTheOperationsTheGeneratorEmits():
    assert _signatures(_withoutPageClass(_realInnerBlock("Event", "Attempt"))) == sorted(EXPECTED_OPERATIONS)


# 3. o Event gerado

@requiresGenerator
@requiresJavaSdk
def test_generatedAttemptMatchesTheRealDeclarationMemberByMember(generatedEvent):
    real = _realInnerBlock("Event", "Attempt")
    generated = _generatedBlock(generatedEvent, "Attempt")

    assert _members(generated) == _members(real)


@requiresGenerator
def test_generatedAttemptHasTheTenOperationsAndItsOwnPage(generatedEvent):
    block = _generatedBlock(generatedEvent, "Attempt")

    assert _signatures(block) == sorted(EXPECTED_OPERATIONS)
    assert "public final static class Page {" in block
    assert "public List<Attempt> attempts;" in block
    assert "Rest.getPage(data, params, user)" in block
    assert "for (SubResource attempt: page.entities) {" in block
    assert "return new Attempt.Page(attempts, page.cursor);" in block


@requiresGenerator
def test_generatedAttemptDocumentsEveryOperationFromThePythonDocstrings(generatedEvent):
    block = _generatedBlock(generatedEvent, "Attempt")

    assert block.count("@throws Exception error in the request") == 10
    assert "         * eventIds [list of strings, default null]: list of Event ids to filter attempts." in block
    assert "Receive a single event.Attempt object previously created by the Stark Bank API by its id" in block


@requiresGenerator
def test_theOuterEventIsUnchangedByTheNestedClass(generatedEvent):
    outer = generatedEvent.split("    public final static class Attempt")[0]

    assert generatedEvent.count("public final static class Page {") == 2
    assert generatedEvent.count("import java.util.List;") == 1


@requiresGenerator
def test_aResourceWithoutNestedClassIsGeneratedWithoutOperationsInItsInnerClasses(tmp_path):
    from conftest import _loadTool
    source = _generated(_loadTool("build-resource.py"), tmp_path, "Transfer", _realConventionsText())
    rule = _generatedBlock(source, "Rule")

    assert "Rest." not in rule
    assert "Attempt" not in source


# 4. borda

def test_nestedFlagWithoutTheInnerClassEntryFailsNamingResourceAndClass(buildResource, tmp_path):
    conventions = tmp_path / "type-conventions.yaml"
    conventions.write_text(_eventConventions(without="Attempt"), encoding="utf-8")

    with pytest.raises(ValueError, match=r"Event.*Attempt"):
        buildResource.stageJavaDocs("Event", tmp_path / "staged", conventions)


def test_nestedClassWithoutClassDataFailsNamingResourceAndClass(buildResource, tmp_path):
    conventions = yaml.safe_load(_realConventionsText())
    attempt = conventions["javaInnerClasses"]["Event"]["Attempt"]
    del attempt["classData"], attempt["classDataType"]
    (tmp_path / "type-conventions.yaml").write_text(yaml.safe_dump(conventions), encoding="utf-8")

    with pytest.raises(ValueError, match=r"Event\.Attempt.*classData"):
        buildResource.stageJavaDocs("Event", tmp_path / "staged", tmp_path / "type-conventions.yaml")


# 5. compila com os irmaos reais

_MAIN = """import com.starkbank.*;
import com.starkbank.utils.Generator;
import java.util.HashMap;

public class Main {
    public static void main(String[] args) throws Exception {
        Event.Attempt attempt = new Event.Attempt("1", "timeout", "late", "2", "3", "now");
        System.out.println("attempt -> " + attempt.id + " " + attempt.code + " " + attempt.eventId);
        Event.Attempt.Page page = new Event.Attempt.Page(new java.util.ArrayList<>(), "next");
        System.out.println("page -> " + page.attempts.size() + " " + page.cursor);
        if (args.length > 0) {
            Event.Attempt.Page remote = Event.Attempt.page(new HashMap<>());
            Generator<Event.Attempt> stream = Event.Attempt.query();
            Event.Attempt one = Event.Attempt.get("1");
        }
    }
}
"""


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedEventCompilesWithTheRealSiblingsAndTheAttemptApiIsCallable(
    builtOnce, tmp_path_factory, javaRuntimeClasspath
):
    code, out, target = builtOnce("Event", "java", "--advisory")
    assert code == 0, out
    work = tmp_path_factory.mktemp("event-attempt-runtime")
    generated = work / "src/com/starkbank/Event.java"
    generated.parent.mkdir(parents=True)
    shutil.copy(target / "src/main/java/com/starkbank/Event.java", generated)
    (work / "src/Main.java").write_text(_MAIN, encoding="utf-8")

    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath", str(SOURCEPATH),
         str(generated), str(work / "src/Main.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run(["java", "-cp", f"{work / 'out'}:{javaRuntimeClasspath}", "Main"], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == ["attempt -> 1 timeout 2", "page -> 0 next"]


@requiresGenerator
@requiresJavac
def test_generatedEventPassesTheStrictGateWithoutDeadOrMissingImports(builtOnce):
    code, out, _ = builtOnce("Event", "java")

    assert code == 0, out
    assert "DEAD_IMPORT" not in out
