import re
import yaml
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavaSdk

SCHEMAS = REPO_ROOT / "apis/schemas"
REAL_SOURCES = REPO_ROOT / "_references/sdk-java/src/main/java/com/starkbank"


def _generated(buildResource, tmpPath, resource: str, conventionsText: str) -> str:
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text(conventionsText, encoding="utf-8")
    staged = buildResource.stageJavaDocs(resource, tmpPath / "staged", conventions)
    output = tmpPath / "out"
    code = buildResource.generate(resource, buildResource.GENERATORS["java"][0], output, str(staged["spec"]))
    assert code == 0
    return (output / f"src/main/java/com/starkbank/{resource}.java").read_text(encoding="utf-8")


def _outer(source: str) -> str:
    return source.split("public final static class Log")[0]


@requiresGenerator
def test_overrideChangesTheFieldTheAllArgsConstructorAndTheMapCast(buildResource, tmpPath):
    source = _outer(_generated(buildResource, tmpPath, "Transfer", "javaTypes:\n  Transfer:\n    amount: Integer\n"))

    assert re.search(r"^    public Integer amount;$", source, re.M)
    assert re.search(r"public Transfer\(.*\bInteger amount\b", source)
    assert "this.amount = amount == null ? null : amount.intValue();" in source
    assert "Long amount" not in source


@requiresGenerator
def test_withoutOverrideTheDefaultTypeIsKept(buildResource, tmpPath):
    source = _outer(_generated(buildResource, tmpPath, "Transfer", "javaTypes:\n"))

    assert re.search(r"^    public Long amount;$", source, re.M)
    assert re.search(r"public Transfer\(.*\bLong amount\b", source)


def _realConventionsText() -> str:
    return (REPO_ROOT / "apis/type-conventions.yaml").read_text(encoding="utf-8")


def _realOuterFields(resource: str) -> dict:
    """`public T campo;` da classe externa (4 espacos, antes de qualquer classe interna): `amount` do Boleto e do
    Invoice tambem existe na classe interna, com outro tipo."""
    source = (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")
    inner = re.search(r"^    public (?:final )?static class", source, re.M)
    outer = source[:inner.start()] if inner else source
    declared = re.findall(r"^    public ([\w<>\[\], ]+?) (\w+);", outer, re.M)
    return {name: javaType for javaType, name in reversed(declared)}


def test_everyTopLevelKeyOfTheTableIsAnExistingResource(buildResource):
    resources = set(buildResource.loadTool("parity-report").schemaResources())

    assert set(yaml.safe_load(_realConventionsText())["javaTypes"]) <= resources


@requiresJavaSdk
def test_everyOverrideMatchesTheTypeTheRealSdkDeclaresInTheOuterClass():
    table = yaml.safe_load(_realConventionsText())["javaTypes"]

    divergent = [f"{resource}.{name}: tabela={javaType} real={_realOuterFields(resource).get(name)}"
                 for resource, fields in table.items() for name, javaType in fields.items()
                 if _realOuterFields(resource).get(name) != javaType]

    assert divergent == []


def test_everyOverrideIsAcceptedByTheProfileAndPointsToAFieldOfItsSchema(javaCode, javaDocs, extractSchema):
    table = yaml.safe_load(_realConventionsText())["javaTypes"]

    for resource in table:
        schemas = yaml.safe_load((SCHEMAS / f"{resource.lower()}.yaml").read_text(encoding="utf-8"))["components"]
        entity = javaDocs.describeEntity(schemas["schemas"][resource], schemas["schemas"][f"{resource}Create"])
        overrides = extractSchema.javaTypeOverrides(resource)
        javaCode.renderJavaCode(schemas["schemas"][resource], entity, resource, overrides)


def _real(buildResource, tmpPath, resource: str) -> str:
    return _outer(_generated(buildResource, tmpPath, resource, _realConventionsText()))


@requiresGenerator
def test_transferTypesAmountAsPrimitiveLongAndMetadataAsHashMap(buildResource, tmpPath):
    source = _real(buildResource, tmpPath, "Transfer")

    assert re.search(r"^    public long amount;$", source, re.M)
    assert re.search(r"^    public HashMap<String, Object> metadata;$", source, re.M)
    assert 'this.amount = ((Number) dataCopy.remove("amount")).longValue();' in source
    assert "this.metadata = null;" in source


@requiresGenerator
def test_merchantPurchaseReadsMetadataAsAMapWithSuppressAndInstallmentCountAsInt(buildResource, tmpPath):
    source = _real(buildResource, tmpPath, "MerchantPurchase")

    assert re.search(r"^    public int installmentCount;$", source, re.M)
    assert 'this.installmentCount = ((Number) dataCopy.remove("installmentCount")).intValue();' in source
    assert 'this.metadata = (Map<String, Object>) dataCopy.remove("metadata");' in source
    assert '@SuppressWarnings("unchecked")' in source


@requiresGenerator
def test_invoiceTypesAmountAsNumberReadWithADirectCast(buildResource, tmpPath):
    source = _real(buildResource, tmpPath, "Invoice")

    assert re.search(r"^    public Number amount;$", source, re.M)
    assert 'this.amount = (Number) dataCopy.remove("amount");' in source


@requiresGenerator
def test_merchantSessionTypesListsAndExpiration(buildResource, tmpPath):
    source = _real(buildResource, tmpPath, "MerchantSession")

    assert re.search(r"^    public List<String> allowedIps;$", source, re.M)
    assert re.search(r"^    public Number expiration;$", source, re.M)
    assert 'this.allowedIps = (List<String>) dataCopy.remove("allowedIps");' in source
    assert "import java.util.List;" in source


@requiresGenerator
def test_darfPaymentTypesAmountAndFeeAsStringAndNominalAsLong(buildResource, tmpPath):
    source = _real(buildResource, tmpPath, "DarfPayment")

    assert re.search(r"^    public String amount;$", source, re.M)
    assert re.search(r"^    public long nominalAmount;$", source, re.M)
    assert "this.amount = null;" in source


@requiresGenerator
def test_returnOnlyPrimitiveIsNeverAssignedNull(buildResource, tmpPath):
    source = _real(buildResource, tmpPath, "Balance")

    assert re.search(r"^    public long amount;$", source, re.M)
    assert "this.amount = 0;" in source
    assert "this.amount = null;" not in source


def _realOpenResources(resources: list) -> set:
    declaration = re.compile(r"^public (final )?class ", re.M)
    opened = set()
    for resource in resources:
        path = REAL_SOURCES / f"{resource}.java"
        match = declaration.search(path.read_text(encoding="utf-8")) if path.is_file() else None
        if match and not match.group(1):
            opened.add(resource)
    return opened


def _openClasses() -> list:
    return yaml.safe_load(_realConventionsText())["javaOpenClasses"]


@requiresJavaSdk
def test_everyOpenClassIsNotFinalInTheRealSdk():
    assert _realOpenResources(_openClasses()) == set(_openClasses())


@requiresJavaSdk
def test_theOpenClassListIsCompleteAgainstTheRealSdk(buildResource):
    resources = buildResource.loadTool("parity-report").schemaResources()

    assert _realOpenResources(resources) == set(_openClasses())


def test_theOpenClassListIsAlphabeticalWithoutRepeats():
    assert _openClasses() == sorted(set(_openClasses()))


@requiresGenerator
def test_resourceInTheOpenListIsGeneratedWithoutFinal(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", "javaOpenClasses:\n  - Transfer\n")

    assert "public class Transfer extends Resource {" in source
    assert "public final class Transfer" not in source


@requiresGenerator
def test_resourceOutsideTheOpenListIsGeneratedFinal(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", "javaOpenClasses:\n  - Balance\n")

    assert "public final class Transfer extends Resource {" in source


@requiresGenerator
def test_openClassOnlyLiftsTheOuterClassAndLeavesTheLogFinal(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", "javaOpenClasses:\n  - Transfer\n")

    assert "public final static class Log" in source


def test_unknownResourceInTheOpenListFailsNamingIt(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("javaOpenClasses:\n  - Balance\n  - Transferr\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Transferr"):
        extractSchema.javaOpenClass("Balance", conventions)


def test_javaOpenClassIsTrueOnlyForListedResources(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("javaOpenClasses:\n  - Balance\n", encoding="utf-8")

    assert extractSchema.javaOpenClass("Balance", conventions) is True
    assert extractSchema.javaOpenClass("Transfer", conventions) is False


def test_javaOpenClassIsFalseWhenTheSectionIsAbsent(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("javaTypes:\n", encoding="utf-8")

    assert extractSchema.javaOpenClass("Balance", conventions) is False


def test_renderJavaCodeCarriesTheOpenFlag(javaCode, javaDocs):
    schemas = yaml.safe_load((SCHEMAS / "balance.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    entity = javaDocs.describeEntity(schemas["Balance"], schemas["BalanceCreate"])

    assert javaCode.renderJavaCode(schemas["Balance"], entity, "Balance", {})["open"] is False
    assert javaCode.renderJavaCode(schemas["Balance"], entity, "Balance", {}, isOpen=True)["open"] is True
