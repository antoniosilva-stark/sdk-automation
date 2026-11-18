import re
import shutil
import subprocess

import yaml
import pytest

from conftest import REFERENCES, REPO_ROOT, _loadTool, requiresGenerator, requiresJavaSdk, requiresJavac

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
    declared = re.findall(r"^    public ([\w<>\[\], .]+?) (\w+);", _outerSource(resource), re.M)
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
        javaCode.renderJavaCode(
            schemas["schemas"][resource],
            entity,
            resource,
            overrides,
            innerClasses=extractSchema.javaInnerClasses(resource),
            resourceNames=extractSchema.schemaResourceNames(),
            otherInnerClasses=extractSchema.javaOtherInnerClasses(resource),
        )


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

    with pytest.raises(ValueError, match="Transferr.*javaOpenClasses"):
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


def _generatedSchemaOf(resource: str) -> tuple:
    """O schema como o Java o declara: sem os campos de javaOmittedFields (o `log` da base do Event) e com os de
    javaExtraFields (o que o real tem e a spec nao)."""
    schema, createSchema = _schemaOf(resource)
    conventions = yaml.safe_load(_realConventionsText())
    omitted = (conventions.get("javaOmittedFields") or {}).get(resource) or []
    kept = {"properties": {name: prop for name, prop in schema["properties"].items() if name not in omitted}}
    extras = (conventions.get("javaExtraFields") or {}).get(resource) or {}
    return _loadTool("java-code.py").addExtraFields({**schema, **kept}, createSchema, resource, extras)


def _generatedCtorFields(javaCode, javaDocs, extractSchema, resource: str) -> list:
    schema, createSchema = _generatedSchemaOf(resource)
    return javaCode.renderJavaCode(
        schema,
        javaDocs.describeEntity(schema, createSchema),
        resource,
        extractSchema.javaTypeOverrides(resource),
        ctorOrder=extractSchema.javaConstructorOrder(resource),
        ctorTypes=extractSchema.javaConstructorTypes(resource),
        innerClasses=extractSchema.javaInnerClasses(resource),
        resourceNames=extractSchema.schemaResourceNames(),
        otherInnerClasses=extractSchema.javaOtherInnerClasses(resource),
    )["ctorFields"]


def _resourcesWithTheRealNameSet(buildResource) -> dict:
    """Recurso -> construtor publico mais longo do real, so quando tem os mesmos nomes que o schema; os demais
    (Transfer...) divergem em nomes e sao da ME 9.7."""
    matching = {}
    for resource in buildResource.loadTool("parity-report").schemaResources():
        if not (REAL_SOURCES / f"{resource}.java").is_file():
            continue
        real = _realLongestPublicConstructor(resource)
        if {name for _, name in real} == set(_generatedSchemaOf(resource)[0]["properties"]):
            matching[resource] = real
    return matching


@requiresJavaSdk
def test_everyConstructorOrderEntryIsTheNameOrderOfTheLongestPublicRealConstructor():
    divergent = [f"{resource}: tabela={order} real={[name for _, name in _realLongestPublicConstructor(resource)]}"
                 for resource, order in _constructorSection("javaConstructorOrder").items()
                 if order != [name for _, name in _realLongestPublicConstructor(resource)]]

    assert divergent == []


def test_everyConstructorOrderEntryHoldsExactlyTheSchemaFieldsTheJavaDeclares():
    for resource, order in _constructorSection("javaConstructorOrder").items():
        assert sorted(order) == sorted(_generatedSchemaOf(resource)[0]["properties"]), resource


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

    with pytest.raises(ValueError, match=f"Transferr.*{loader}"):
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


_RESOURCE_SECTIONS = [
    ("javaOpenClasses", "javaOpenClasses:\n  - {name}\n", "javaOpenClass"),
    ("javaConstructorOrder", "javaConstructorOrder:\n  {name}: [id]\n", "javaConstructorOrder"),
    ("javaConstructorTypes", "javaConstructorTypes:\n  {name}:\n    amount: int\n", "javaConstructorTypes"),
    ("javaInnerClasses", "javaInnerClasses:\n  {name}: {{}}\n", "javaInnerClasses"),
    ("javaLogs", "javaLogs:\n  {name}: {{names: short, errors: none, classData: short}}\n", "javaLogShape"),
    ("javaExtraFields", "javaExtraFields:\n  {name}: {{}}\n", "javaExtraFields"),
]


@pytest.mark.parametrize("name", ["balance", "BALANCE"])
@pytest.mark.parametrize("section, template, loader", _RESOURCE_SECTIONS)
def test_resourceNameInAWrongCaseFailsNamingTheNameAndTheSection(
    extractSchema, tmpPath, name, section, template, loader
):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text(template.format(name=name), encoding="utf-8")

    with pytest.raises(ValueError, match=f"{name}: {section} cita recurso"):
        getattr(extractSchema, loader)("Balance", conventions)


@pytest.mark.parametrize("section, template, loader", _RESOURCE_SECTIONS)
def test_nonStringResourceNameFailsNamingTheValueAndTheSection(extractSchema, tmpPath, section, template, loader):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text(template.format(name="123"), encoding="utf-8")

    with pytest.raises(ValueError, match=f"123: {section} cita recurso"):
        getattr(extractSchema, loader)("Balance", conventions)


def test_listAsResourceNameFailsWithValueErrorToo(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("javaOpenClasses:\n  - [Balance]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="javaOpenClasses cita recurso"):
        extractSchema.javaOpenClass("Balance", conventions)


def test_everyResourceNameOfTheRealTablesIsAccepted(extractSchema):
    sections = _constructorSection

    assert all(extractSchema.javaOpenClass(name) for name in yaml.safe_load(_realConventionsText())["javaOpenClasses"])
    assert all(extractSchema.javaConstructorOrder(name) for name in sections("javaConstructorOrder"))
    assert all(extractSchema.javaConstructorTypes(name) for name in sections("javaConstructorTypes"))
    assert all(extractSchema.javaInnerClasses(name) for name in sections("javaInnerClasses"))


def _walkInner(classes: dict, prefix: str = ""):
    for name, definition in classes.items():
        yield f"{prefix}{name}", definition
        yield from _walkInner(definition.get("inner") or {}, f"{prefix}{name}.")


def _innerEntries() -> list:
    section = _constructorSection("javaInnerClasses")
    return [(resource, path, definition) for resource, classes in section.items()
            for path, definition in _walkInner(classes)]


_INNER_IDS = [f"{resource}.{path}" for resource, path, _ in _innerEntries()]


def _memberIndent(path: str) -> str:
    return " " * (4 * (path.count(".") + 2))


def _balancedBlock(source: str, match) -> str:
    depth, index = 1, match.end()
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[match.start():index]


def _realInnerBlock(resource: str, path: str) -> str | None:
    """A classe interna do real; `Log.User` e a `User` declarada dentro do bloco do `Log`, um nivel mais fundo."""
    block = (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")
    for depth, name in enumerate(path.split(".")):
        pattern = rf"^ {{{4 * (depth + 1)}}}public (?:final )?static (?:final )?class {name}\b[^{{]*\{{"
        match = re.search(pattern, block, re.M)
        if not match:
            return None
        block = _balancedBlock(block, match)
    return block


def _realInnerConstructors(block: str, path: str) -> list:
    name = path.rsplit(".", 1)[-1]
    pattern = rf"^{_memberIndent(path)}public {name}\s*\((.*?)\)\s*(?:throws [\w, ]+)?\{{"
    return [_splitParameters(match.group(1)) for match in re.finditer(pattern, block, re.M | re.S)]


_MAP_PARAMETER = [("Map<String, Object>", "data")]


@requiresJavaSdk
@pytest.mark.parametrize("resource, path, definition", _innerEntries(), ids=_INNER_IDS)
def test_innerClassEntryExistsInTheRealWithTheSameFinalityAndSuperclass(resource, path, definition):
    block = _realInnerBlock(resource, path)

    assert block is not None
    header = block.split("{", 1)[0]
    assert (" final " in f" {header} ") is definition.get("final", True)
    assert re.search(rf"\bextends {definition.get('extends', 'SubResource')}\s*$", header)


@requiresJavaSdk
@pytest.mark.parametrize("resource, path, definition", _innerEntries(), ids=_INNER_IDS)
def test_innerClassEntryHasTheRealFieldsWithTheirTypesInDeclarationOrder(resource, path, definition):
    block = _realInnerBlock(resource, path)

    declared = re.findall(rf"^{_memberIndent(path)}public ([\w<>\[\], .]+?) (\w+);", block, re.M)

    assert [(field, javaType) for javaType, field in declared] == list(definition["fields"].items())


def _withoutInheritedId(definition: dict, parameters: list) -> list:
    """O `id` do ctor de uma classe que estende Resource e repassado a `super(id)` e nao e campo: sem `id` nos nomes da
    entrada ele e o ultimo parametro (PaymentRequest.Log); citado, a entrada fixa a posicao dele (Event.Attempt)."""
    if definition.get("extends") != "Resource" or not parameters:
        return parameters
    assert ("String", "id") in parameters
    if any("id" in names for names in definition["constructors"]):
        return parameters
    assert parameters[-1] == ("String", "id")
    return parameters[:-1]


@requiresJavaSdk
@pytest.mark.parametrize("resource, path, definition", _innerEntries(), ids=_INNER_IDS)
def test_innerClassEntryHasTheRealPositionalConstructorsWithFieldNamesAndTypesInOrder(resource, path, definition):
    block = _realInnerBlock(resource, path)
    positional = [parameters for parameters in _realInnerConstructors(block, path) if parameters != _MAP_PARAMETER]

    real = sorted([field for _, field in _withoutInheritedId(definition, parameters)] for parameters in positional)
    assert real == sorted(definition["constructors"])
    for parameters in positional:
        typed = {**definition["fields"], "id": "String"}
        assert all(javaType == typed[field] for javaType, field in _withoutInheritedId(definition, parameters))


@requiresJavaSdk
@pytest.mark.parametrize("resource, path, definition", _innerEntries(), ids=_INNER_IDS)
def test_innerClassEntryHasTheMapConstructorOnlyWhenTheRealDoesAndReadsTheSameFields(resource, path, definition):
    block = _realInnerBlock(resource, path)
    hasMapConstructor = _MAP_PARAMETER in _realInnerConstructors(block, path)

    assert hasMapConstructor is ("map" in definition)
    reads = re.findall(r'this\.(\w+) = .*dataCopy\.remove\("\1"\)', block)
    assert reads == definition.get("map", [])


@requiresJavaSdk
@pytest.mark.parametrize("resource, path, definition", _innerEntries(), ids=_INNER_IDS)
def test_innerClassEntryDeclaresTheClassDataOnlyWhenTheRealDoes(resource, path, definition):
    block = _realInnerBlock(resource, path)
    name = path.rsplit(".", 1)[-1]

    real = re.search(rf'static (?:SubResource\.)?ClassData data = new (?:SubResource\.)?ClassData\({name}\.class, "(\w+)"\)',
                     block)

    assert (real.group(1) if real else None) == definition.get("classData")


@requiresJavaSdk
@pytest.mark.parametrize("resource, path, definition", _innerEntries(), ids=_INNER_IDS)
def test_innerClassEntryWritesTheClassDataTypeTheRealWrites(resource, path, definition):
    block = _realInnerBlock(resource, path)

    real = re.search(r"static (SubResource\.)?ClassData data = new (SubResource\.)?ClassData\(", block)

    assert (real is not None) is ("classData" in definition)
    if real:
        assert real.group(1) == real.group(2)
        assert ("qualified" if real.group(1) else "short") == definition["classDataType"]


_NOT_SIMPLE_INNER = {("MerchantSession", "Purchase"), ("PaymentRequest", "Deserializer")}
_NOT_SIMPLE_FILES = {"Event"}
# `Page` o template emite; o `Deserializer` aninhado (Transfer.Rule) e Gson manual, dispensado em apis/java-handwritten.yaml
_NOT_SIMPLE_NESTED = {"Page", "Deserializer"}
# o Log de PaymentRequest nao tem get/query/page: nao e um javaLogs, e sim uma classe interna que estende Resource
_LOG_AS_INNER = {("PaymentRequest", "Log")}
# o Event inteiro fica fora (subclasses e Deserializer), mas o Attempt e uma classe interna comum com as operacoes que o gerador imprime
_ATTEMPT_AS_INNER = {("Event", "Attempt")}


def _realInnerClasses() -> set:
    """Os caminhos (`Recurso`, `Log.User`) das classes internas simples do real, aninhadas inclusive."""
    declaration = re.compile(r"^    public (?:final )?static (?:final )?class (\w+)", re.M)
    nested = re.compile(r"^        public (?:final )?static (?:final )?class (\w+)", re.M)
    found = set()
    for path in sorted(REAL_SOURCES.glob("*.java")):
        if path.stem in _NOT_SIMPLE_FILES:
            continue
        source = path.read_text(encoding="utf-8")
        for name in declaration.findall(source):
            if (path.stem, name) in _NOT_SIMPLE_INNER or name == "Page" or (name == "Log" and (path.stem, name) not in _LOG_AS_INNER):
                continue
            found.add((path.stem, name))
            found.update((path.stem, f"{name}.{inner}") for inner in nested.findall(_realInnerBlock(path.stem, name))
                         if inner not in _NOT_SIMPLE_NESTED)
    return found | {pair for pair in _ATTEMPT_AS_INNER if _realInnerBlock(*pair)}


@requiresJavaSdk
def test_innerClassSectionHoldsExactlyTheSimpleInnerClassesOfTheRealSdk():
    section = {(resource, path) for resource, path, _ in _innerEntries()}

    assert sorted(_realInnerClasses() - section) == []
    assert sorted(section - _realInnerClasses()) == []


def _isAlphabetical(classes: dict) -> bool:
    return list(classes) == sorted(classes) and all(_isAlphabetical(item.get("inner") or {}) for item in classes.values())


def test_innerClassSectionIsAlphabeticalByResourceAndByClass():
    section = _constructorSection("javaInnerClasses")

    assert list(section) == sorted(section)
    assert all(_isAlphabetical(classes) for classes in section.values())


_SYNTHETIC_INNER = """javaInnerClasses:
  Transfer:
    Pair:
      fields: {key: String, amount: Integer, tags: "String[]"}
      constructors: [[key], [key, amount, tags], []]
      map: [key, tags]
      classData: Pairs
      classDataType: qualified
    Slot:
      fields: {size: long}
      constructors: [[size]]
      final: false
"""


@requiresGenerator
def test_innerClassIsEmittedInsideTheOuterClassAfterTheLogWithItsConstructors(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", _SYNTHETIC_INNER)

    assert source.index("public final static class Log") < source.index("public final static class Pair extends SubResource {")
    assert source.rstrip().endswith("    }\n}")
    assert "        public String key;\n        public Integer amount;\n        public String[] tags;\n" in source
    assert "        public Pair(String key) {\n            this.key = key;\n            this.amount = null;\n" in source
    assert "        public Pair(String key, Integer amount, String[] tags) {\n" in source
    assert "        public Pair() {\n        }\n" in source


@requiresGenerator
def test_innerClassMapConstructorReadsOnlyTheListedFieldsAndRejectsTheRest(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", _SYNTHETIC_INNER)

    assert ("        public Pair(Map<String, Object> data) throws Exception {\n"
            "            HashMap<String, Object> dataCopy = new HashMap<>(data);\n") in source
    assert '            this.key = (String) dataCopy.remove("key");\n' in source
    assert "            this.amount = null;\n" in source
    assert '            this.tags = (String[]) dataCopy.remove("tags");\n' in source
    assert 'throw new Exception("Unknown parameters used in constructor: [" + String.join' in source.split("class Pair")[1]


@requiresGenerator
def test_innerClassFinalFalseEmitsAnOpenClassWithPrimitiveFieldAndNoClassData(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", _SYNTHETIC_INNER)

    assert "    public static class Slot extends SubResource {\n" in source
    assert "final static class Slot" not in source
    assert "        public long size;\n" in source
    assert "        public Slot(long size) {\n            this.size = size;\n        }\n" in source
    assert source.split("class Slot")[1].count("ClassData") == 0


@requiresGenerator
def test_innerClassDataIsEmittedOnlyWhenDeclared(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", _SYNTHETIC_INNER)
    pair = source.split("class Pair")[1].split("class Slot")[0]

    assert '        static SubResource.ClassData data = new SubResource.ClassData(Pair.class, "Pairs");\n' in pair


@requiresGenerator
def test_innerClassDataTypeShortEmitsTheUnqualifiedLine(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", _SYNTHETIC_INNER.replace("classDataType: qualified", "classDataType: short"))
    pair = source.split("class Pair")[1].split("class Slot")[0]

    assert '        static ClassData data = new ClassData(Pair.class, "Pairs");\n' in pair
    assert "SubResource.ClassData" not in pair


_SYNTHETIC_NESTED = """javaInnerClasses:
  Balance:
    Log:
      extends: Resource
      fields: {created: String, errors: "String[]", balance: Balance, user: Balance.Log.User}
      constructors: [[created, errors, balance, user], []]
      classData: BalanceLog
      classDataType: short
      inner:
        User:
          fields: {email: String, id: String}
          constructors: [[email, id], []]
"""


@requiresGenerator
def test_innerClassThatExtendsResourceAndNestsAnotherIsEmittedInTheShapeOfTheRealLog(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Balance", _SYNTHETIC_NESTED)

    assert source.rstrip().endswith("""
    public final static class Log extends Resource {
        public String created;
        public String[] errors;
        public Balance balance;
        public Balance.Log.User user;

        static ClassData data = new ClassData(Log.class, "BalanceLog");

        public Log(String created, String[] errors, Balance balance, Balance.Log.User user, String id) {
            super(id);
            this.created = created;
            this.errors = errors;
            this.balance = balance;
            this.user = user;
        }

        public Log() {
            super(null);
        }

        public final static class User extends SubResource {
            public String email;
            public String id;

            public User(String email, String id) {
                this.email = email;
                this.id = id;
            }

            public User() {
            }
        }
    }
}""")


@requiresGenerator
def test_resourceWithoutInnerEntryIsGeneratedIdenticallyWhateverOtherResourcesDeclare(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n")
    other = _generated(buildResource, tmpPath / "other", "Transfer", _SYNTHETIC_INNER.replace("Transfer:", "Balance:"))

    assert other == plain
    assert "static class Pair" not in plain


@requiresGenerator
@requiresJavac
@pytest.mark.parametrize("resource, inner", [
    ("Boleto", ["Description", "Discount"]),
    ("Invoice", ["Description", "Discount", "Payment", "Rule"]),
    ("MerchantSession", ["AllowedInstallment"]),
])
def test_resourceWithInnerClassesPassesTheJavacGate(builtOnce, resource, inner):
    code, out, target = builtOnce(resource, "java")

    assert code == 0, out
    assert "sintaxe (javac)" in out
    source = (target / f"src/main/java/com/starkbank/{resource}.java").read_text(encoding="utf-8")
    assert all(re.search(rf"static class {name} extends SubResource \{{", source) for name in inner)


def _balanceInner(javaCode, javaDocs, innerClasses):
    schemas = yaml.safe_load((SCHEMAS / "balance.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    entity = javaDocs.describeEntity(schemas["Balance"], schemas["BalanceCreate"])
    return javaCode.renderJavaCode(schemas["Balance"], entity, "Balance", {}, innerClasses=innerClasses)["innerClasses"]


def test_renderJavaCodeCarriesNoInnerClassWithoutEntry(javaCode, javaDocs):
    assert _balanceInner(javaCode, javaDocs, None) == []
    assert _balanceInner(javaCode, javaDocs, {}) == []


def test_renderJavaCodeRendersInnerClassConstructorsWithAssignmentsAndNulls(javaCode, javaDocs):
    rendered = _balanceInner(javaCode, javaDocs, {"Pair": {
        "fields": {"key": "String", "amount": "Integer"},
        "constructors": [["key"], []],
    }})

    assert rendered == [{
        "className": "Pair",
        "superclass": "SubResource",
        "isFinal": True,
        "fields": [{"name": "key", "javaType": "String"}, {"name": "amount", "javaType": "Integer"}],
        "constructors": [
            {"signature": "String key", "body": ["this.key = key;", "this.amount = null;"]},
            {"signature": "", "body": []},
        ],
        "inner": [],
    }]


_PAIR = {"fields": {"key": "String", "amount": "Integer"}, "constructors": [["key", "amount"]]}


@pytest.mark.parametrize("innerClasses, message", [
    ({"Pair": {**_PAIR, "constructors": [["key", "nope"]]}}, "Balance.Pair: construtor cita campo que a classe nao tem: nope"),
    ({"Pair": {**_PAIR, "map": ["nope"]}}, "Balance.Pair: map cita campo que a classe nao tem: nope"),
    ({"Pair": {**_PAIR, "fields": {"key": "Foo", "amount": "Integer"}}}, "Balance.Pair.key: tipo de override 'Foo'"),
    ({"Pair": {**_PAIR, "constructors": [["key", "key"]]}}, "Balance.Pair: construtor repete campo: key"),
    ({"Pair": {**_PAIR, "constructors": [["key"], ["key"]]}}, "Balance.Pair: dois construtores com a mesma assinatura"),
    ({"Pair": {**_PAIR, "constructors": [["key"], ["amount"]], "fields": {"key": "String", "amount": "String"}}},
     "Balance.Pair: dois construtores com a mesma assinatura"),
    ({"Pair": {**_PAIR, "constructors": [["key"]], "fields": {"key": "String", "size": "long"}}},
     "Balance.Pair: construtor omite o primitivo size"),
    ({"Pair": {**_PAIR, "constructors": [["key", "size"]], "fields": {"key": "String", "size": "long"}, "map": ["key"]}},
     "Balance.Pair: map omite o primitivo size"),
    ({"Pair": {**_PAIR, "map": ["key", "key"]}}, "Balance.Pair: map repete campo: key"),
    ({"Pair": {**_PAIR, "final": "no"}}, "Balance.Pair: final deve ser booleano"),
    ({"Pair": {**_PAIR, "classData": "bad name", "classDataType": "short"}}, "Balance.Pair: classData invalido: 'bad name'"),
    ({"Pair": {**_PAIR, "classData": "Pairs"}}, "Balance.Pair: classDataType ausente"),
    ({"Pair": {**_PAIR, "classData": "Pairs", "classDataType": "full"}},
     "Balance.Pair: classDataType invalido: 'full', esperado um de ['short', 'qualified']"),
    ({"Pair": {**_PAIR, "classDataType": "short"}}, "Balance.Pair: classDataType sem classData"),
    ({"Pair": {**_PAIR, "extra": 1}}, "Balance.Pair: chave desconhecida: extra"),
    ({"Pair": {"constructors": [["key"]]}}, "Balance.Pair: fields ausente ou vazio"),
    ({"pair": _PAIR}, "Balance.pair: nome de classe interna invalido"),
    ({"Page": _PAIR}, "Balance.Page: nome de classe interna que o template ja emite"),
    ({"Balance": _PAIR}, "Balance.Balance: nome de classe interna igual ao da classe externa"),
])
def test_invalidInnerClassFailsNamingTheResourceTheClassAndTheProblem(javaCode, javaDocs, innerClasses, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        _balanceInner(javaCode, javaDocs, innerClasses)


_LOG = {
    "extends": "Resource",
    "fields": {"created": "String", "balance": "Balance", "user": "Balance.Log.User"},
    "constructors": [["created", "balance", "user"], []],
    "classData": "BalanceLog",
    "classDataType": "short",
    "inner": {
        "User": {"fields": {"email": "String", "id": "String"}, "constructors": [["email", "id"], []]},
    },
}


def test_innerClassThatExtendsResourceTakesTheIdLastAndPassesItToSuper(javaCode, javaDocs):
    [log] = _balanceInner(javaCode, javaDocs, {"Log": _LOG})

    assert log["superclass"] == "Resource"
    assert log["constructors"] == [
        {"signature": "String created, Balance balance, Balance.Log.User user, String id",
         "body": ["super(id);", "this.created = created;", "this.balance = balance;", "this.user = user;"]},
        {"signature": "", "body": ["super(null);"]},
    ]


def test_innerClassWithoutExtendsKeepsSubResourceAndHasAnEmptyNestedList(javaCode, javaDocs):
    [pair] = _balanceInner(javaCode, javaDocs, {"Pair": _PAIR})

    assert pair["superclass"] == "SubResource"
    assert pair["inner"] == []


def test_nestedInnerClassIsRenderedWithTheSameShapeAsTheOuterOne(javaCode, javaDocs):
    [log] = _balanceInner(javaCode, javaDocs, {"Log": _LOG})

    assert log["inner"] == [{
        "className": "User",
        "superclass": "SubResource",
        "isFinal": True,
        "fields": [{"name": "email", "javaType": "String"}, {"name": "id", "javaType": "String"}],
        "constructors": [
            {"signature": "String email, String id", "body": ["this.email = email;", "this.id = id;"]},
            {"signature": "", "body": []},
        ],
        "inner": [],
    }]


def test_nestedInnerClassCanItselfNestAnotherOne(javaCode, javaDocs):
    deeper = {"Entry": {**_PAIR, "inner": {"Mid": {**_PAIR, "inner": {"Leaf": _PAIR}}}}}

    [entry] = _balanceInner(javaCode, javaDocs, deeper)

    assert entry["inner"][0]["className"] == "Mid"
    assert entry["inner"][0]["inner"][0]["className"] == "Leaf"


def test_aFieldOfAnInnerClassMayBeTheQualifiedTypeOfItsNestedClass(javaCode, javaDocs):
    [entry] = _balanceInner(javaCode, javaDocs, {"Entry": {
        **_PAIR, "fields": {"key": "String", "user": "Balance.Entry.User"}, "constructors": [["key", "user"]],
        "inner": {"User": _PAIR},
    }})

    assert entry["fields"][1] == {"name": "user", "javaType": "Balance.Entry.User"}


@pytest.mark.parametrize("innerClasses, message", [
    ({"Entry": {**_PAIR, "fields": {"key": "String", "user": "Balance.Ghost"}}},
     "Balance.Entry.user: tipo de override 'Balance.Ghost' que o Map-ctor nao le"),
    ({"Entry": {**_PAIR, "fields": {"key": "String", "user": "Other"}}},
     "Balance.Entry.user: tipo de override 'Other' que o Map-ctor nao le"),
    ({"Entry": {"fields": {"key": "String", "user": "Balance"}, "constructors": [["key", "user"]], "map": ["key", "user"]}},
     "Balance.Entry: map cita campo de tipo 'Balance', que o Map-ctor nao le: user"),
])
def test_aFieldTypeOutsideTheProfileMustBeTheOuterResourceOrAnInnerClassOfItAndNeverInTheMap(
    javaCode, javaDocs, innerClasses, message
):
    with pytest.raises(ValueError, match=re.escape(message)):
        _balanceInner(javaCode, javaDocs, innerClasses)


@pytest.mark.parametrize("innerClasses, message", [
    ({"Pair": {**_PAIR, "extends": "Object"}}, "Balance.Pair: extends invalido: 'Object', esperado um de ['SubResource', 'Resource']"),
    ({"Pair": {**_PAIR, "extends": ["Resource"]}}, "Balance.Pair: extends invalido: ['Resource'], esperado um de"),
    ({"Pair": {"fields": {"id": "String"}, "constructors": [["id"]], "extends": "Resource"}},
     "Balance.Pair: classe que estende Resource herda o campo id e nao pode declara-lo"),
    ({"Pair": {**_PAIR, "extends": "Resource", "map": ["key"]}},
     "Balance.Pair: map nao e suportado em classe que estende Resource"),
    ({"Pair": {**_PAIR, "inner": []}}, "Balance.Pair: inner deve ser um mapa de classes"),
    ({"Pair": {**_PAIR, "inner": {"user": _PAIR}}}, "Balance.Pair.user: nome de classe interna invalido"),
    ({"Pair": {**_PAIR, "inner": {"User": {**_PAIR, "extra": 1}}}}, "Balance.Pair.User: chave desconhecida: extra"),
    ({"Pair": {**_PAIR, "inner": {"User": {"constructors": [["key"]]}}}}, "Balance.Pair.User: fields ausente ou vazio"),
    ({"Pair": {**_PAIR, "inner": {"User": {**_PAIR, "constructors": [["nope"]]}}}},
     "Balance.Pair.User: construtor cita campo que a classe nao tem: nope"),
    ({"Pair": {**_PAIR, "inner": {"Pair": _PAIR}}}, "Balance.Pair.Pair: nome de classe interna repetido"),
    ({"Pair": {**_PAIR, "inner": {"Balance": _PAIR}}}, "Balance.Pair.Balance: nome de classe interna igual ao da classe externa"),
    ({"Pair": {**_PAIR, "inner": {"Page": _PAIR}}}, "Balance.Pair.Page: nome de classe interna que o template ja emite"),
    ({"Pair": {**_PAIR, "inner": {"Slot": _PAIR}}, "Slot": _PAIR}, "Balance.Slot: nome de classe interna repetido"),
    ({"Pair": {**_PAIR, "inner": {"Slot": {**_PAIR, "inner": {"Slot": _PAIR}}}}},
     "Balance.Pair.Slot.Slot: nome de classe interna repetido"),
])
def test_invalidExtendsOrNestedInnerClassFailsNamingResourceClassKeyAndValue(javaCode, javaDocs, innerClasses, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        _balanceInner(javaCode, javaDocs, innerClasses)


@pytest.mark.parametrize("definition", [
    {"extends": "Resource", "fields": {"a": "String"}},
    {"extends": "Resource", "fields": {"a": "String"}, "constructors": []},
    {"extends": "Resource", "fields": {"a": "String"}, "constructors": None},
])
def test_classThatExtendsResourceWithoutAConstructorFailsSinceTheJavacWouldInjectAnAbsentSuper(
    javaCode, javaDocs, definition
):
    with pytest.raises(ValueError, match=re.escape("Balance.Log: extends Resource exige ao menos um construtor em constructors")):
        _balanceInner(javaCode, javaDocs, {"Log": definition})


def test_aListOfAClassThatExtendsResourceFailsSinceTheItemParserWouldNotPassTheId(javaCode, javaDocs):
    schemas = yaml.safe_load((SCHEMAS / "balance.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    entity = javaDocs.describeEntity(schemas["Balance"], schemas["BalanceCreate"])
    inner = {"Entry": {"fields": {"key": "String"}, "constructors": [["key"]], "extends": "Resource"}}

    with pytest.raises(ValueError, match=re.escape("Balance.amount: classe interna 'Entry' estende Resource")):
        javaCode.renderJavaCode(schemas["Balance"], entity, "Balance", {"amount": "List<Entry>"}, innerClasses=inner)


def test_aClassNamedLogIsRejectedOnlyWhereTheTemplateEmitsTheLogOfTheResource(javaCode, javaDocs):
    schemas = yaml.safe_load((SCHEMAS / "balance.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    entity = javaDocs.describeEntity(schemas["Balance"], schemas["BalanceCreate"])
    shape = {"names": "short", "errors": "none", "classData": "short"}

    with pytest.raises(ValueError, match=re.escape("Balance.Log: nome de classe interna que o template ja emite")):
        javaCode.renderJavaCode(schemas["Balance"], entity, "Balance", {}, innerClasses={"Log": _PAIR},
                                logShape=shape, logEntity="balance")
    assert _balanceInner(javaCode, javaDocs, {"Log": _PAIR})[0]["className"] == "Log"


def test_innerClassWithAnAcceptedTypeFromTheProfileIsRendered(javaCode, javaDocs):
    fields = {"a": "Number", "b": "long", "c": "int", "d": "String[]", "e": "Map<String, Object>"}
    rendered = _balanceInner(javaCode, javaDocs, {"Pair": {"fields": fields, "constructors": [list(fields)], "map": list(fields)}})

    assert rendered[0]["map"]["suppress"] is True
    assert 'this.b = ((Number) dataCopy.remove("b")).longValue();' in rendered[0]["map"]["body"]


def test_innerClassLoaderReturnsTheClassesOfTheResourceAndNothingForTheOthers(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("javaInnerClasses:\n  Balance:\n    Pair:\n      fields: {key: String}\n", encoding="utf-8")

    assert extractSchema.javaInnerClasses("Balance", conventions) == {"Pair": {"fields": {"key": "String"}}}
    assert extractSchema.javaInnerClasses("Transfer", conventions) == {}


def test_innerClassLoaderDefaultsToNothingWhenTheSectionIsAbsent(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("javaTypes:\n", encoding="utf-8")

    assert extractSchema.javaInnerClasses("Balance", conventions) == {}


def _isSubObjectType(resource: str, javaType: str, innerByResource: dict, resources: set) -> bool:
    """`Resource`, `List<InternaDoRecurso>` (qualificada ou nao), `List<Outro.Interna>` ou `List<OutroRecurso>`: o que o
    gerador nao deriva do schema, que so diz `array` de `object`."""
    if javaType == "Resource":
        return True
    match = re.fullmatch(r"List<(?:(\w+)\.)?(\w+)>", javaType)
    if not match:
        return False
    qualifier, bare = match.groups()
    if bare in innerByResource.get(qualifier or resource, {}):
        return True
    return qualifier is None and bare in resources


@requiresJavaSdk
def test_everyRealOuterSubObjectListAndResourceFieldOfAGeneratedResourceHasItsOverride(buildResource):
    resources = set(buildResource.loadTool("parity-report").schemaResources())
    innerByResource = _constructorSection("javaInnerClasses")
    table = yaml.safe_load(_realConventionsText())["javaTypes"]

    missing = [f"{resource}.{name}: real={javaType} tabela={table.get(resource, {}).get(name)}"
               for resource in sorted(resources) if (REAL_SOURCES / f"{resource}.java").is_file()
               for name, javaType in _realOuterFields(resource).items()
               if _isSubObjectType(resource, javaType, innerByResource, resources)
               and table.get(resource, {}).get(name) != javaType]

    assert missing == []


@requiresJavaSdk
def test_theSubObjectOverridesAreTheOnesTheRealSdkDeclares(buildResource):
    resources = set(buildResource.loadTool("parity-report").schemaResources())
    innerByResource = _constructorSection("javaInnerClasses")
    table = yaml.safe_load(_realConventionsText())["javaTypes"]

    declared = {f"{resource}.{name}": javaType for resource, fields in table.items()
                for name, javaType in fields.items()
                if _isSubObjectType(resource, javaType, innerByResource, resources)}

    assert declared == {
        "Boleto.descriptions": "List<Boleto.Description>",
        "Boleto.discounts": "List<Boleto.Discount>",
        "BrcodePayment.rules": "List<BrcodePayment.Rule>",
        "CorporateCard.rules": "List<CorporateRule>",
        "CorporateHolder.permissions": "List<Permission>",
        "CorporateHolder.rules": "List<CorporateRule>",
        "CorporateRule.categories": "List<MerchantCategory>",
        "CorporateRule.countries": "List<MerchantCountry>",
        "CorporateRule.methods": "List<CardMethod>",
        "DynamicBrcode.rules": "List<DynamicBrcode.Rule>",
        "Invoice.descriptions": "List<Invoice.Description>",
        "Invoice.discounts": "List<Invoice.Discount>",
        "Invoice.rules": "List<Invoice.Rule>",
        "Invoice.splits": "List<Split>",
        "MerchantSession.allowedInstallments": "List<AllowedInstallment>",
        "PaymentRequest.actions": "List<PaymentRequest.Action>",
        "PaymentRequest.payment": "Resource",
        "Transfer.rules": "List<Rule>",
        "VerifiedTransfer.rules": "List<Transfer.Rule>",
    }


_SYNTHETIC_LIST = """javaInnerClasses:
  Transfer:
    Rule:
      fields: {key: String, value: Number}
      constructors: [[key, value], []]
      map: [key, value]
javaTypes:
  Transfer:
    rules: List<Transfer.Rule>
"""


@requiresGenerator
def test_listOfInnerClassesTypesTheFieldTheAllArgsConstructorAndTheMapCall(buildResource, tmpPath):
    source = _outer(_generated(buildResource, tmpPath, "Transfer", _SYNTHETIC_LIST))

    assert re.search(r"^    public List<Transfer.Rule> rules;$", source, re.M)
    assert re.search(r"public Transfer\(.*\bList<Transfer.Rule> rules\b", source)
    assert '        this.rules = parseRules((List<Object>) dataCopy.remove("rules"));\n' in source
    assert "List<Object> rules" not in source
    assert '@SuppressWarnings("unchecked")\n    public Transfer(Map<String, Object> data)' in source


@requiresGenerator
def test_theParseHelperIsEmittedRightAfterTheNoArgConstructor(buildResource, tmpPath):
    source = _outer(_generated(buildResource, tmpPath, "Transfer", _SYNTHETIC_LIST))

    helper = ('    @SuppressWarnings("unchecked")\n'
              "    private static List<Transfer.Rule> parseRules(List<Object> items) throws Exception {\n"
              "        if (items == null) {\n"
              "            return null;\n"
              "        }\n")
    assert source.count("private static List<Transfer.Rule> parseRules") == 1
    assert helper in source
    assert source.index("    public Transfer() {") < source.index(helper)
    assert "        parsed.add(new Transfer.Rule((Map<String, Object>) item));\n" in source


@requiresGenerator
def test_aListOverrideOfAnotherResourceLeavesTheGeneratedClassIdentical(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n")
    other = _generated(
        buildResource,
        tmpPath / "other",
        "Transfer",
        "javaInnerClasses:\n  Invoice:\n    Rule:\n      fields: {key: String}\n      constructors: [[key]]\n"
        "javaTypes:\n  Invoice:\n    rules: List<Invoice.Rule>\n",
    )

    assert other == plain
    assert "parseRules" not in plain


@requiresGenerator
def test_aListOverrideThatNamesAnUnknownElementFailsTheStaging(buildResource, tmpPath):
    with pytest.raises(ValueError, match="Transfer.rules: elemento 'Foo' de List nao e classe interna"):
        _generated(buildResource, tmpPath, "Transfer", "javaTypes:\n  Transfer:\n    rules: List<Foo>\n")


@requiresGenerator
def test_aResourceWithParseHelpersButNoListOperationImportsArrayList(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "CorporateRule", "javaTypes:\n")
    typed = _generated(buildResource, tmpPath / "typed", "CorporateRule", _realConventionsText())

    assert "ArrayList" not in plain
    assert "import java.util.ArrayList;" in typed
    assert "parseCategories" in typed


_LIST_RUNTIME_MAIN = r"""
import com.starkbank.Boleto;
import com.starkbank.BrcodePayment;
import com.starkbank.Invoice;
import com.starkbank.MerchantSession;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class Main {
    static Map<String, Object> map(Object... pairs) {
        Map<String, Object> result = new HashMap<>();
        for (int index = 0; index < pairs.length; index += 2) {
            result.put((String) pairs[index], pairs[index + 1]);
        }
        return result;
    }

    static List<Object> list(Object... items) {
        return new ArrayList<>(Arrays.asList(items));
    }

    interface Action {
        void run() throws Exception;
    }

    static void expectFailure(String label, Action action) {
        try {
            action.run();
            System.out.println(label + " -> accepted");
        } catch (Exception exception) {
            System.out.println(label + " -> " + exception.getMessage());
        }
    }

    public static void main(String[] args) throws Exception {
        Boleto boleto = new Boleto(map(
            "amount", 100,
            "discounts", list(map("date", "2020-03-12", "percentage", 2.5)),
            "descriptions", list(map("text", "Taxes", "amount", 120L), map("text", "Only"))
        ));
        System.out.println("boleto discount -> " + boleto.discounts.get(0).getClass().getName() + " "
            + boleto.discounts.get(0).date + " " + boleto.discounts.get(0).percentage);
        System.out.println("boleto description 0 -> " + boleto.descriptions.get(0).text + " " + boleto.descriptions.get(0).amount);
        System.out.println("boleto description 1 -> " + boleto.descriptions.get(1).text + " " + boleto.descriptions.get(1).amount);

        Boleto.Discount original = new Boleto.Discount("2020-01-01", 1.5);
        Boleto kept = new Boleto(map("amount", 1, "discounts", list(original)));
        System.out.println("boleto instance kept -> " + (kept.discounts.get(0) == original));
        System.out.println("boleto empty list -> " + new Boleto(map("amount", 1, "discounts", list())).discounts.size());
        System.out.println("boleto null list -> " + new Boleto(map("amount", 1)).discounts);
        expectFailure("boleto invalid item", () -> new Boleto(map("amount", 1, "discounts", list("text"))));
        expectFailure("boleto unknown key", () -> new Boleto(map("amount", 1, "nope", 1)));

        BrcodePayment brcode = new BrcodePayment(map("rules", list(map("key", "resendingLimit", "value", 5))));
        System.out.println("brcode rule -> " + brcode.rules.get(0).getClass().getName() + " " + brcode.rules.get(0).key
            + " " + brcode.rules.get(0).value);
        expectFailure("brcode rule unknown key", () -> new BrcodePayment(map("rules", list(map("key", "a", "nope", 1)))));

        Invoice invoice = new Invoice(map(
            "amount", 100,
            "discounts", list(map("percentage", 2.5, "due", "2020-11-25")),
            "rules", list(map("key", "allowedTaxIds", "value", new String[]{"012.345.678-90"})),
            "splits", list(map("amount", 5L, "receiverId", "1234"))
        ));
        System.out.println("invoice discount -> " + invoice.discounts.get(0).getClass().getName() + " "
            + invoice.discounts.get(0).percentage + " " + invoice.discounts.get(0).due);
        System.out.println("invoice rule -> " + invoice.rules.get(0).getClass().getName() + " " + invoice.rules.get(0).key
            + " " + invoice.rules.get(0).value[0]);
        System.out.println("invoice split -> " + invoice.splits.get(0).getClass().getName() + " " + invoice.splits.get(0).receiverId);

        MerchantSession session = new MerchantSession(map(
            "allowedInstallments", list(map("totalAmount", 1000, "count", 2), map("totalAmount", 3000L, "count", 3))
        ));
        System.out.println("session installment 0 -> " + session.allowedInstallments.get(0).totalAmount + " "
            + session.allowedInstallments.get(0).count);
        System.out.println("session installment 1 -> " + session.allowedInstallments.get(1).totalAmount + " "
            + session.allowedInstallments.get(1).count);
    }
}"""

_LIST_RUNTIME_RESOURCES = ("Boleto", "Invoice", "BrcodePayment", "MerchantSession")


@pytest.fixture(scope="module")
def listRun(tmp_path_factory, builtOnce, javaRuntimeClasspath):
    work = tmp_path_factory.mktemp("list-runtime")
    sources = []
    for resource in _LIST_RUNTIME_RESOURCES:
        code, out, target = builtOnce(resource, "java")
        assert code == 0, out
        copy = work / "src/com/starkbank" / f"{resource}.java"
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(target / "src/main/java/com/starkbank" / f"{resource}.java", copy)
        sources.append(str(copy))
    (work / "src/Main.java").write_text(_LIST_RUNTIME_MAIN, encoding="utf-8")

    # os gerados vencem o do real no -sourcepath; Split e o resto do real so entram se alguem os referencia
    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath",
         str(REFERENCES["java"] / "src/main/java"), *sources, str(work / "src/Main.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run(["java", "-cp", f"{work / 'out'}:{javaRuntimeClasspath}", "Main"], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    return ran.stdout.splitlines()


@requiresGenerator
@requiresJavac
def test_aListOfMapsBecomesTheInnerObjectsWithTheirValues(listRun):
    assert "boleto discount -> com.starkbank.Boleto$Discount 2020-03-12 2.5" in listRun
    assert "boleto description 0 -> Taxes 120" in listRun
    assert "boleto description 1 -> Only null" in listRun


@requiresGenerator
@requiresJavac
def test_aListThatAlreadyHoldsInstancesPassesIntactAndNullStaysNull(listRun):
    assert "boleto instance kept -> true" in listRun
    assert "boleto empty list -> 0" in listRun
    assert "boleto null list -> null" in listRun


@requiresGenerator
@requiresJavac
def test_anItemOfAnInvalidTypeFailsLikeTheReal(listRun):
    assert 'boleto invalid item -> Unknown type "class java.lang.String", use Boleto.Discount or HashMap' in listRun


@requiresGenerator
@requiresJavac
def test_anUnknownKeyOfTheOuterMapStillFails(listRun):
    assert "boleto unknown key -> Unknown parameters used in constructor: [nope]" in listRun


@requiresGenerator
@requiresJavac
def test_anInnerClassWithAMapConstructorIsBuiltByItAndKeepsItsUnknownKeyCheck(listRun):
    assert "brcode rule -> com.starkbank.BrcodePayment$Rule resendingLimit 5" in listRun
    assert "brcode rule unknown key -> Unknown parameters used in constructor: [nope]" in listRun


@requiresGenerator
@requiresJavac
def test_anInnerClassWithoutAMapConstructorIsBuiltByItsPositionalOne(listRun):
    assert "invoice discount -> com.starkbank.Invoice$Discount 2.5 2020-11-25" in listRun
    assert "invoice rule -> com.starkbank.Invoice$Rule allowedTaxIds 012.345.678-90" in listRun


@requiresGenerator
@requiresJavac
def test_aListOfAGeneratedResourceIsBuiltByItsMapConstructor(listRun):
    assert "invoice split -> com.starkbank.Split 1234" in listRun


@requiresGenerator
@requiresJavac
def test_primitiveFieldsOfAnInnerClassAreReadAsNumberWhateverTheNumericType(listRun):
    assert "session installment 0 -> 1000 2" in listRun
    assert "session installment 1 -> 3000 3" in listRun


@requiresGenerator
@requiresJavac
@pytest.mark.parametrize("resource, expected", [
    ("Invoice", 'static ClassData data = new ClassData(Payment.class, "Payment");'),
    ("MerchantSession", 'static SubResource.ClassData data = new SubResource.ClassData(AllowedInstallment.class, "AllowedInstallments");'),
])
def test_generatedInnerClassDataFollowsTheRealQualifierAndCompilesWithTheSdk(listRun, builtOnce, resource, expected):
    code, out, target = builtOnce(resource, "java")
    assert code == 0, out
    source = (target / f"src/main/java/com/starkbank/{resource}.java").read_text(encoding="utf-8")

    assert f"        {expected}\n" in source
    assert (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8").count(expected) == 1
