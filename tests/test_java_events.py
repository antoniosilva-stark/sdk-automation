import re
import yaml
import shutil
import pytest
import subprocess

from conftest import REPO_ROOT, requiresGenerator, requiresJavac, requiresJavaSdk
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText, _schemaOf, _splitParameters

REAL_EVENT = REAL_SOURCES / "Event.java"
UNKNOWN_CLASS = "UnknownEvent"
BASE_PARAMETERS = [("String", "created"), ("Boolean", "isDelivered"), ("String", "subscription"), ("String", "id"),
                   ("String", "workspaceId")]


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _realSource() -> str:
    return REAL_EVENT.read_text(encoding="utf-8")


def _deserializerBlock(source: str) -> str:
    return re.search(r"^    public static class Deserializer .*?^    \}\n", source, re.M | re.S).group(0)


def _realSwitch(source: str | None = None) -> list:
    """(subscription, classe, usa GsonEvent) de cada `case` do real, na ordem; o `default` fica de fora."""
    block = _deserializerBlock(source or _realSource())
    pattern = (r'case "([\w-]+)":\s*return (?:context\.deserialize\(jsonObject,\s*(\w+)\.class\)'
               r'|GsonEvent\.getInstance\(\)\.fromJson\(jsonObject, (\w+)\.class\));')
    return [(subscription, plain or gson, bool(gson)) for subscription, plain, gson in re.findall(pattern, block)]


def _subclassBlocks(source: str) -> dict:
    found = re.finditer(r"^    public final static class (\w+) extends Event \{.*?^    \}\n", source, re.M | re.S)
    return {match.group(1): match.group(0) for match in found}


def _logTypeOf(block: str) -> str:
    return re.search(r"^        public ([\w.]+) log;", block, re.M).group(1)


def _entry() -> dict:
    return yaml.safe_load(_realConventionsText())["javaEvents"]["Event"]


def _ctorFieldsOfEvent(javaCode, javaDocs, extractSchema) -> list:
    schema, createSchema = _schemaOf("Event")
    omitted = extractSchema.javaOmittedFields("Event")
    schema = javaCode.omitFields(schema, "Event", omitted)
    return javaCode.renderJavaCode(
        schema,
        javaDocs.describeEntity(schema, createSchema),
        "Event",
        {},
        ctorOrder=extractSchema.javaConstructorOrder("Event"),
    )["ctorFields"]


@pytest.fixture(scope="module")
def generatedEvent(tmp_path_factory):
    from conftest import _loadTool
    return _generated(_loadTool("build-resource.py"), tmp_path_factory.mktemp("event"), "Event", _realConventionsText())


# 1. os dados, nos dois sentidos contra o real

def _realLogResources(buildResource) -> frozenset:
    spec = buildResource.loadFast((REPO_ROOT / buildResource.SPEC_FILE).read_text(encoding="utf-8"))
    return buildResource.logResourceNames(spec, REPO_ROOT / "apis/type-conventions.yaml")


@requiresJavaSdk
def test_theSubscriptionsOfTheDataAreTheCasesOfTheRealSwitchInOrder():
    assert _entry()["subscriptions"] == [subscription for subscription, _, _ in _realSwitch()]


@requiresJavaSdk
def test_theGsonSubsetOfTheDataIsTheCasesTheRealDeserializesWithGsonEvent():
    assert _entry()["gson"] == [subscription for subscription, _, usesGson in _realSwitch() if usesGson]
    assert _entry()["gson"] == ["payment-request"]


@requiresJavaSdk
def test_theDerivedClassesAndLogTypesAreTheRealSubclassesAndTheUnknownOne(
    javaCode, javaDocs, extractSchema, buildResource
):
    events = javaCode.renderEvents("Event", extractSchema.javaEventShape("Event"),
                                   _ctorFieldsOfEvent(javaCode, javaDocs, extractSchema),
                                   extractSchema.schemaResourceNames(), _realLogResources(buildResource))
    real = {name: _logTypeOf(block) for name, block in _subclassBlocks(_realSource()).items()}

    assert {item["className"]: item["logType"] for item in events["subclasses"]} == real
    assert real[UNKNOWN_CLASS] == "JsonObject"
    assert [(item["subscription"], item["className"], item["isGson"]) for item in events["cases"]] == _realSwitch()
    assert events["subclasses"][-1]["className"] == UNKNOWN_CLASS


@requiresJavaSdk
def test_everyRealSubclassIsInTheSwitchOrIsTheUnknownOne():
    classes = set(_subclassBlocks(_realSource())) - {UNKNOWN_CLASS}

    assert classes == {className for _, className, _ in _realSwitch()}


@requiresJavaSdk
def test_theRealDeserializerFallsBackToTheUnknownEventAndReturnsNullWithoutSubscription():
    block = _normalized(_deserializerBlock(_realSource()))

    assert f"default: return context.deserialize(jsonObject, {UNKNOWN_CLASS}.class);" in block
    assert block.endswith("} return null; } }")


@requiresJavaSdk
def test_theOmittedFieldIsNotInTheRealBaseButEverySubclassDeclaresIt():
    omitted = yaml.safe_load(_realConventionsText())["javaOmittedFields"]["Event"]
    base = _realSource().split("    public static class Deserializer")[0]
    declared = {name for _, name in re.findall(r"^    public ([\w<>\[\], .]+?) (\w+);", base, re.M)}
    schemaFields = set(_schemaOf("Event")[0]["properties"])

    assert omitted == ["log"]
    assert not declared & set(omitted)
    assert all(re.search(rf"^        public [\w.]+ {field};", block, re.M)
               for block in _subclassBlocks(_realSource()).values() for field in omitted)
    assert schemaFields - set(omitted) - {"id"} == declared


# 2. o Event gerado

@requiresGenerator
@requiresJavaSdk
def test_generatedBaseEventHasTheRealFieldsAndNoLog(generatedEvent):
    outer = generatedEvent.split("    public static class Deserializer")[0]
    fields = dict((name, javaType) for javaType, name in re.findall(r"^    public ([\w<>\[\], .]+?) (\w+);", outer, re.M))

    assert fields == {"created": "String", "isDelivered": "Boolean", "subscription": "String", "workspaceId": "String"}
    assert not re.search(r"\blog\b", outer)


@requiresGenerator
@requiresJavaSdk
def test_generatedBaseEventHasTheRealAllArgsConstructorAndTheNoArgOne(generatedEvent):
    constructors = [_splitParameters(match.group(1))
                    for match in re.finditer(r"^    public Event\((.*?)\) (?:throws Exception )?\{", generatedEvent, re.M)]

    assert BASE_PARAMETERS in constructors
    assert "    public Event() {\n        super(null);\n    }\n" in generatedEvent


@requiresGenerator
@requiresJavaSdk
def test_generatedSubclassesAreTheRealOnesWithTheirFieldAndConstructors(generatedEvent):
    generated = _subclassBlocks(generatedEvent)
    real = _subclassBlocks(_realSource())

    assert set(generated) == set(real)
    assert len(real) == 19
    assert {name: _normalized(block) for name, block in generated.items()} == \
        {name: _normalized(block) for name, block in real.items()}


@requiresGenerator
@requiresJavaSdk
def test_generatedDeserializerHasEveryCaseTheDefaultAndTheNullReturnOfTheReal(generatedEvent):
    assert _normalized(_deserializerBlock(generatedEvent)) == _normalized(_deserializerBlock(_realSource()))
    assert _realSwitch(generatedEvent) == _realSwitch()


@requiresGenerator
def test_generatedEventImportsWhatTheDeserializerAndParseUseAndNoSignatureClass(generatedEvent):
    assert "import com.google.gson.*;\n" in generatedEvent
    assert "import com.starkcore.utils.GsonEvent;\n" in generatedEvent
    assert "import java.lang.reflect.Type;\n" in generatedEvent
    assert "import com.starkbank.utils.Parse;\n" in generatedEvent
    assert "import com.starkbank.ellipticcurve" not in generatedEvent
    assert "import com.starkbank.error.InvalidSignatureError" not in generatedEvent


@requiresGenerator
def test_withoutAGsonSubsetNoCaseUsesGsonEventAndOnlyParseKeepsTheImport(buildResource, tmpPath):
    conventions = yaml.safe_load(_realConventionsText())
    del conventions["javaEvents"]["Event"]["gson"]
    source = _generated(buildResource, tmpPath, "Event", yaml.safe_dump(conventions, sort_keys=False))

    assert "import com.google.gson.*;\n" in source
    assert "import com.starkcore.utils.GsonEvent;\n" in source
    assert "GsonEvent.getInstance().fromJson(jsonObject" not in source
    assert source.count("GsonEvent") == 2
    assert source.count("context.deserialize(jsonObject,") == 19


@requiresGenerator
def test_generatedBaseEventKeepsNoLogInItsJavadoc(generatedEvent):
    outer = generatedEvent.split("    public static class Deserializer")[0]

    assert "log [string]" not in outer
    assert "@param log" not in outer


@requiresGenerator
@requiresJavac
def test_theGeneratedEventPassesTheStrictGateIncludingTheStyleStepAndTheDeadImportCheck(builtOnce):
    code, out, _ = builtOnce("Event", "java")

    assert code == 0, out
    assert "sintaxe (javac)" in out
    assert "estilo" in out
    assert "DEAD_IMPORT" not in out


def _mainSource(subscriptions: list) -> str:
    items = ", ".join(f'"{subscription}"' for subscription in subscriptions)
    return """import com.google.gson.*;
import com.starkbank.*;
import com.starkbank.utils.GsonAdapters;
import com.starkcore.utils.GsonEvent;

public class Main {
    static String kind(Gson gson, String json) {
        Event event = gson.fromJson(json, Event.class);
        return event == null ? "null" : event.getClass().getSimpleName() + " " + event.subscription;
    }

    public static void main(String[] args) {
        GsonAdapters.setup();
        Gson gson = GsonEvent.getInstance();
        for (String subscription : new String[] {%s, "something-new"}) {
            System.out.println("case " + subscription + " -> " + kind(gson, "{\\"subscription\\":\\"" + subscription + "\\",\\"id\\":\\"1\\"}"));
        }
        System.out.println("case none -> " + kind(gson, "{\\"id\\":\\"1\\"}"));
    }
}
""" % items


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_theGeneratedEventCompilesWithTheRealSiblingsAndDeserializesEachSubscriptionToItsClass(
    builtOnce, tmp_path_factory, javaRuntimeClasspath
):
    code, out, target = builtOnce("Event", "java", "--advisory")
    assert code == 0, out
    subscriptions = [subscription for subscription, _, _ in _realSwitch()]
    work = tmp_path_factory.mktemp("event-runtime")
    generated = work / "src/com/starkbank/Event.java"
    generated.parent.mkdir(parents=True)
    shutil.copy(target / "src/main/java/com/starkbank/Event.java", generated)
    (work / "src/Main.java").write_text(_mainSource(subscriptions), encoding="utf-8")

    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath",
         str(REPO_ROOT / "_references/sdk-java/src/main/java"), str(generated), str(work / "src/Main.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run(["java", "-cp", f"{work / 'out'}:{javaRuntimeClasspath}", "Main"], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    lines = ran.stdout.splitlines()

    for subscription, className, _ in _realSwitch():
        assert f"case {subscription} -> {className} {subscription}" in lines
    assert f"case something-new -> {UNKNOWN_CLASS} something-new" in lines
    assert "case none -> null" in lines


# 3. nenhuma outra saida muda

def _withoutEventSections(text: str) -> str:
    conventions = yaml.safe_load(text)
    conventions.pop("javaEvents", None)
    conventions.pop("javaOmittedFields", None)
    conventions["javaConstructorOrder"].pop("Event", None)
    return yaml.safe_dump(conventions, sort_keys=False)


@requiresGenerator
def test_theEventSectionsLeaveAnotherResourceByteIdentical(buildResource, tmpPath):
    real = _generated(buildResource, tmpPath / "real", "Transfer", _realConventionsText())
    without = _generated(buildResource, tmpPath / "without", "Transfer", _withoutEventSections(_realConventionsText()))

    assert real == without


@requiresGenerator
def test_aResourceWithoutTheSectionsGeneratesNoDeserializerNorSubclassNorGsonImport(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", _realConventionsText())

    assert "Deserializer" not in source
    assert "com.google.gson" not in source
    assert "GsonEvent" not in source
    assert "java.lang.reflect.Type" not in source


@requiresGenerator
def test_eventWithoutTheSectionsKeepsTheLogFieldAndTheSchemaOrderConstructor(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Event", _withoutEventSections(_realConventionsText()))

    assert "    public String log;\n" in source
    assert "public Event(String id, String log, String created, Boolean isDelivered, String subscription, String workspaceId) {" in source
    assert "Deserializer" not in source


# 4. validacao na borda

def _loadEvents(extractSchema, tmpPath, text: str, resource: str = "Event"):
    conventions = tmpPath / f"events-{abs(hash(text))}.yaml"
    conventions.write_text(text, encoding="utf-8")
    return extractSchema.javaEventShape(resource, conventions)


def test_javaEventShapeReturnsTheEntryAndDefaultsGsonToNothing(extractSchema, tmpPath):
    text = "javaEvents:\n  Event:\n    subscriptions: [transfer, boleto-payment]\n"

    assert _loadEvents(extractSchema, tmpPath, text) == {"subscriptions": ["transfer", "boleto-payment"], "gson": []}


def test_javaEventShapeIsNoneWithoutEntryOrSection(extractSchema, tmpPath):
    assert _loadEvents(extractSchema, tmpPath, "javaEvents:\n  Event:\n    subscriptions: [transfer]\n", "Transfer") is None
    assert _loadEvents(extractSchema, tmpPath, "javaTypes:\n") is None


@pytest.mark.parametrize("text, message", [
    ("javaEvents:\n  Event:\n    subscriptions: [Boleto-Payment]\n", r"Event: javaEvents\.subscriptions.*'Boleto-Payment'"),
    ("javaEvents:\n  Event:\n    subscriptions: [boleto_payment]\n", r"Event: javaEvents\.subscriptions.*'boleto_payment'"),
    ("javaEvents:\n  Event:\n    subscriptions: [transfer-]\n", r"Event: javaEvents\.subscriptions.*'transfer-'"),
    ("javaEvents:\n  Event:\n    subscriptions: [9lives]\n", r"Event: javaEvents\.subscriptions.*'9lives'"),
    ("javaEvents:\n  Event:\n    subscriptions: [7]\n", r"Event: javaEvents\.subscriptions.*7"),
    ('javaEvents:\n  Event:\n    subscriptions: ["transfer\\n"]\n', r"Event: javaEvents\.subscriptions.*'transfer\\n'"),
    ("javaEvents:\n  Event:\n    subscriptions: ['tr\"ansfer']\n", r"Event: javaEvents\.subscriptions.*'tr\"ansfer'"),
    ("javaEvents:\n  Event:\n    subscriptions: ['tr\\ansfer']\n", r"Event: javaEvents\.subscriptions.*'tr\\\\ansfer'"),
    ("javaEvents:\n  Event:\n    subscriptions: [transfer, transfer]\n", r"Event: javaEvents\.subscriptions.*repete.*'transfer'"),
    ("javaEvents:\n  Event:\n    subscriptions: []\n", r"Event: javaEvents\.subscriptions.*lista nao vazia"),
    ("javaEvents:\n  Event:\n    subscriptions: transfer\n", r"Event: javaEvents\.subscriptions.*'transfer'"),
    ("javaEvents:\n  Event: {}\n", r"Event: javaEvents\.subscriptions ausente"),
    ("javaEvents:\n  Event: [transfer]\n", r"Event: javaEvents.*mapa"),
    ("javaEvents:\n  Event:\n    subscriptions: [transfer]\n    gson: [invoice]\n", r"Event: javaEvents\.gson.*'invoice'.*subscriptions"),
    ("javaEvents:\n  Event:\n    subscriptions: [transfer]\n    gson: [transfer, transfer]\n", r"Event: javaEvents\.gson.*repete.*'transfer'"),
    ("javaEvents:\n  Event:\n    subscriptions: [transfer]\n    gson: transfer\n", r"Event: javaEvents\.gson.*'transfer'"),
    ("javaEvents:\n  Event:\n    subscriptions: [transfer]\n    other: 1\n", r"Event: javaEvents chave desconhecida: other"),
    ("javaEvents:\n  Evnt:\n    subscriptions: [transfer]\n", r"Evnt: javaEvents cita recurso"),
    ("javaEvents: [Event]\n", r"javaEvents deve ser um mapa"),
])
def test_invalidJavaEventsDataFailsNamingTheResourceTheKeyAndTheValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=message):
        _loadEvents(extractSchema, tmpPath, text)


def _loadOmitted(extractSchema, tmpPath, text: str, resource: str = "Event"):
    conventions = tmpPath / f"omitted-{abs(hash(text))}.yaml"
    conventions.write_text(text, encoding="utf-8")
    return extractSchema.javaOmittedFields(resource, conventions)


def test_javaOmittedFieldsReturnsTheListOfTheResourceAndNothingOtherwise(extractSchema, tmpPath):
    text = "javaOmittedFields:\n  Event: [log]\n"

    assert _loadOmitted(extractSchema, tmpPath, text) == ["log"]
    assert _loadOmitted(extractSchema, tmpPath, text, "Transfer") == []
    assert _loadOmitted(extractSchema, tmpPath, "javaTypes:\n") == []


@pytest.mark.parametrize("text, message", [
    ("javaOmittedFields:\n  Evnt: [log]\n", r"Evnt: javaOmittedFields cita recurso"),
    ("javaOmittedFields:\n  Event: log\n", r"Event: javaOmittedFields.*lista nao vazia.*'log'"),
    ("javaOmittedFields:\n  Event: []\n", r"Event: javaOmittedFields.*lista nao vazia"),
    ("javaOmittedFields:\n  Event: [log, log]\n", r"Event: javaOmittedFields repete 'log'"),
    ("javaOmittedFields:\n  Event: [7]\n", r"Event: javaOmittedFields.*7"),
    ("javaOmittedFields: [Event]\n", r"javaOmittedFields deve ser um mapa"),
])
def test_invalidJavaOmittedFieldsFailsNamingTheResourceAndTheValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=message):
        _loadOmitted(extractSchema, tmpPath, text)


def test_omittingAFieldTheSchemaDoesNotHaveOrTheIdFailsNamingResourceAndField(javaCode):
    schema, _ = _schemaOf("Event")

    with pytest.raises(ValueError, match=r"Event\.ghost: javaOmittedFields cita campo que o schema nao tem"):
        javaCode.omitFields(schema, "Event", ["ghost"])
    with pytest.raises(ValueError, match=r"Event\.id: javaOmittedFields cita o campo id"):
        javaCode.omitFields(schema, "Event", ["id"])


def test_omitFieldsDropsOnlyThePropertyAndKeepsTheRestOfTheSchema(javaCode):
    schema, _ = _schemaOf("Event")
    omitted = javaCode.omitFields(schema, "Event", ["log"])

    assert "log" not in omitted["properties"]
    assert list(omitted["properties"]) == [name for name in schema["properties"] if name != "log"]
    assert omitted["x-sdk-docs"] == schema["x-sdk-docs"]
    assert "log" in schema["properties"]
    assert javaCode.omitFields(schema, "Event", []) is schema


def test_renderEventsDerivesTheClassesTheLogTypesAndTheSuperCallFromTheBaseConstructor(javaCode):
    ctorFields = [{"name": "created", "javaType": "String", "isId": False},
                  {"name": "id", "javaType": "String", "isId": True}]
    shape = {"subscriptions": ["boleto-holmes", "invoice"], "gson": ["invoice"]}

    events = javaCode.renderEvents("Event", shape, ctorFields, frozenset({"BoletoHolmes", "Invoice"}),
                                   frozenset({"BoletoHolmes", "Invoice"}))

    assert events["cases"] == [
        {"subscription": "boleto-holmes", "className": "BoletoHolmesEvent", "isGson": False},
        {"subscription": "invoice", "className": "InvoiceEvent", "isGson": True},
    ]
    assert events["subclasses"] == [
        {"className": "BoletoHolmesEvent", "logType": "BoletoHolmes.Log"},
        {"className": "InvoiceEvent", "logType": "Invoice.Log"},
        {"className": "UnknownEvent", "logType": "JsonObject"},
    ]
    assert events["baseParameters"] == "String created, String id"
    assert events["superArguments"] == "created, id"
    assert events["unknownClassName"] == "UnknownEvent"
    assert events["usesGsonEvent"] is True
    plain = {"subscriptions": ["invoice"], "gson": []}
    assert javaCode.renderEvents("Event", plain, ctorFields, frozenset({"Invoice"}),
                                 frozenset({"Invoice"}))["usesGsonEvent"] is False


@pytest.mark.parametrize("subscription, message", [
    ("ghost-thing", r"Event: javaEvents\.subscriptions 'ghost-thing' cita o recurso GhostThing, que nao existe"),
    ("unknown", r"Event: javaEvents\.subscriptions 'unknown' cita o recurso Unknown, que nao existe"),
    ("event", r"Event: javaEvents\.subscriptions 'event' cita o proprio recurso"),
])
def test_aSubscriptionWhoseResourceIsNotAKnownOneOrIsTheEventItselfFailsNamingResourceKeyAndValue(
    javaCode, subscription, message
):
    ctorFields = [{"name": "id", "javaType": "String", "isId": True}]

    with pytest.raises(ValueError, match=message):
        javaCode.renderEvents("Event", {"subscriptions": [subscription], "gson": []}, ctorFields,
                              frozenset({"Event", "Invoice"}), frozenset({"Event", "Invoice"}))


# 5. o recurso da subscription tem de ter Log

def test_aSubscriptionWhoseResourceHasNoLogFailsNamingResourceKeyAndValue(javaCode):
    ctorFields = [{"name": "id", "javaType": "String", "isId": True}]
    names = frozenset({"Event", "Balance", "Invoice"})

    with pytest.raises(ValueError, match=r"Event: javaEvents\.subscriptions 'balance' cita o recurso Balance, que nao tem Log"):
        javaCode.renderEvents("Event", {"subscriptions": ["invoice", "balance"], "gson": []}, ctorFields, names,
                              frozenset({"Invoice"}))


def test_aSubscriptionWhoseResourceHasALogIsAccepted(javaCode):
    ctorFields = [{"name": "id", "javaType": "String", "isId": True}]
    names = frozenset({"Event", "PaymentRequest"})

    events = javaCode.renderEvents("Event", {"subscriptions": ["payment-request"], "gson": []}, ctorFields, names,
                                   frozenset({"PaymentRequest"}))

    assert events["subclasses"][0] == {"className": "PaymentRequestEvent", "logType": "PaymentRequest.Log"}


_LOG_SPEC = """components:
  schemas:
    Invoice:
      x-sdk-log: true
    Balance:
      x-sdk-get: true
    PaymentRequest:
      x-sdk-page: true
    Transfer:
      x-sdk-log: true
"""


def test_aResourceHasALogWhenItHasTheSpecFlagOrALogInItsInnerClasses(buildResource, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text(
        "javaInnerClasses:\n"
        "  PaymentRequest:\n    Log:\n      fields: {created: String}\n"
        "  Balance:\n    Entry:\n      fields: {created: String}\n",
        encoding="utf-8",
    )
    spec = buildResource.loadFast(_LOG_SPEC)

    assert buildResource.logResourceNames(spec, conventions) == frozenset({"Invoice", "Transfer", "PaymentRequest"})


def test_everySubscriptionOfTheDataDerivesAResourceThatHasALog(buildResource, extractSchema):
    logResources = _realLogResources(buildResource)
    resources = {"".join(word.capitalize() for word in subscription.split("-"))
                 for subscription in _entry()["subscriptions"]}

    assert "PaymentRequest" in resources
    assert resources <= logResources
