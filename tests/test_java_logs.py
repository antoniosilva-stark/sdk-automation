import re
import yaml
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavaSdk, requiresJavac
from test_java_field_types import REAL_SOURCES, SCHEMAS, _generated, _realConventionsText, _splitParameters

# Logs do real que NAO tem o formato simples (created, type, [errors], entidade, id): ficam fora do javaLogs e seguem a
# saida de hoje do template. O teste prova que cada um segue diferente do simples no real, entao a lista nao envelhece.
LOG_EXCLUSIONS = {
    "BoletoHolmes": "campo extra `updated` e ctor created,type,holmes,id,updated",
    "CorporatePurchase": "campos extras description e corporateTransactionId e ctor all-args de 7 parametros",
    "MerchantInstallment": "o parametro do ctor e `merchantInstallment` e o campo e `installment`",
    "PaymentRequest": "Log escrito a mao com a classe interna User (ME 9.8)",
}

SHORT_RETURNS = {"Log", "Generator<Log>", "Log.Page"}


SPEC_FILE = REPO_ROOT / "apis/spec-v2.openapi.yaml"


def _logEntries() -> dict:
    return yaml.safe_load(_realConventionsText())["javaLogs"]


def _logBlock(source: str) -> str | None:
    match = re.search(r"^    public (?:final )?static class Log extends Resource \{.*?^    \}\n", source, re.M | re.S)
    return match.group(0) if match else None


def _realLogBlock(resource: str) -> str | None:
    return _logBlock((REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8"))


def _logFields(block: str) -> dict:
    return {name: javaType for javaType, name in re.findall(r"^        public ([\w<>\[\], .]+?) (\w+);", block, re.M)}


def _logConstructor(block: str) -> list:
    return _splitParameters(re.search(r"^        public Log\((.*?)\) \{", block, re.M | re.S).group(1))


def _logSignatures(block: str) -> list:
    found = re.findall(r"^        public static (.+?) (get|query|page)\((.*?)\) throws", block, re.M | re.S)
    return sorted((returns, name, " ".join(parameters.split())) for returns, name, parameters in found)


def _logPdfSignatures(block: str | None) -> list:
    found = re.findall(r"^        public static InputStream pdf\((.*?)\) throws Exception", block or "", re.M | re.S)
    return sorted(" ".join(parameters.split()) for parameters in found)


def _pageParts(block: str) -> tuple:
    constructor = re.search(r"^            public Page\((.*?)\) \{", block, re.M | re.S).group(1)
    return (re.search(r"^            public (List<.+?>) logs;", block, re.M).group(1), _splitParameters(constructor))


def _classDataClass(block: str) -> str:
    return re.search(r"^        static ClassData data = new ClassData\((.+?), ", block, re.M).group(1)


def _realSimpleShape(resource: str) -> dict | None:
    block = _realLogBlock(resource)
    if block is None or not re.search(r"^        public Log\(", block, re.M):
        return None
    constructor = _logConstructor(block)
    names = [name for _, name in constructor]
    types = dict((name, javaType) for javaType, name in constructor)
    entity = names[-2] if len(names) > 1 else None
    hasErrors = "errors" in names
    isSimple = (
        names == ["created", "type", *(["errors"] if hasErrors else []), entity, "id"]
        and types.get(entity) == resource
        and set(_logFields(block)) == set(names) - {"id"}
    )
    if not isSimple:
        return None
    returns = {returned for returned, _, _ in _logSignatures(block)}
    qualified = {f"{resource}.Log", f"Generator<{resource}.Log>", f"{resource}.Log.Page"}
    if returns not in (SHORT_RETURNS, qualified):
        return None
    classData = "short" if _classDataClass(block) == "Log.class" else "qualified"
    return {
        "names": "short" if returns == SHORT_RETURNS else "qualified",
        "errors": types.get("errors", "none"),
        "classData": classData,
    }


def _realLogResources(buildResource) -> list:
    return [resource for resource in buildResource.loadTool("parity-report").schemaResources()
            if _realLogBlock(resource) is not None]


@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(_logEntries()))
def test_everyJavaLogsEntryEqualsTheShapeOfTheRealLog(resource):
    assert _realSimpleShape(resource) == _logEntries()[resource]


@requiresJavaSdk
def test_everyRealClassDataOfAListedLogIsLogClassOrTheQualifiedForm(buildResource):
    for resource in _logEntries():
        assert _classDataClass(_realLogBlock(resource)) in ("Log.class", f"{resource}.Log.class")


@requiresJavaSdk
def test_everyRealLogWithTheSimpleShapeIsInJavaLogsUnlessExcluded(buildResource):
    simple = {resource for resource in _realLogResources(buildResource) if _realSimpleShape(resource)}

    assert simple == set(_logEntries())


@requiresJavaSdk
def test_everyRealLogIsEitherInJavaLogsOrInTheNamedExclusionSet(buildResource):
    assert set(_realLogResources(buildResource)) == set(_logEntries()) | set(LOG_EXCLUSIONS)


@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(LOG_EXCLUSIONS))
def test_excludedResourceStillDiffersFromTheSimpleShapeInTheReal(resource):
    assert _realLogBlock(resource) is not None
    assert _realSimpleShape(resource) is None
    assert resource not in _logEntries()


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(_logEntries()))
def test_generatedLogDeclarationsMatchTheRealOnes(buildResource, tmpPath, resource):
    generated = _logBlock(_generated(buildResource, tmpPath, resource, _realConventionsText()))
    real = _realLogBlock(resource)

    assert _logSignatures(generated) == _logSignatures(real)
    assert _logConstructor(generated) == _logConstructor(real)
    assert _pageParts(generated) == _pageParts(real)
    assert set(_logFields(generated).items()) == set(_logFields(real).items())
    assert _classDataClass(generated) == _classDataClass(real)
    assert _logPdfSignatures(generated) == _logPdfSignatures(real)


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", sorted(set(LOG_EXCLUSIONS) - {"PaymentRequest"}))
def test_excludedResourceGeneratesItsLogWithTheSamePdfOperationsAsTheReal(buildResource, tmpPath, resource):
    generated = _logBlock(_generated(buildResource, tmpPath, resource, _realConventionsText()))

    assert _logPdfSignatures(generated) == _logPdfSignatures(_realLogBlock(resource)) == []


def _specLogPdfResources(buildResource) -> set:
    spec = buildResource.loadFast(SPEC_FILE.read_text(encoding="utf-8"))
    found = set()
    for name, entry in spec["components"]["schemas"].items():
        if "x-sdk-log" not in entry:
            continue
        docs = buildResource.resolveRef(spec, name, REPO_ROOT / "apis").get("x-sdk-docs") or {}
        if ((docs.get("log") or {}).get("pdf")):
            found.add(name)
    return found


@requiresJavaSdk
def test_theResourcesWhoseSpecDocsCarryALogPdfAreExactlyTheOnesWhoseRealLogHasIt(buildResource):
    realPdf = {resource for resource in _realLogResources(buildResource)
               if _logPdfSignatures(_realLogBlock(resource))}

    assert realPdf == {"Deposit", "Invoice"}
    assert _specLogPdfResources(buildResource) == realPdf


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource", ["Deposit", "Invoice"])
def test_generatedLogPdfHasTheRealSignaturesAndReadsTheLogsOwnClassData(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())
    block = _logBlock(source)

    assert _logPdfSignatures(block) == ["String id", "String id, User user"]
    assert "            return Log.pdf(id, null);\n" in block
    assert '            return Rest.getContent(data, id, "pdf", user, new HashMap<>());\n' in block
    assert "import java.io.InputStream;" in source


@requiresGenerator
def test_logPdfJavadocIsRenderedFromTheSpecDocsOfTheLog(buildResource, tmpPath):
    block = _logBlock(_generated(buildResource, tmpPath, "Deposit", _realConventionsText()))
    plain, user = re.findall(r"(        /\*\*\n(?:         \*.*\n)+?         \*/\n)        public static InputStream pdf\(", block)

    assert "         * Retrieve a reversed Deposit Log pdf file\n" in plain
    assert "         * Receive a reversed Deposit Log pdf receipt file generated in the Stark Bank API by its id.\n" in plain
    assert "         * @param id [string]: object unique id. ex: \"5656565656565656\"\n" in plain
    assert "@param user" not in plain
    assert "         * @param user [Organization/Project object, default null]:" in user
    assert "         * @return Deposit pdf file\n" in user
    assert user.count("*/") == 1


@requiresGenerator
def test_logWithoutLogPdfDocsEmitsNoLogPdfAndKeepsTheOuterPdfImport(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", _realConventionsText())

    assert _logPdfSignatures(_logBlock(source)) == []
    assert "public static InputStream pdf(String id, User user)" in source
    assert "import java.io.InputStream;" in source


@requiresGenerator
def test_logWithoutAnyPdfEmitsNoInputStreamImport(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "CorporateHolder", _realConventionsText())

    assert "InputStream" not in source


@requiresGenerator
@pytest.mark.parametrize("resource, entry, page", [
    ("Transfer", 'names: short, errors: "String[]", classData: short', "return new Log.Page(logs, page.cursor);"),
    ("CorporateCard", "names: qualified, errors: none, classData: qualified", "return new Page(logs, page.cursor);"),
])
def test_pageIsBuiltByTheNameTheShapeSelects(buildResource, tmpPath, resource, entry, page):
    source = _generated(buildResource, tmpPath, resource, f"javaLogs:\n  {resource}: {{{entry}}}\n")

    assert page in source


@requiresGenerator
def test_errorsNoneDropsTheFieldTheParameterTheJavadocAndTheImport(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "CorporateCard", "javaLogs:\n  CorporateCard: {names: short, errors: none, classData: short}\n")
    block = _logBlock(source)

    assert "errors" not in block
    assert "ErrorElement" not in source
    assert "public Log(String created, String type, CorporateCard card, String id) {" in block


@requiresGenerator
def test_errorsStringArrayDropsTheErrorElementImport(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", "javaLogs:\n  Transfer: {names: short, errors: \"String[]\", classData: short}\n")

    assert "public String[] errors;" in source
    assert "ErrorElement" not in source


@requiresGenerator
def test_classDataFollowsItsOwnAxisIndependentlyOfTheNames(buildResource, tmpPath):
    mixed = _generated(buildResource, tmpPath / "mixed", "Transfer",
                       "javaLogs:\n  Transfer: {names: short, errors: none, classData: qualified}\n")
    short = _generated(buildResource, tmpPath / "short", "Transfer",
                       "javaLogs:\n  Transfer: {names: qualified, errors: none, classData: short}\n")

    assert 'new ClassData(Transfer.Log.class, "TransferLog");' in mixed
    assert 'new ClassData(Log.class, "TransferLog");' in short


@requiresGenerator
def test_resourceWithoutEntryKeepsTheQualifiedLogWithErrorElement(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Transfer", "javaLogs:\n  Balance: {names: short, errors: none, classData: short}\n")

    assert "public static Transfer.Log get(String id) throws Exception {" in source
    assert "public static Generator<Transfer.Log> query() throws Exception {" in source
    assert "public static Transfer.Log.Page page() throws Exception {" in source
    assert "public List<ErrorElement> errors;" in source
    assert "import com.starkbank.error.ErrorElement;" in source
    assert "return new Page(logs, page.cursor);" in source
    assert 'new ClassData(Transfer.Log.class, "TransferLog");' in source


@requiresGenerator
def test_explicitDefaultEntryEqualsNoEntry(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n")
    explicit = _generated(
        buildResource,
        tmpPath / "explicit",
        "Transfer",
        "javaLogs:\n  Transfer: {names: qualified, errors: \"List<ErrorElement>\", classData: qualified}\n",
    )

    assert explicit == plain


@requiresGenerator
def test_entryOfAnotherResourceLeavesTheGeneratedClassIdentical(buildResource, tmpPath):
    plain = _generated(buildResource, tmpPath / "plain", "Transfer", "javaTypes:\n")
    other = _generated(buildResource, tmpPath / "other", "Transfer", "javaLogs:\n  Invoice: {names: short, errors: none, classData: short}\n")

    assert other == plain


@requiresGenerator
@requiresJavac
@pytest.mark.parametrize("resource, declaration", [
    ("Deposit", "public Log(String created, String type, String[] errors, Deposit deposit, String id) {"),
    ("Invoice", "public Log(String created, String type, String[] errors, Invoice invoice, String id) {"),
    ("Transfer", "public Log(String created, String type, String[] errors, Transfer transfer, String id) {"),
    ("CorporateHolder", "public Log(String created, String type, CorporateHolder holder, String id) {"),
])
def test_resourceWithALogShapeEntryPassesTheJavacGate(builtOnce, resource, declaration):
    code, out, target = builtOnce(resource, "java")

    assert code == 0, out
    assert "sintaxe (javac)" in out
    assert declaration in (target / f"src/main/java/com/starkbank/{resource}.java").read_text(encoding="utf-8")


def _loadLogs(extractSchema, tmpPath, text: str, resource: str = "Balance"):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text(text, encoding="utf-8")
    return extractSchema.javaLogShape(resource, conventions)


def test_javaLogShapeReturnsTheEntryOfTheResource(extractSchema, tmpPath):
    text = "javaLogs:\n  Balance: {names: short, errors: none, classData: short}\n"

    assert _loadLogs(extractSchema, tmpPath, text) == {"names": "short", "errors": "none", "classData": "short"}


def test_javaLogShapeIsTheTodayDefaultWithoutEntryOrSection(extractSchema, tmpPath):
    default = {"names": "qualified", "errors": "List<ErrorElement>", "classData": "qualified"}

    assert _loadLogs(extractSchema, tmpPath, "javaLogs:\n  Invoice: {names: short, errors: none, classData: short}\n") == default
    assert _loadLogs(extractSchema, tmpPath, "javaTypes:\n") == default


@pytest.mark.parametrize("text, message", [
    ("javaLogs:\n  Balance: {names: shorter, errors: none, classData: short}\n", "Balance: javaLogs.names.*'shorter'"),
    ("javaLogs:\n  Balance: {names: short, errors: strings, classData: short}\n", "Balance: javaLogs.errors.*'strings'"),
    ("javaLogs:\n  Balance: {names: short, classData: short}\n", "Balance: javaLogs.errors ausente"),
    ("javaLogs:\n  Balance: {errors: none, classData: short}\n", "Balance: javaLogs.names ausente"),
    ("javaLogs:\n  Balance: short\n", "Balance: javaLogs.*mapa"),
    ("javaLogs:\n  Balance: [short, none]\n", "Balance: javaLogs.*mapa"),
    ("javaLogs:\n  Balance: {names: [short], errors: none, classData: short}\n", "Balance: javaLogs.names.*\\['short'\\]"),
    ("javaLogs:\n  Transferr: {names: short, errors: none, classData: short}\n", "Transferr: javaLogs cita recurso"),
    ("javaLogs:\n  Balance: {names: short, errors: none}\n", "Balance: javaLogs.classData ausente"),
    ("javaLogs:\n  Balance: {names: short, errors: none, classData: shorter}\n", "Balance: javaLogs.classData.*'shorter'"),
    ("javaLogs:\n  Balance: {names: short, errors: none, classData: [short]}\n", "Balance: javaLogs.classData.*\\['short'\\]"),
    ("javaLogs:\n  Balance: {names: short, errors: none, classData: Log.class}\n", "Balance: javaLogs.classData.*'Log.class'"),
    ("javaLogs:\n  balance: {names: short, errors: none, classData: short}\n", "balance: javaLogs cita recurso"),
    ("javaLogs:\n  Invoice: {names: short, errors: nope, classData: short}\n", "Invoice: javaLogs.errors.*'nope'"),
])
def test_invalidJavaLogsDataFailsNamingTheResourceTheFieldAndTheValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=message):
        _loadLogs(extractSchema, tmpPath, text)


def test_javaLogsWithoutAMappingFailsToo(extractSchema, tmpPath):
    with pytest.raises(ValueError, match="javaLogs"):
        _loadLogs(extractSchema, tmpPath, "javaLogs:\n  - Balance\n")


def test_everyJavaLogsEntryOfTheRealTableIsAccepted(extractSchema):
    assert all(extractSchema.javaLogShape(name) == entry for name, entry in _logEntries().items())


def _balanceCode(javaCode, javaDocs, **options):
    schemas = yaml.safe_load((SCHEMAS / "balance.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    entity = javaDocs.describeEntity(schemas["Balance"], schemas["BalanceCreate"])
    return javaCode.renderJavaCode(schemas["Balance"], entity, "Balance", {}, **options)


def test_renderJavaCodeCarriesNoLogWithoutAShape(javaCode, javaDocs):
    assert "log" not in _balanceCode(javaCode, javaDocs)


@pytest.mark.parametrize("shape, expected", [
    ({"names": "short", "errors": "String[]", "classData": "short"},
     {"typeName": "Log", "pageName": "Log.Page", "newPageName": "Log.Page", "classDataClass": "Log",
      "errorsType": "String[]", "hasErrors": True, "usesErrorElement": False}),
    ({"names": "qualified", "errors": "List<ErrorElement>", "classData": "qualified"},
     {"typeName": "Balance.Log", "pageName": "Balance.Log.Page", "newPageName": "Page", "classDataClass": "Balance.Log",
      "errorsType": "List<ErrorElement>", "hasErrors": True, "usesErrorElement": True}),
    ({"names": "qualified", "errors": "none", "classData": "qualified"},
     {"typeName": "Balance.Log", "pageName": "Balance.Log.Page", "newPageName": "Page", "classDataClass": "Balance.Log",
      "errorsType": "", "hasErrors": False, "usesErrorElement": False}),
    ({"names": "short", "errors": "none", "classData": "qualified"},
     {"typeName": "Log", "pageName": "Log.Page", "newPageName": "Log.Page", "classDataClass": "Balance.Log",
      "errorsType": "", "hasErrors": False, "usesErrorElement": False}),
])
def test_renderJavaCodeResolvesTheLogNamesAndErrorsFromTheShape(javaCode, javaDocs, shape, expected):
    assert _balanceCode(javaCode, javaDocs, logShape=shape)["log"] == expected


def _specLogResources(extractSchema, specFile) -> set:
    schemas = extractSchema.loadFast(specFile.read_text(encoding="utf-8"))["components"]["schemas"]
    return {name for name, schema in schemas.items() if isinstance(schema, dict) and "x-sdk-log" in schema}


def _logFlagDrift(flagged: set, entries) -> tuple:
    """Entrada do javaLogs sem a flag `x-sdk-log` na spec nunca chega ao template; recurso com a flag e fora do javaLogs
    e da exclusao nomeada geraria o Log com o default sem ninguem ter decidido. PaymentRequest tem Log escrito a mao, sem
    a flag, por isso a exclusao nao precisa estar na spec."""
    return (sorted(set(entries) - flagged), sorted(flagged - set(entries) - set(LOG_EXCLUSIONS)))


def test_javaLogsAndTheSpecLogFlagCoverTheSameResources(extractSchema):
    flagged = _specLogResources(extractSchema, SPEC_FILE)

    assert _logFlagDrift(flagged, _logEntries()) == ([], [])


def test_logFlagDriftNamesAnEntryWithoutTheFlagAndAFlagWithoutAnEntry(extractSchema, tmpPath):
    spec = tmpPath / "spec.yaml"
    spec.write_text(
        "components:\n  schemas:\n"
        "    Transfer:\n      x-sdk-log: true\n"
        "    Balance:\n      type: object\n"
        "    Webhook:\n      x-sdk-log: true\n",
        encoding="utf-8",
    )
    flagged = _specLogResources(extractSchema, spec)

    assert flagged == {"Transfer", "Webhook"}
    assert _logFlagDrift(flagged, {"Transfer": {}, "Balance": {}}) == (["Balance"], ["Webhook"])
    assert _logFlagDrift(flagged, {"Transfer": {}, "Webhook": {}}) == ([], [])
