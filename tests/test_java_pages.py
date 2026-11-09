import re
import shutil
import subprocess
import yaml
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavaSdk, requiresJavac
from test_java_field_types import REAL_SOURCES, SCHEMAS, _generated, _realConventionsText, _splitParameters

SPEC_FILE = REPO_ROOT / "apis/spec-v2.openapi.yaml"
PAGE_OVERLOADS = ("Map<String, Object> params", "User user", "", "Map<String, Object> params, User user")
OVERLOAD_NAMES = ("params", "user", "plain", "paramsUser")

# Page externa do real cujo recurso nao tem `x-sdk-page` na spec: o template nunca a emite, entao o javaPages nao a alcanca.
# O teste prova que cada um segue sem a flag e com a Page no real, entao a lista nao envelhece.
PAGE_EXCLUSIONS = {}

# Parametro do ctor da Page que o real escreve com outro nome que o campo (MerchantInstallments, com M maiuscula): so o
# nome do parametro, que nao muda o contrato, entao fica fora do modelo e nomeado aqui.
PAGE_CTOR_NAME_EXCEPTIONS = {
    "MerchantInstallment": "MerchantInstallments",
}

DEFAULT_NAMES = "short"


def _pageEntries() -> dict:
    return yaml.safe_load(_realConventionsText())["javaPages"]


def _lowerFirst(resource: str) -> str:
    return f"{resource[:1].lower()}{resource[1:]}"


def _defaultShape(resource: str) -> dict:
    return {"listName": f"{_lowerFirst(resource)}s", "names": DEFAULT_NAMES}


def _pageBlock(source: str) -> str | None:
    match = re.search(r"^    public (?:final )?static class Page \{.*?^    \}\n", source, re.M | re.S)
    return match.group(0) if match else None


def _realSource(resource: str) -> str:
    return (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")


def _realPageResources() -> list:
    return sorted(path.stem for path in REAL_SOURCES.glob("*.java") if _pageBlock(path.read_text(encoding="utf-8")))


def _pageParts(source: str) -> dict:
    block = _pageBlock(source)
    listType, listName = re.search(r"^        public (List<.+?>) (\w+);", block, re.M).groups()
    constructor = re.search(r"^        public Page\((.*?)\) \{", block, re.M | re.S).group(1)
    return {"listType": listType, "listName": listName, "constructor": _splitParameters(constructor)}


def _realPartsWithTheGeneratedCtorName(resource: str, source: str) -> dict:
    parts = _pageParts(source)
    ctorName = PAGE_CTOR_NAME_EXCEPTIONS.get(resource)
    if ctorName is None:
        return parts
    return {**parts, "constructor": [(parts["listType"], parts["listName"]), ("String", "cursor")]}


def _pageReturns(source: str) -> dict:
    """Retorno de cada overload externo de `page`, por parametros; a Page do Log tem 8 espacos e nao entra."""
    found = re.findall(r"^    public static (\S*Page) page\((.*?)\) throws", source, re.M | re.S)
    return {re.sub(r"\s*,\s*", ", ", " ".join(parameters.split())): returned for returned, parameters in found}


def _flaggedResources(buildResource) -> set:
    spec = buildResource.loadFast(SPEC_FILE.read_text(encoding="utf-8"))
    return {name for name, entry in spec["components"]["schemas"].items()
            if isinstance(entry, dict) and "x-sdk-page" in entry}


def _realShape(resource: str) -> dict | None:
    """None quando a Page do real nao tem a forma que o template emite (campo, parametro e retornos coerentes). Retorno
    qualificado em so algumas sobrecargas vira `names: short` mais `qualifiedOverloads`, na ordem canonica."""
    source = _realSource(resource)
    parts = _pageParts(source)
    returns = _pageReturns(source)
    ctorName = PAGE_CTOR_NAME_EXCEPTIONS.get(resource, parts["listName"])
    qualified = f"{resource}.Page"
    isShape = (
        parts["listType"] == f"List<{resource}>"
        and parts["constructor"] == [(parts["listType"], ctorName), ("String", "cursor")]
        and set(returns) == set(PAGE_OVERLOADS)
        and set(returns.values()) <= {"Page", qualified}
    )
    if not isShape:
        return None
    qualifiedOverloads = [name for name, parameters in zip(OVERLOAD_NAMES, PAGE_OVERLOADS) if returns[parameters] == qualified]
    if len(qualifiedOverloads) == len(PAGE_OVERLOADS):
        return {"listName": parts["listName"], "names": "qualified"}
    if not qualifiedOverloads:
        return {"listName": parts["listName"], "names": "short"}
    return {"listName": parts["listName"], "names": "short", "qualifiedOverloads": qualifiedOverloads}


def _effectiveShape(resource: str) -> dict:
    return _pageEntries().get(resource) or _defaultShape(resource)


@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(_pageEntries()))
def test_everyJavaPagesEntryEqualsTheShapeOfTheRealPage(resource):
    assert _realShape(resource) == _pageEntries()[resource]


@requiresJavaSdk
def test_everyRealOuterPageWithTheSpecFlagIsItsEntryOrTheDefault(buildResource):
    flagged = _flaggedResources(buildResource)
    resources = [resource for resource in _realPageResources() if resource in flagged]

    assert resources
    for resource in resources:
        assert _realShape(resource) == _effectiveShape(resource), resource


@requiresJavaSdk
def test_noJavaPagesEntryIsRedundantWithTheDefault():
    redundant = [resource for resource, shape in _pageEntries().items() if shape == _defaultShape(resource)]

    assert redundant == []


@requiresJavaSdk
def test_everyRealOuterPageIsFlaggedOrInTheNamedExclusionSet(buildResource):
    flagged = _flaggedResources(buildResource)
    unflagged = {resource for resource in _realPageResources() if resource not in flagged}

    assert unflagged == set(PAGE_EXCLUSIONS)


@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(PAGE_EXCLUSIONS))
def test_excludedResourceStillHasARealPageNoSpecFlagAndNoEntry(buildResource, resource):
    assert _pageBlock(_realSource(resource)) is not None
    assert resource not in _flaggedResources(buildResource)
    assert resource not in _pageEntries()


def test_javaPagesAndTheSpecPageFlagCoverTheSameResourcesOnlyWhereTheEntryExists(buildResource):
    assert sorted(set(_pageEntries()) - _flaggedResources(buildResource)) == []


def _generatedPage(buildResource, tmpPath, resource: str, conventionsText: str):
    source = _generated(buildResource, tmpPath, resource, conventionsText)
    return source, _pageParts(source), _pageReturns(source)


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(_pageEntries()))
def test_generatedPageDeclarationsMatchTheRealOnes(buildResource, tmpPath, resource):
    _, parts, returns = _generatedPage(buildResource, tmpPath, resource, _realConventionsText())
    real = _realSource(resource)

    assert parts == _realPartsWithTheGeneratedCtorName(resource, real)
    assert returns == _pageReturns(real)


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", ["Boleto", "Transfer", "Event"])
def test_generatedPageOfAResourceWithoutEntryMatchesTheRealDefaultShape(buildResource, tmpPath, resource):
    _, parts, returns = _generatedPage(buildResource, tmpPath, resource, _realConventionsText())

    assert parts == _pageParts(_realSource(resource))
    assert returns == _pageReturns(_realSource(resource))


@requiresGenerator
def test_resourceWithoutEntryGeneratesTheDefaultListNameAndShortPage(buildResource, tmpPath):
    source, parts, returns = _generatedPage(buildResource, tmpPath, "Transfer", "javaTypes:\n")

    assert parts["listName"] == "transfers"
    assert set(returns.values()) == {"Page"}
    assert "        List<Transfer> transfers = new ArrayList<>();\n" in source
    assert "        return new Page(transfers, page.cursor);\n" in source


@requiresGenerator
def test_explicitDefaultEntryEqualsNoEntry(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n")
    explicit = _generated(buildResource, tmpPath / "explicit", "Transfer",
                          "javaPages:\n  Transfer: {listName: transfers, names: short}\n")

    assert explicit == plain


@requiresGenerator
def test_entryOfAnotherResourceLeavesTheGeneratedClassIdentical(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n")
    other = _generated(buildResource, tmpPath / "other", "Transfer",
                       "javaPages:\n  Invoice: {listName: invoices, names: qualified}\n")

    assert other == plain


@requiresGenerator
def test_entryWithoutTheSpecFlagLeavesTheClassIdentical(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Balance", "javaTypes:\n")
    other = _generated(buildResource, tmpPath / "other", "Balance",
                       "javaPages:\n  Balance: {listName: balances, names: qualified}\n")

    assert other == plain


@requiresGenerator
def test_customListNameIsTheFieldTheConstructorTheLocalListAndTheReturnedPage(buildResource, tmpPath):
    source, parts, returns = _generatedPage(buildResource, tmpPath, "BoletoPayment",
                                            "javaPages:\n  BoletoPayment: {listName: payments, names: short}\n")

    assert parts == {"listType": "List<BoletoPayment>", "listName": "payments",
                     "constructor": [("List<BoletoPayment>", "payments"), ("String", "cursor")]}
    assert "            this.payments = payments;\n" in source
    assert "        List<BoletoPayment> payments = new ArrayList<>();\n" in source
    assert "            payments.add((BoletoPayment) boletoPayment);\n" in source
    assert "        return new Page(payments, page.cursor);\n" in source
    assert set(returns.values()) == {"Page"}


@requiresGenerator
def test_qualifiedNamesQualifyTheReturnTypeOfAllFourOverloadsAndKeepTheBody(buildResource, tmpPath):
    source, parts, returns = _generatedPage(buildResource, tmpPath, "Transfer",
                                            "javaPages:\n  Transfer: {listName: transfers, names: qualified}\n")

    assert returns == {parameters: "Transfer.Page" for parameters in PAGE_OVERLOADS}
    assert parts["listName"] == "transfers"
    assert "        return new Page(transfers, page.cursor);\n" in source
    assert "    public final static class Page {\n" in source


@requiresGenerator
def test_pageEntryDoesNotTouchTheLogsOwnPage(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n")
    custom = _generated(buildResource, tmpPath / "custom", "Transfer",
                        "javaPages:\n  Transfer: {listName: items, names: qualified}\n")

    assert plain.split("public final static class Log")[1] == custom.split("public final static class Log")[1]


@requiresGenerator
def test_javadocReturnNamesTheListNameOfTheEntry(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "BoletoPayment",
                        "javaPages:\n  BoletoPayment: {listName: payments, names: short}\n")

    assert "     * BoletoPayment.Page.payments: list of BoletoPayment objects with updated attributes\n" in source
    assert "BoletoPayment.Page.boletoPayments" not in source


@requiresGenerator
@requiresJavac
@pytest.mark.parametrize("resource, declaration", [
    ("CorporateHolder", "public Page(List<CorporateHolder> holders, String cursor) {"),
    ("BoletoPayment", "public Page(List<BoletoPayment> payments, String cursor) {"),
    ("MerchantInstallment", "public Page(List<MerchantInstallment> merchantInstallments, String cursor) {"),
    ("MerchantCard", "public Page(List<MerchantCard> merchantCards, String cursor) {"),
])
def test_resourceWithAPageShapeEntryPassesTheJavacGate(builtOnce, resource, declaration):
    code, out, target = builtOnce(resource, "java")

    assert code == 0, out
    assert "sintaxe (javac)" in out
    assert declaration in (target / f"src/main/java/com/starkbank/{resource}.java").read_text(encoding="utf-8")


def _loadPages(extractSchema, tmpPath, text: str, resource: str = "Balance"):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text(text, encoding="utf-8")
    return extractSchema.javaPageShape(resource, conventions)


def test_javaPageShapeReturnsTheEntryOfTheResource(extractSchema, tmpPath):
    text = "javaPages:\n  Balance: {listName: items, names: qualified}\n"

    assert _loadPages(extractSchema, tmpPath, text) == {"listName": "items", "names": "qualified"}


def test_javaPageShapeIsTheTodayDefaultWithoutEntryOrSection(extractSchema, tmpPath):
    default = {"listName": "balances", "names": "short"}

    assert _loadPages(extractSchema, tmpPath, "javaPages:\n  Invoice: {listName: items, names: short}\n") == default
    assert _loadPages(extractSchema, tmpPath, "javaTypes:\n") == default
    assert _loadPages(extractSchema, tmpPath, "javaTypes:\n", "DictKey") == {"listName": "dictKeys", "names": "short"}


@pytest.mark.parametrize("text, message", [
    ("javaPages:\n  Balance: {listName: items, names: shorter}\n", "Balance: javaPages.names.*'shorter'"),
    ("javaPages:\n  Balance: {listName: items, names: [short]}\n", "Balance: javaPages.names.*\\['short'\\]"),
    ("javaPages:\n  Balance: {listName: items}\n", "Balance: javaPages.names ausente"),
    ("javaPages:\n  Balance: {names: short}\n", "Balance: javaPages.listName ausente"),
    ("javaPages:\n  Balance: {listName: '', names: short}\n", "Balance: javaPages.listName.*''"),
    ("javaPages:\n  Balance: {listName: two words, names: short}\n", "Balance: javaPages.listName.*'two words'"),
    ("javaPages:\n  Balance: {listName: 1items, names: short}\n", "Balance: javaPages.listName.*'1items'"),
    ("javaPages:\n  Balance: {listName: [items], names: short}\n", "Balance: javaPages.listName.*\\['items'\\]"),
    ("javaPages:\n  Balance: {listName: cursor, names: short}\n", "Balance: javaPages.listName.*'cursor'"),
    ("javaPages:\n  Balance: {listName: page, names: short}\n", "Balance: javaPages.listName.*'page'"),
    ("javaPages:\n  Balance: {listName: params, names: short}\n", "Balance: javaPages.listName.*'params'"),
    ("javaPages:\n  Balance: {listName: user, names: short}\n", "Balance: javaPages.listName.*'user'"),
    ("javaPages:\n  Balance: {listName: balance, names: short}\n", "Balance: javaPages.listName.*'balance'"),
    ("javaPages:\n  Balance: {listName: items, names: short, extra: 1}\n", "Balance: javaPages chave desconhecida: extra"),
    ("javaPages:\n  Balance: short\n", "Balance: javaPages.*mapa"),
    ("javaPages:\n  Balance: [items, short]\n", "Balance: javaPages.*mapa"),
    ("javaPages:\n  Transferr: {listName: items, names: short}\n", "Transferr: javaPages cita recurso"),
    ("javaPages:\n  balance: {listName: items, names: short}\n", "balance: javaPages cita recurso"),
    ("javaPages:\n  Invoice: {listName: items, names: nope}\n", "Invoice: javaPages.names.*'nope'"),
    ("javaPages:\n  Balance: {listName: items, names: short, qualifiedOverloads: [nope]}\n",
     "Balance: javaPages.qualifiedOverloads.*'nope'"),
    ("javaPages:\n  Balance: {listName: items, names: short, qualifiedOverloads: [params, params]}\n",
     "Balance: javaPages.qualifiedOverloads repete 'params'"),
    ("javaPages:\n  Balance: {listName: items, names: short, qualifiedOverloads: []}\n",
     "Balance: javaPages.qualifiedOverloads.*\\[\\]"),
    ("javaPages:\n  Balance: {listName: items, names: short, qualifiedOverloads: params}\n",
     "Balance: javaPages.qualifiedOverloads.*'params'"),
    ("javaPages:\n  Balance: {listName: items, names: qualified, qualifiedOverloads: [params]}\n",
     "Balance: javaPages.qualifiedOverloads.*names"),
])
def test_invalidJavaPagesDataFailsNamingTheResourceTheKeyAndTheValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=message):
        _loadPages(extractSchema, tmpPath, text)


@pytest.mark.parametrize("word", [
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const", "continue", "default",
    "do", "double", "else", "enum", "extends", "final", "finally", "float", "for", "goto", "if", "implements", "import",
    "instanceof", "int", "interface", "long", "native", "new", "package", "private", "protected", "public", "return",
    "short", "static", "strictfp", "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try",
    "void", "volatile", "while", "true", "false", "null",
])
def test_javaPagesListNameThatIsAJavaReservedWordFailsNamingTheResourceTheKeyAndTheValue(extractSchema, tmpPath, word):
    text = f"javaPages:\n  Balance: {{listName: '{word}', names: short}}\n"

    with pytest.raises(ValueError, match=f"Balance: javaPages.listName invalido: '{word}'"):
        _loadPages(extractSchema, tmpPath, text)


def test_javaPagesWithoutAMappingFailsToo(extractSchema, tmpPath):
    with pytest.raises(ValueError, match="javaPages"):
        _loadPages(extractSchema, tmpPath, "javaPages:\n  - Balance\n")


def test_everyJavaPagesEntryOfTheRealTableIsAccepted(extractSchema):
    assert all(extractSchema.javaPageShape(name) == entry for name, entry in _pageEntries().items())


def _balanceCode(javaCode, javaDocs, **options):
    schemas = yaml.safe_load((SCHEMAS / "balance.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    entity = javaDocs.describeEntity(schemas["Balance"], schemas["BalanceCreate"])
    return javaCode.renderJavaCode(schemas["Balance"], entity, "Balance", {}, **options)


def test_renderJavaCodeCarriesNoPageWithoutAShape(javaCode, javaDocs):
    assert "page" not in _balanceCode(javaCode, javaDocs)


@pytest.mark.parametrize("shape, expected", [
    ({"listName": "balances", "names": "short"}, {"listName": "balances", "paramsTypeName": "Page",
     "userTypeName": "Page", "plainTypeName": "Page", "paramsUserTypeName": "Page"}),
    ({"listName": "items", "names": "qualified"}, {"listName": "items", "paramsTypeName": "Balance.Page",
     "userTypeName": "Balance.Page", "plainTypeName": "Balance.Page", "paramsUserTypeName": "Balance.Page"}),
    ({"listName": "items", "names": "short", "qualifiedOverloads": ["user", "paramsUser"]},
     {"listName": "items", "paramsTypeName": "Page", "userTypeName": "Balance.Page", "plainTypeName": "Page",
      "paramsUserTypeName": "Balance.Page"}),
])
def test_renderJavaCodeResolvesTheListNameAndThePageTypeFromTheShape(javaCode, javaDocs, shape, expected):
    assert _balanceCode(javaCode, javaDocs, pageShape=shape)["page"] == expected


def test_renderDocsReturnUsesTheGivenListNameAndTodaysNameByDefault(javaDocs):
    entity = {"description": "", "fields": []}
    docs = {"page": {"returns": ["list of Widget objects", "cursor to retrieve the next page"]}}
    default = javaDocs.renderDocs("Widget", docs, {"x-sdk-page"}, entity)["lines"]["pagePlain"]
    custom = javaDocs.renderDocs("Widget", docs, {"x-sdk-page"}, entity, pageListName="items")["lines"]["pagePlain"]

    assert "Widget.Page.widgets: list of Widget objects" in default
    assert "Widget.Page.items: list of Widget objects" in custom
    assert [line.replace("items", "widgets") for line in custom] == default


UTILITY_PAYMENT_PAGE_RETURNS = {
    "Map<String, Object> params": "UtilityPayment.Page",
    "User user": "Page",
    "": "Page",
    "Map<String, Object> params, User user": "Page",
}


@requiresGenerator
@requiresJavaSdk
def test_generatedUtilityPaymentQualifiesOnlyThePageMapOverloadLikeTheReal(buildResource, tmpPath):
    source, _, returns = _generatedPage(buildResource, tmpPath, "UtilityPayment", _realConventionsText())

    assert returns == UTILITY_PAYMENT_PAGE_RETURNS == _pageReturns(_realSource("UtilityPayment"))
    assert "    public static UtilityPayment.Page page(Map<String, Object> params) throws Exception {\n" in source
    assert "    public static Page page(User user) throws Exception {\n" in source
    assert "    public static Page page() throws Exception {\n" in source
    assert "    public static Page page(Map<String, Object> params, User user) throws Exception {\n" in source


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedUtilityPaymentCompilesWithTheRealSiblingsAndHasNoDeadImport(
    builtOnce, tmp_path_factory, javaRuntimeClasspath,
):
    code, out, target = builtOnce("UtilityPayment", "java")
    assert code == 0, out
    assert "DEAD_IMPORT" not in out
    work = tmp_path_factory.mktemp("utilitypayment-page")
    copied = work / "src/com/starkbank/UtilityPayment.java"
    copied.parent.mkdir(parents=True)
    shutil.copy(target / "src/main/java/com/starkbank/UtilityPayment.java", copied)

    compiled = subprocess.run(
        ["javac", "-Xlint:-options", "-d", str(work / "out"), "-cp", javaRuntimeClasspath,
         "-sourcepath", str(REPO_ROOT / "_references/sdk-java/src/main/java"), str(copied)],
        capture_output=True, text=True,
    )

    assert compiled.returncode == 0, compiled.stderr

