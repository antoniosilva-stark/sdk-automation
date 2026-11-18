import re
import shutil
import subprocess

import yaml
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavac, requiresJavaSdk
from test_java_field_types import (
    _balancedBlock,
    _generated,
    _outerSource,
    _realConventionsText,
    _realInnerBlock,
    _realLongestPublicConstructor,
    _realOuterFields,
    _schemaOf,
    _splitParameters,
)
from test_java_payment_request_log import _generatedBlock, _members

SOURCEPATH = REPO_ROOT / "_references/sdk-java/src/main/java"
EXTRA_RESOURCES = ["InvoicePullSubscription", "MerchantPurchase", "MerchantSession", "VerifiedTransfer"]
FIVE_RESOURCES = [*EXTRA_RESOURCES, "Transfer"]
# o real tem o ctor all-args com os mesmos nomes que o gerado so nestes tres; MerchantPurchase e Transfer carregam
# campos da spec que o real nao declara (cardNumber..., displayDescription), e o ctor deles segue um gap a parte
SAME_NAME_SET = ["InvoicePullSubscription", "MerchantSession", "VerifiedTransfer"]


def _extras() -> dict:
    return yaml.safe_load(_realConventionsText())["javaExtraFields"]


def _extraItems() -> list:
    return [(resource, name, spec) for resource, fields in _extras().items() for name, spec in fields.items()]


def _normalised(text: str) -> str:
    return " ".join(text.split())


def _outerOf(source: str) -> str:
    inner = re.search(r"^    public (?:final )?static class", source, re.M)
    return source[:inner.start()] if inner else source


def _outerFieldsOf(source: str) -> dict:
    declared = re.findall(r"^    public ([\w<>\[\], .]+?) (\w+);", _outerOf(source), re.M)
    return {name: javaType for javaType, name in declared}


def _generatedConstructor(source: str, resource: str) -> list:
    pattern = rf"^    public {resource}\s*\((?!Map<)(?!\))(.*?)\)\s*\{{"
    return _splitParameters(re.search(pattern, _outerOf(source), re.M | re.S).group(1))


# 1. os dados contra o real, nos dois sentidos

def test_theExtraFieldSectionIsAlphabeticalByResourceAndByField():
    assert list(_extras()) == sorted(_extras())
    assert all(list(fields) == sorted(fields) for fields in _extras().values())


@requiresJavaSdk
@pytest.mark.parametrize("resource, name, spec", _extraItems(), ids=lambda value: value if isinstance(value, str) else "")
def test_everyExtraFieldIsDeclaredByTheRealWithTheSameTypeAndTakenByItsLongestConstructor(resource, name, spec):
    assert _realOuterFields(resource).get(name) == spec["type"]
    assert (spec["type"], name) in _realLongestPublicConstructor(resource)


@requiresJavaSdk
@pytest.mark.parametrize("resource, name, spec", _extraItems(), ids=lambda value: value if isinstance(value, str) else "")
def test_theRealMapConstructorReadsEveryExtraField(resource, name, spec):
    assert re.search(rf'this\.{name} = .*dataCopy\.remove\("{name}"\);', _outerSource(resource))


@requiresJavaSdk
@pytest.mark.parametrize("resource, name, spec", _extraItems(), ids=lambda value: value if isinstance(value, str) else "")
def test_theDescriptionOfAnExtraFieldIsTheRealJavadocWordingAndAbsentWhenTheRealHasNone(resource, name, spec):
    real = _normalised(re.sub(r"\n\s*\*", " ", _outerSource(resource)))

    assert (f"{name} [" in real) is ("description" in spec)
    if "description" in spec:
        assert f"{name} [string]: {spec['description']}" in real


@pytest.mark.parametrize("resource, name, spec", _extraItems(), ids=lambda value: value if isinstance(value, str) else "")
def test_noExtraFieldRepeatsAFieldOfTheSpecSchema(resource, name, spec):
    assert name not in _schemaOf(resource)[0]["properties"]


@requiresJavaSdk
@pytest.mark.parametrize("resource", FIVE_RESOURCES)
def test_everyOuterFieldOfTheRealIsASchemaFieldOrAnExtraFieldOfTheFiveResources(resource):
    schemaNames = set(_schemaOf(resource)[0]["properties"])
    extraNames = set(_extras().get(resource) or {})

    assert sorted(set(_realOuterFields(resource)) - schemaNames - extraNames) == []


@requiresJavaSdk
@pytest.mark.parametrize("resource", ["MerchantSession", "VerifiedTransfer", "InvoicePullSubscription"])
def test_theConstructorOrderEntryOfAResourceWithAnExtraFieldPlacesItWhereTheRealDoes(resource):
    order = yaml.safe_load(_realConventionsText())["javaConstructorOrder"][resource]

    assert set(_extras()[resource]) <= set(order)
    assert order == [name for _, name in _realLongestPublicConstructor(resource)]


# 2. o contrato do dado, no limite

def _conventions(tmpPath, text: str):
    """O loader guarda o arquivo por caminho: cada texto ganha o seu."""
    path = tmpPath / f"type-conventions-{abs(hash(text))}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_theLoaderReadsTheTypeAndTheOptionalDescriptionAndDefaultsToNothing(extractSchema, tmpPath):
    conventions = _conventions(
        tmpPath,
        'javaExtraFields:\n  Balance:\n    code: {type: String, description: "the code"}\n    flag: {type: Boolean}\n',
    )

    assert extractSchema.javaExtraFields("Balance", conventions) == {
        "code": {"type": "String", "description": "the code"},
        "flag": {"type": "Boolean"},
    }
    assert extractSchema.javaExtraFields("Transfer", conventions) == {}
    assert extractSchema.javaExtraFields("Balance", _conventions(tmpPath, "javaTypes:\n")) == {}


@pytest.mark.parametrize("text, message", [
    ("javaExtraFields: [Balance]\n", r"javaExtraFields deve ser um mapa recurso"),
    ("javaExtraFields:\n  Balance: []\n", r"Balance: javaExtraFields deve ser um mapa nome -> \{type, description\}, veio \[\]"),
    ("javaExtraFields:\n  Balance: {}\n", r"Balance: javaExtraFields deve ser um mapa nome"),
    ("javaExtraFields:\n  Balance:\n    Code: {type: String}\n", r"Balance: javaExtraFields nome invalido: 'Code'"),
    ("javaExtraFields:\n  Balance:\n    class: {type: String}\n", r"Balance: javaExtraFields nome invalido: 'class'"),
    ("javaExtraFields:\n  Balance:\n    1: {type: String}\n", r"Balance: javaExtraFields nome invalido: 1"),
    ("javaExtraFields:\n  Balance:\n    code: String\n", r"Balance.code: javaExtraFields deve ser um mapa \{type, description\}, veio 'String'"),
    ("javaExtraFields:\n  Balance:\n    code: {description: x}\n", r"Balance.code: javaExtraFields.type ausente"),
    ("javaExtraFields:\n  Balance:\n    code: {type: 7}\n", r"Balance.code: javaExtraFields.type invalido: 7"),
    ("javaExtraFields:\n  Balance:\n    code: {type: String, after: id}\n", r"Balance.code: javaExtraFields chave desconhecida: after"),
    ("javaExtraFields:\n  Balance:\n    code: {type: String, description: ''}\n",
     r"Balance.code: javaExtraFields.description invalida: ''"),
    ("javaExtraFields:\n  Balance:\n    code: {type: String, description: 'a */ b'}\n",
     r"Balance.code: javaExtraFields.description invalida: 'a \*/ b'"),
    ('javaExtraFields:\n  Balance:\n    code: {type: String, description: "a\\nb"}\n',
     r"Balance.code: javaExtraFields.description invalida: 'a\\nb'"),
    ("javaExtraFields:\n  Balance:\n    code: {type: String, description: 'a\\b'}\n",
     r"Balance.code: javaExtraFields.description invalida: 'a\\\\b'"),
])
def test_invalidExtraFieldDataFailsNamingTheResourceTheKeyAndTheValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=message):
        extractSchema.javaExtraFields("Balance", _conventions(tmpPath, text))


def _extended(javaCode, javaDocs, extras: dict, overrides: dict | None = None, **arguments):
    schema, createSchema = _schemaOf("Balance")
    schema, createSchema = javaCode.addExtraFields(schema, createSchema, "Balance", extras)
    entity = javaDocs.describeEntity(schema, createSchema)
    return schema, javaCode.renderJavaCode(schema, entity, "Balance", overrides or {}, **arguments), entity


def test_anExtraFieldIsAppendedAsAnOptionalFieldTypedAsTheDataSaysAndReadByTheMapConstructor(javaCode, javaDocs):
    schema, code, entity = _extended(javaCode, javaDocs, {"confirmationMode": {"type": "String"}})

    assert list(schema["properties"])[-1] == "confirmationMode"
    assert [field["name"] for field in code["fields"]][-1] == "confirmationMode"
    assert code["fields"][-1] == {"name": "confirmationMode", "javaType": "String", "isId": False}
    assert code["ctorFields"][-1]["name"] == "confirmationMode"
    assert code["ctorMapBody"][-1] == 'this.confirmationMode = (String) dataCopy.remove("confirmationMode");'
    assert entity["fields"][-1]["returnOnly"] is False


def test_theFieldsOfTheSchemaAreUntouchedByAnExtraField(javaCode, javaDocs):
    schema, createSchema = _schemaOf("Balance")
    plain = javaCode.renderJavaCode(schema, javaDocs.describeEntity(schema, createSchema), "Balance", {})
    _, code, _ = _extended(javaCode, javaDocs, {"confirmationMode": {"type": "String"}})

    assert code["fields"][:-1] == plain["fields"]
    assert code["ctorMapBody"][:-1] == plain["ctorMapBody"]
    assert schema == _schemaOf("Balance")[0]
    assert createSchema == _schemaOf("Balance")[1]


def test_anExtraFieldWithANumericTypeIsConvertedLikeAnyOtherField(javaCode, javaDocs):
    _, code, entity = _extended(javaCode, javaDocs, {"limit": {"type": "Long"}})

    assert code["fields"][-1]["javaType"] == "Long"
    assert code["ctorMapBody"][-2:] == [
        'Number limit = (Number) dataCopy.remove("limit");',
        "this.limit = limit == null ? null : limit.longValue();",
    ]
    assert entity["fields"][-1]["type"] == "integer"


def test_theConstructorOrderCountsTheExtraFieldAsAField(javaCode, javaDocs):
    schema, createSchema = _schemaOf("Balance")
    names = [*schema["properties"], "confirmationMode"]
    extras = {"confirmationMode": {"type": "String"}}

    _, code, _ = _extended(javaCode, javaDocs, extras, ctorOrder=["confirmationMode", *names[:-1]])
    assert [field["name"] for field in code["ctorFields"]] == ["confirmationMode", *names[:-1]]

    with pytest.raises(ValueError, match=r"Balance: ordem do construtor nao e permutacao dos campos \(faltam: confirmationMode"):
        _extended(javaCode, javaDocs, extras, ctorOrder=names[:-1])


def test_theJavadocOfAnExtraFieldUsesTheDescriptionOrTheBareGenericLine(javaCode, javaDocs):
    extras = {
        "confirmationMode": {"type": "String"},
        "installmentId": {"type": "String", "description": 'unique id of the installment. ex: "5656565656565656"'},
    }
    schema, _, entity = _extended(javaCode, javaDocs, extras)

    rendered = javaDocs.renderDocs("Balance", schema.get("x-sdk-docs") or {}, set(), entity)["lines"]

    assert "@param confirmationMode [string]" in rendered["ctorAllDoc"]
    assert '@param installmentId [string]: unique id of the installment. ex: "5656565656565656"' in rendered["ctorAllDoc"]
    assert 'installmentId [string]: unique id of the installment. ex: "5656565656565656"' in rendered["ctorMapDoc"]
    assert "confirmationMode [string]" in rendered["dataDoc"]


@pytest.mark.parametrize("extras, message", [
    ({"amount": {"type": "String"}}, r"Balance\.amount: javaExtraFields cita campo que o schema ja tem"),
    ({"id": {"type": "String"}}, r"Balance\.id: javaExtraFields cita campo que o schema ja tem"),
    ({"code": {"type": "Strng"}}, r"Balance\.code: tipo de override 'Strng'"),
    ({"code": {"type": "List<Foo>"}}, r"Balance\.code: elemento 'Foo' de List nao e classe interna nem recurso gerado"),
])
def test_anExtraFieldThatTheSchemaHasOrTheProfileCannotReadFailsNamingTheResourceAndTheKey(
    javaCode, javaDocs, extras, message
):
    with pytest.raises(ValueError, match=message):
        _extended(javaCode, javaDocs, extras)


def test_anExtraFieldAlsoNamedInJavaTypesFailsInsteadOfBeingOverriddenSilently(javaCode, javaDocs):
    with pytest.raises(ValueError, match=r"Balance\.code: javaTypes cita campo declarado em javaExtraFields"):
        _extended(javaCode, javaDocs, {"code": {"type": "String"}}, overrides={"code": "Integer"})


def test_noExtraFieldsLeavesTheSchemaAndTheCreateSchemaAsTheyWere(javaCode):
    schema, createSchema = _schemaOf("Balance")

    assert javaCode.addExtraFields(schema, createSchema, "Balance", {}) == (schema, createSchema)
    assert javaCode.addExtraFields(schema, None, "Balance", {}) == (schema, None)


def test_anExtraFieldNamedLikeAFieldOnlyTheCreateSchemaHasFailsInsteadOfOverwritingIt(javaCode):
    schema, _ = _schemaOf("Balance")
    createSchema = {"properties": {"onlyOnCreate": {"type": "string", "description": "original"}}}

    with pytest.raises(ValueError, match="Balance.onlyOnCreate: javaExtraFields cita campo que o schema ja tem"):
        javaCode.addExtraFields(schema, createSchema, "Balance", {"onlyOnCreate": {"type": "String"}})


def test_anExtraFieldOfAResourceWithoutACreateSchemaStillMakesOne(javaCode):
    schema, _ = _schemaOf("Balance")

    _, createSchema = javaCode.addExtraFields(schema, None, "Balance", {"code": {"type": "String"}})

    assert list(createSchema["properties"]) == ["code"]


def test_theLoaderOfAnotherResourceIgnoresTheExtraFieldsOfThisOne(extractSchema, tmpPath):
    conventions = _conventions(tmpPath, "javaExtraFields:\n  Transfer:\n    code: {type: String}\n")

    assert extractSchema.javaExtraFields("Balance", conventions) == {}


def test_theOtherInnerClassesLoaderIndexesTheClassesOfEveryOtherResourceByQualifiedName(extractSchema, tmpPath):
    conventions = _conventions(
        tmpPath,
        "javaInnerClasses:\n  Balance:\n    Pair: {fields: {key: String}, constructors: [[key]]}\n"
        "  Transfer:\n    Rule: {fields: {key: String}, constructors: [[key]]}\n",
    )

    assert extractSchema.javaOtherInnerClasses("Balance", conventions) == {
        "Transfer.Rule": {"fields": {"key": "String"}, "constructors": [["key"]]},
    }
    assert extractSchema.javaOtherInnerClasses("Deposit", conventions).keys() == {"Balance.Pair", "Transfer.Rule"}
    assert extractSchema.javaOtherInnerClasses("Balance", _conventions(tmpPath, "javaTypes:\n")) == {}


# 3. as classes geradas

@pytest.fixture(scope="module")
def generatedFive(tmp_path_factory):
    from conftest import _loadTool
    buildResource = _loadTool("build-resource.py")
    return {resource: _generated(buildResource, tmp_path_factory.mktemp(resource), resource, _realConventionsText())
            for resource in FIVE_RESOURCES}


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", FIVE_RESOURCES)
def test_generatedClassDeclaresEveryFieldOfTheRealWithItsType(generatedFive, resource):
    generated = _outerFieldsOf(generatedFive[resource])

    divergent = {name: (javaType, generated.get(name)) for name, javaType in _realOuterFields(resource).items()
                 if generated.get(name) != javaType}

    assert divergent == {}


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", SAME_NAME_SET)
def test_generatedAllArgsConstructorIsTheRealOneWhereTheNameSetsAgree(generatedFive, resource):
    assert _generatedConstructor(generatedFive[resource], resource) == _realLongestPublicConstructor(resource)


# o que separa o ctor all-args destes dois do real depois desta entrega: (parametros so do real, so do gerado). Os so do
# gerado sao campos da spec que o real nao declara (javaOmittedFields os tiraria) e `int fee` e o tipo de parametro do
# real contra o `Integer fee` gerado (javaConstructorTypes); nenhum dos dois e campo extra nem a tipagem de `rules`
RESIDUAL_CONSTRUCTOR_GAP = {
    "MerchantPurchase": ([("int", "fee")], ["cardExpiration", "cardNumber", "cardSecurityCode", "fee"]),
    "Transfer": ([], ["displayDescription"]),
}


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(RESIDUAL_CONSTRUCTOR_GAP))
def test_generatedAllArgsConstructorOfTheOtherTwoCarriesEveryRealParameterButTheKnownResidual(generatedFive, resource):
    generated = set(_generatedConstructor(generatedFive[resource], resource))
    real = set(_realLongestPublicConstructor(resource))
    onlyReal, onlyGenerated = RESIDUAL_CONSTRUCTOR_GAP[resource]

    assert sorted(real - generated) == onlyReal
    assert sorted(name for _, name in generated - real) == onlyGenerated


@requiresGenerator
@pytest.mark.parametrize("resource, name", [(resource, name) for resource, name, _ in _extraItems()])
def test_generatedMapConstructorReadsTheExtraFieldFromTheMap(generatedFive, resource, name):
    assert f'this.{name} = (String) dataCopy.remove("{name}");' in generatedFive[resource]
    assert f"this.{name} = null;" not in generatedFive[resource]


@requiresGenerator
@pytest.mark.parametrize("resource, name, spec", _extraItems(), ids=lambda value: value if isinstance(value, str) else "")
def test_generatedJavadocOfTheExtraFieldFollowsTheDataDescription(generatedFive, resource, name, spec):
    line = f"@param {name} [string]"
    expected = f"{line}: {spec['description']}" if "description" in spec else line

    assert re.search(rf"^     \* {re.escape(expected)}$", generatedFive[resource], re.M)


def _withoutDeserializer(block: str) -> str:
    match = re.search(r"^        public static class Deserializer\b[^{]*\{", block, re.M)
    return block.replace(_balancedBlock(block, match), "")


@requiresGenerator
@requiresJavaSdk
def test_generatedTransferRuleIsTheRealOneMinusTheDeserializerTheWaiverKeeps(generatedFive):
    real = _withoutDeserializer(_realInnerBlock("Transfer", "Rule"))
    generated = _generatedBlock(generatedFive["Transfer"], "Rule")

    # o real le `value` sem cast e o gerador escreve o cast `(Object)` do perfil: a mesma atribuicao
    assert _members(generated.replace("(Object) dataCopy", "dataCopy")) == _members(real)


@requiresGenerator
def test_generatedTransferTypesRulesAsTheRuleListAndReadsItThroughTheParseHelper(generatedFive):
    source = generatedFive["Transfer"]

    assert re.search(r"^    public List<Rule> rules;$", source, re.M)
    assert 'this.rules = parseRules((List<Object>) dataCopy.remove("rules"));' in source
    assert "private static List<Rule> parseRules(List<Object> items) throws Exception {" in source
    assert "parsed.add(new Rule((Map<String, Object>) item));" in source
    assert "Deserializer" not in source


@requiresGenerator
def test_generatedVerifiedTransferReadsTheRulesOfTheTransferThroughItsMapConstructor(generatedFive):
    source = generatedFive["VerifiedTransfer"]

    assert re.search(r"^    public List<Transfer.Rule> rules;$", source, re.M)
    assert "private static List<Transfer.Rule> parseRules(List<Object> items) throws Exception {" in source
    assert "parsed.add(new Transfer.Rule((Map<String, Object>) item));" in source
    assert "class Rule" not in source


@requiresGenerator
@requiresJavac
@pytest.mark.parametrize("resource", FIVE_RESOURCES)
def test_generatedResourceWithExtraFieldsOrRulesPassesTheStrictGate(builtOnce, resource):
    code, out, _ = builtOnce(resource, "java")

    assert code == 0, out
    assert "sintaxe (javac)" in out
    assert "DEAD_IMPORT" not in out


_MAIN = """import com.starkbank.*;
import java.util.*;

public class Main {
    static Map<String, Object> map(Object... pairs) {
        Map<String, Object> result = new HashMap<>();
        for (int index = 0; index < pairs.length; index += 2) {
            result.put((String) pairs[index], pairs[index + 1]);
        }
        return result;
    }

    public static void main(String[] args) throws Exception {
        Transfer.Rule rule = new Transfer.Rule("resendingLimit", 5);
        Transfer.Rule fromMap = new Transfer.Rule(map("key", "flag", "value", true));
        System.out.println("rule -> " + rule.key + " " + rule.value + " " + fromMap.key + " " + fromMap.value);
        System.out.println("empty rule -> " + new Transfer.Rule().key);
        Transfer transfer = new Transfer(map("amount", 1L, "rules", List.of(map("key", "a", "value", 2), rule)));
        System.out.println("transfer -> " + transfer.rules.size() + " " + transfer.rules.get(0).key + " " + (transfer.rules.get(1) == rule));
        VerifiedTransfer verified = new VerifiedTransfer(map("amount", 1L, "accountType", "savings", "rules", List.of(map("key", "b", "value", 3))));
        Transfer.Rule verifiedRule = verified.rules.get(0);
        System.out.println("verified -> " + verified.accountType + " " + verifiedRule.key + " " + verifiedRule.value);
        System.out.println("session -> " + new MerchantSession(map("confirmationMode", "manual")).confirmationMode);
        System.out.println("purchase -> " + new MerchantPurchase(map("amount", 1L, "installmentCount", 1, "confirmationMode", "automatic")).confirmationMode);
        InvoicePullSubscription subscription = new InvoicePullSubscription(map("installmentId", "77"));
        System.out.println("subscription -> " + subscription.installmentId);
        try {
            new MerchantSession(map("bogus", 1));
        } catch (Exception error) {
            System.out.println("unknown -> " + error.getMessage());
        }
    }
}
"""


def _withoutTheTransferRuleDeserializer() -> str:
    """O `Rest` do real chama o `GsonAdapters`, que registra o `Transfer.Rule.Deserializer`; o gerador ainda nao o emite
    (esta waived em apis/java-handwritten.yaml), entao o teste compila uma copia do GsonAdapters sem essa linha."""
    real = (SOURCEPATH / "com/starkbank/utils/GsonAdapters.java").read_text(encoding="utf-8")
    lines = [line for line in real.splitlines() if "Transfer.Rule.Deserializer" not in line]
    assert len(lines) == len(real.splitlines()) - 1
    return "\n".join(lines) + "\n"


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedResourcesCompileWithTheRealSiblingsAndBehaveAsTheRealOnes(
    builtOnce, tmp_path_factory, javaRuntimeClasspath
):
    work = tmp_path_factory.mktemp("extra-fields-runtime")
    generated = []
    for resource in FIVE_RESOURCES:
        code, out, target = builtOnce(resource, "java")
        assert code == 0, out
        copy = work / f"src/com/starkbank/{resource}.java"
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(target / f"src/main/java/com/starkbank/{resource}.java", copy)
        generated.append(copy)
    (work / "src/Main.java").write_text(_MAIN, encoding="utf-8")
    shim = work / "src/com/starkbank/utils/GsonAdapters.java"
    shim.parent.mkdir(parents=True)
    shim.write_text(_withoutTheTransferRuleDeserializer(), encoding="utf-8")

    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath", str(SOURCEPATH),
         *map(str, generated), str(shim), str(work / "src/Main.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run(["java", "-cp", f"{work / 'out'}:{javaRuntimeClasspath}", "Main"], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == [
        "rule -> resendingLimit 5 flag true",
        "empty rule -> null",
        "transfer -> 2 a true",
        "verified -> savings b 3",
        "session -> manual",
        "purchase -> automatic",
        "subscription -> 77",
        "unknown -> Unknown parameters used in constructor: [bogus]",
    ]


# 4. o que nao declara a secao fica como estava

@requiresGenerator
def test_resourceWithoutAnEntryIsGeneratedIdenticallyWhateverOtherResourcesDeclare(buildResource, tmpPath):
    other = ("javaExtraFields:\n  Transfer:\n    code: {type: String}\n"
             "javaInnerClasses:\n  Transfer:\n    Pair:\n      fields: {key: String}\n      constructors: [[key]]\n")
    plain = _generated(buildResource, tmpPath / "plain", "Balance", "javaTypes:\n")

    assert _generated(buildResource, tmpPath / "other", "Balance", other) == plain
    assert "String code" not in plain


@requiresGenerator
def test_aResourceWithAnExtraFieldDeclaresAssignsAndReadsIt(buildResource, tmpPath):
    source = _generated(
        buildResource, tmpPath, "Balance",
        "javaExtraFields:\n  Balance:\n    code: {type: String, description: the code}\n",
    )

    assert "    public String code;\n" in source
    assert "        this.code = code;\n" in source
    assert '        this.code = (String) dataCopy.remove("code");\n' in source
    assert "     * @param code [string]: the code\n" in source
