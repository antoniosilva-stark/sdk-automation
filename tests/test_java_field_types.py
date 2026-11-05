import re
import yaml
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavaSdk

SCHEMAS = REPO_ROOT / "apis/schemas"
REAL_SOURCES = REPO_ROOT / "_references/sdk-java/src/main/java/com/starkbank"


def _generated(buildResource, tmpPath, resource: str, conventionsText: str) -> str:
    tmpPath.mkdir(parents=True, exist_ok=True)
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
    declared = re.findall(r"^    public ([\w<>\[\], ]+?) (\w+);", _outerSource(resource), re.M)
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


def _outerSource(resource: str) -> str:
    source = (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")
    inner = re.search(r"^    public (?:final )?static class", source, re.M)
    return source[:inner.start()] if inner else source


def _splitParameters(text: str) -> list:
    """Divide em virgulas de nivel 0: `HashMap<String, Object>` e um parametro so."""
    parts, depth, current = [], 0, ""
    for character in text:
        depth += character == "<"
        depth -= character == ">"
        if character == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += character
    parts.append(current)
    pairs = []
    for part in parts:
        javaType, _, name = " ".join(part.split()).rpartition(" ")
        if name:
            pairs.append((javaType, name))
    return pairs


def _realLongestPublicConstructor(resource: str) -> list:
    pattern = rf"^    public {resource}\s*\((.*?)\)\s*(?:throws [\w, ]+)?\{{"
    constructors = [_splitParameters(match.group(1))
                    for match in re.finditer(pattern, _outerSource(resource), re.M | re.S)]
    return max(constructors, key=len, default=[])


def _constructorSection(name: str) -> dict:
    return yaml.safe_load(_realConventionsText())[name]


def _schemaOf(resource: str) -> tuple:
    text = (SCHEMAS / f"{resource.lower()}.yaml").read_text(encoding="utf-8")
    schemas = yaml.safe_load(text)["components"]["schemas"]
    return schemas[resource], schemas[f"{resource}Create"]


def _generatedCtorFields(javaCode, javaDocs, extractSchema, resource: str) -> list:
    schema, createSchema = _schemaOf(resource)
    return javaCode.renderJavaCode(
        schema,
        javaDocs.describeEntity(schema, createSchema),
        resource,
        extractSchema.javaTypeOverrides(resource),
        ctorOrder=extractSchema.javaConstructorOrder(resource),
        ctorTypes=extractSchema.javaConstructorTypes(resource),
    )["ctorFields"]


def _resourcesWithTheRealNameSet(buildResource) -> dict:
    """Recurso -> construtor publico mais longo do real, so quando tem os mesmos nomes que o schema; os demais
    (Event, Transfer...) divergem em nomes e sao da ME 9.7."""
    matching = {}
    for resource in buildResource.loadTool("parity-report").schemaResources():
        if not (REAL_SOURCES / f"{resource}.java").is_file():
            continue
        real = _realLongestPublicConstructor(resource)
        if {name for _, name in real} == set(_schemaOf(resource)[0]["properties"]):
            matching[resource] = real
    return matching


@requiresJavaSdk
def test_everyConstructorOrderEntryIsTheNameOrderOfTheLongestPublicRealConstructor():
    divergent = [f"{resource}: tabela={order} real={[name for _, name in _realLongestPublicConstructor(resource)]}"
                 for resource, order in _constructorSection("javaConstructorOrder").items()
                 if order != [name for _, name in _realLongestPublicConstructor(resource)]]

    assert divergent == []


def test_everyConstructorOrderEntryHoldsExactlyTheSchemaFields():
    for resource, order in _constructorSection("javaConstructorOrder").items():
        assert sorted(order) == sorted(_schemaOf(resource)[0]["properties"]), resource


@requiresJavaSdk
def test_generatedConstructorFollowsTheRealNameOrderForEveryResourceWithTheSameNameSet(
    buildResource, javaCode, javaDocs, extractSchema
):
    matching = _resourcesWithTheRealNameSet(buildResource)

    divergent = [resource for resource, real in matching.items()
                 if [name for _, name in real]
                 != [field["name"] for field in _generatedCtorFields(javaCode, javaDocs, extractSchema, resource)]]

    assert divergent == []
    assert set(_constructorSection("javaConstructorOrder")) <= set(matching)


@requiresJavaSdk
def test_everyConstructorTypeMatchesTheParameterTypeOfTheRealConstructor():
    divergent = []
    for resource, types in _constructorSection("javaConstructorTypes").items():
        realTypes = {name: javaType for javaType, name in _realLongestPublicConstructor(resource)}
        divergent += [f"{resource}.{name}: tabela={javaType} real={realTypes.get(name)}"
                      for name, javaType in types.items() if realTypes.get(name) != javaType]

    assert divergent == []


@requiresJavaSdk
def test_generatedConstructorTypesFollowTheRealOnesWhereTheProfileSupportsThem(
    buildResource, javaCode, javaDocs, extractSchema
):
    """`List<Boleto.Description>`, `Resource` e `Object` ficam fora: o perfil nao os le (ME 9.7)."""
    supported = set(javaCode.supportedTypes(javaCode.JAVA_CODE_PROFILE))
    divergent = []
    for resource, real in _resourcesWithTheRealNameSet(buildResource).items():
        generated = {field["name"]: field["javaType"]
                     for field in _generatedCtorFields(javaCode, javaDocs, extractSchema, resource)}
        divergent += [f"{resource}.{name}: gerado={generated[name]} real={javaType}"
                      for javaType, name in real if javaType in supported and generated[name] != javaType]

    assert divergent == []


def test_theConstructorOrderTableIsAlphabeticalWithoutRepeats():
    resources = list(_constructorSection("javaConstructorOrder"))

    assert resources == sorted(set(resources))


def _balanceCode(javaCode, javaDocs, **arguments) -> dict:
    schema, createSchema = _schemaOf("Balance")
    return javaCode.renderJavaCode(schema, javaDocs.describeEntity(schema, createSchema), "Balance", {}, **arguments)


def test_constructorFieldsEqualTheFieldsWhenThereIsNoEntry(javaCode, javaDocs):
    code = _balanceCode(javaCode, javaDocs)

    assert code["ctorFields"] == code["fields"]


def test_constructorOrderReordersOnlyTheConstructorFields(javaCode, javaDocs):
    plain = _balanceCode(javaCode, javaDocs)
    code = _balanceCode(javaCode, javaDocs, ctorOrder=["amount", "currency", "updated", "id"])

    assert [field["name"] for field in code["ctorFields"]] == ["amount", "currency", "updated", "id"]
    assert code["fields"] == plain["fields"]
    assert code["ctorMapBody"] == plain["ctorMapBody"]


def test_constructorTypeReplacesOnlyTheConstructorParameterType(javaCode, javaDocs):
    plain = _balanceCode(javaCode, javaDocs)
    code = _balanceCode(javaCode, javaDocs, ctorTypes={"amount": "int"})

    assert {field["name"]: field["javaType"] for field in code["ctorFields"]}["amount"] == "int"
    assert code["fields"] == plain["fields"]
    assert code["ctorMapBody"] == plain["ctorMapBody"]


@pytest.mark.parametrize("order, message", [
    (["amount", "currency", "id"], r"Balance.*faltam: updated"),
    (["amount", "currency", "updated", "id", "ghost"], r"Balance.*sobram: ghost"),
    (["amount", "currency", "updated", "id", "amount"], r"Balance.*repetidos: amount"),
])
def test_constructorOrderThatIsNotAPermutationFailsNamingTheResourceAndTheField(javaCode, javaDocs, order, message):
    with pytest.raises(ValueError, match=message):
        _balanceCode(javaCode, javaDocs, ctorOrder=order)


@pytest.mark.parametrize("types, message", [
    ({"ghost": "int"}, r"Balance\.ghost"),
    ({"id": "int"}, r"Balance\.id"),
    ({"amount": "Strng"}, r"Balance\.amount.*Strng"),
])
def test_constructorTypeOfAnUnknownIdOrInvalidFieldFailsNamingTheResourceAndTheField(
    javaCode, javaDocs, types, message
):
    with pytest.raises(ValueError, match=message):
        _balanceCode(javaCode, javaDocs, ctorTypes=types)


def test_constructorLoadersReadTheSectionsAndDefaultToNothing(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text(
        "javaConstructorOrder:\n  Balance: [amount, currency,\n    updated, id]\n"
        "javaConstructorTypes:\n  Balance:\n    amount: int\n",
        encoding="utf-8",
    )

    assert extractSchema.javaConstructorOrder("Balance", conventions) == ["amount", "currency", "updated", "id"]
    assert extractSchema.javaConstructorTypes("Balance", conventions) == {"amount": "int"}
    assert extractSchema.javaConstructorOrder("Transfer", conventions) is None
    assert extractSchema.javaConstructorTypes("Transfer", conventions) == {}


def test_constructorLoadersDefaultToNothingWhenTheSectionsAreAbsent(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("javaTypes:\n", encoding="utf-8")

    assert extractSchema.javaConstructorOrder("Balance", conventions) is None
    assert extractSchema.javaConstructorTypes("Balance", conventions) == {}


@pytest.mark.parametrize("section, loader", [
    ("javaConstructorOrder:\n  Balance: [id]\n  Transferr: [id]\n", "javaConstructorOrder"),
    ("javaConstructorTypes:\n  Balance:\n    amount: int\n  Transferr:\n    amount: int\n", "javaConstructorTypes"),
])
def test_unknownResourceInAConstructorSectionFailsNamingIt(extractSchema, tmpPath, section, loader):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text(section, encoding="utf-8")

    with pytest.raises(ValueError, match="Transferr"):
        getattr(extractSchema, loader)("Balance", conventions)


def _allArgsConstructor(source: str, resource: str) -> str:
    return re.search(rf"^    public {resource}\((?!Map<)(?!\)).*?^    \}}\n", source, re.M | re.S).group(0)


def _constructorParameterNames(constructor: str) -> list:
    signature = constructor.split("\n", 1)[0]
    return [pair[1] for pair in _splitParameters(signature[signature.index("(") + 1:signature.rindex(")")])]


def _constructorAssignmentNames(constructor: str) -> list:
    return re.findall(r"^        this\.(\w+) = \w+;$", constructor, re.M)


def _transferFieldNames() -> list:
    return list(_schemaOf("Transfer")[0]["properties"])


@requiresGenerator
def test_orderReordersOnlyTheParametersAndAssignmentsOfTheAllArgsConstructor(buildResource, tmpPath):
    names = _transferFieldNames()
    reversed_ = names[::-1]
    plain = _outer(_generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n"))
    ordered = _outer(_generated(
        buildResource,
        tmpPath / "ordered",
        "Transfer",
        "javaConstructorOrder:\n  Transfer: [" + ", ".join(reversed_) + "]\n",
    ))
    plainConstructor = _allArgsConstructor(plain, "Transfer")
    orderedConstructor = _allArgsConstructor(ordered, "Transfer")

    assert _constructorParameterNames(plainConstructor) == names
    assert _constructorParameterNames(orderedConstructor) == reversed_
    assert _constructorAssignmentNames(orderedConstructor) == [name for name in reversed_ if name != "id"]
    assert ordered.replace(orderedConstructor, "") == plain.replace(plainConstructor, "")


@requiresGenerator
def test_constructorTypeChangesOnlyTheParameterTypeInTheAllArgsConstructor(buildResource, tmpPath):
    plain = _outer(_generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n"))
    typed = _outer(_generated(
        buildResource,
        tmpPath / "typed",
        "Transfer",
        "javaConstructorTypes:\n  Transfer:\n    fee: int\n",
    ))
    plainConstructor = _allArgsConstructor(plain, "Transfer")
    typedConstructor = _allArgsConstructor(typed, "Transfer")

    assert "Integer fee" in plainConstructor
    assert typedConstructor == plainConstructor.replace("Integer fee", "int fee", 1)
    assert typed.replace(typedConstructor, "") == plain.replace(plainConstructor, "")
    assert re.search(r"^    public Integer fee;$", typed, re.M)


@requiresGenerator
def test_entriesOfAnotherResourceLeaveTheGeneratedClassIdentical(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n")
    other = _generated(
        buildResource,
        tmpPath / "other",
        "Transfer",
        "javaConstructorOrder:\n  Balance: [amount, currency, updated, id]\n"
        "javaConstructorTypes:\n  Balance:\n    amount: int\n",
    )

    assert other == plain
