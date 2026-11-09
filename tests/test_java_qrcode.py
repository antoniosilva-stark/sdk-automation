import re
import subprocess
from hashlib import sha1

import yaml
import pytest

from conftest import (
    PYTHON_SDK,
    REPO_ROOT,
    _loadTool,
    requiresGenerator,
    requiresJavac,
    requiresJavaSdk,
    requiresPythonSdk,
)
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText, _splitParameters
from test_java_payment_request_log import SOURCEPATH

NOTE = (
    'Note: the pixel size of each QR code "box" (min 1, max 50, API default 7) cannot currently be customized '
    "through this SDK method; the API's default sizing is always used."
)
SIGNATURE = re.compile(r"^    public static InputStream qrcode\((.*?)\)\s*throws Exception\s*\{", re.M)
ENTRY = {"Invoice": {"qrcode": {"size": NOTE}}}


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _qrcodeSignatures(source: str) -> list:
    return [[javaType for javaType, _ in _splitParameters(parameters)] for parameters in SIGNATURE.findall(source)]


def _qrcodeBodies(source: str) -> list:
    return [_normalized(body) for body in re.findall(
        r"^    public static InputStream qrcode\(.*?\)\s*throws Exception\s*\{\n(.*?)^    \}\n", source, re.M | re.S)]


def _qrcodeJavadocs(source: str) -> list:
    return re.findall(r"(/\*\*(?:(?!\*/).)*?\*/)\s*public static InputStream qrcode\(", source, re.S)


def _realSource(resource: str) -> str:
    return (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    buildResource = _loadTool("build-resource.py")
    return {resource: _generated(buildResource, tmp_path_factory.mktemp(resource.lower()), resource,
                                 _realConventionsText())
            for resource in ("Invoice", "Transfer")}


# 1. o extractor deriva a flag e a doc

@requiresPythonSdk
def test_extractorDerivesTheQrcodeFlagAndDocsForInvoiceOnly(extractSchema):
    flagged, documented = [], []
    for package in sorted((PYTHON_SDK / "starkbank").iterdir()):
        if not (package / "__init__.py").is_file():
            continue
        if "x-sdk-qrcode" in extractSchema.declaredOperations(PYTHON_SDK, package.name)["flags"]:
            flagged.append(package.name)
        if "qrcode" in extractSchema.declaredDocs(PYTHON_SDK, package.name):
            documented.append(package.name)

    assert flagged == documented == ["invoice"]


@requiresPythonSdk
def test_extractorWritesTheQrcodeDocstringIntoTheDocs(extractSchema):
    docs = extractSchema.declaredDocs(PYTHON_SDK, "Invoice")["qrcode"]

    assert [(param["name"], param["section"]) for param in docs["params"]] == [
        ("id", "required"), ("size", "optional"), ("user", "optional"),
    ]
    assert docs["returns"] == ["Invoice png blob"]


def test_qrcodeExportMapsToTheQrcodeFlag(extractSchema):
    assert extractSchema.OPERATION_FLAGS["qrcode"] == "x-sdk-qrcode"


def test_theAppliedSchemaAndTheSpecCarryTheFlagOnlyForInvoiceAndTheSpecHasNoQrcodePath(buildResource):
    spec = buildResource.loadFast((REPO_ROOT / buildResource.SPEC_FILE).read_text(encoding="utf-8"))
    flagged = sorted(name for name, entry in spec["components"]["schemas"].items() if "x-sdk-qrcode" in entry)
    documented = sorted(path.stem for path in (REPO_ROOT / "apis/schemas").glob("*.yaml")
                        if "        qrcode:\n" in path.read_text(encoding="utf-8"))

    assert flagged == ["Invoice"]
    assert documented == ["invoice"]
    assert not [path for path in spec["paths"] if "qrcode" in path]


# 2. o codigo gerado

@requiresGenerator
@requiresJavaSdk
def test_generatedQrcodeHasTheTwoSignaturesOfTheRealOverloadsInOrder(generated):
    assert _qrcodeSignatures(generated["Invoice"]) == _qrcodeSignatures(_realSource("Invoice")) == [
        ["String"], ["String", "User"],
    ]


@requiresGenerator
def test_generatedQrcodeBodiesAreTheRealOnes(generated):
    assert _qrcodeBodies(generated["Invoice"]) == [
        "return Invoice.qrcode(id, null);",
        'return Rest.getContent(data, id, "qrcode", user, new HashMap<>());',
    ]


@requiresGenerator
@requiresJavaSdk
def test_generatedQrcodeBodiesMatchTheRealOnes(generated):
    assert _qrcodeBodies(generated["Invoice"]) == _qrcodeBodies(_realSource("Invoice"))


@requiresGenerator
def test_generatedQrcodeJavadocCarriesTheNoteOnBothOverloads(generated):
    javadocs = _qrcodeJavadocs(generated["Invoice"])

    assert len(javadocs) == 2
    for javadoc in javadocs:
        assert f"     * {NOTE}\n" in javadoc
        assert "@param id [string]: object unique id" in javadoc
        assert "@return Invoice png blob" in javadoc
    assert "@param user" not in javadocs[0]
    assert "@param user [Organization/Project object" in javadocs[1]


@requiresGenerator
def test_generatedQrcodeNoteComesAfterTheDescription(generated):
    javadoc = _qrcodeJavadocs(generated["Invoice"])[0]
    lines = [line.strip() for line in javadoc.splitlines()]

    assert lines[lines.index(f"* {NOTE}") - 1].startswith("* Receive a single Invoice QR Code")


@requiresGenerator
def test_aResourceWithoutTheFlagEmitsNoQrcode(generated):
    assert " qrcode(" not in generated["Transfer"]


@requiresGenerator
def test_invoiceKeepsASingleInputStreamImport(generated):
    assert generated["Invoice"].count("import java.io.InputStream;\n") == 1


# 3. javac contra os irmaos reais

@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedInvoiceWithQrcodeCompilesWithTheRealSiblingsAndHasNoDeadImport(
    builtOnce, tmp_path_factory, javaRuntimeClasspath
):
    code, out, target = builtOnce("Invoice", "java")
    assert code == 0, out
    assert "DEAD_IMPORT" not in out
    work = tmp_path_factory.mktemp("qrcode-compile")

    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath",
         f"{SOURCEPATH}:{target / 'src/main/java'}", str(target / "src/main/java/com/starkbank/Invoice.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr


def test_theInputStreamImportIsGatedByTheQrcodeFlagToo(buildResource):
    assert "usesInputStream=true" in buildResource.javaImports({"x-sdk-qrcode"})
    assert "usesInputStream=true" not in buildResource.javaImports({"x-sdk-get"})


# 4. a secao contra o real

def _entries() -> dict:
    return yaml.safe_load(_realConventionsText())["javaOmittedOperationParams"]


def test_theOmittedOperationParamsSectionHoldsOnlyTheInvoiceQrcodeSize():
    assert _entries() == ENTRY


@requiresJavaSdk
def test_theOmittedParameterIsOneTheRealQrcodeDoesNotHaveAndTheNoteIsItsJavadoc():
    source = _realSource("Invoice")
    declared = [[name for _, name in _splitParameters(parameters)] for parameters in SIGNATURE.findall(source)]

    assert declared == [["id"], ["id", "user"]]
    for operation, parameters in _entries()["Invoice"].items():
        assert operation == "qrcode"
        assert "size" in parameters
        assert f"     * {parameters['size']}\n" in source


# 5. a borda

def _load(extractSchema, tmpPath, text: str, resource: str = "Invoice"):
    conventions = tmpPath / f"{sha1(text.encode()).hexdigest()}.yaml"  # loadConventions memoiza por caminho
    conventions.write_text(text, encoding="utf-8")
    return extractSchema.javaOmittedOperationParams(resource, conventions)


def _section(resource="Invoice", operation="qrcode", parameter="size", note="'Skipped on purpose.'") -> str:
    return f"javaOmittedOperationParams:\n  {resource}:\n    {operation}:\n      {parameter}: {note}\n"


def test_javaOmittedOperationParamsReturnsTheEntryAndNothingWithoutEntryOrSection(extractSchema, tmpPath):
    assert _load(extractSchema, tmpPath, _section()) == {"qrcode": {"size": "Skipped on purpose."}}
    assert _load(extractSchema, tmpPath, _section(), "Transfer") == {}
    assert _load(extractSchema, tmpPath, "javaTypes:\n") == {}


@pytest.mark.parametrize("text, message", [
    (_section(resource="Invoicee"), "Invoicee: javaOmittedOperationParams cita recurso"),
    (_section(operation="delete"), "Invoice.delete: javaOmittedOperationParams operacao sem x-sdk-docs"),
    (_section(parameter="weight"), "Invoice.qrcode.weight: javaOmittedOperationParams parametro inexistente"),
    (_section(parameter="user"), "Invoice.qrcode.user: javaOmittedOperationParams nao omite 'user'"),
    (_section(parameter="id"), "Invoice.qrcode.id: javaOmittedOperationParams nao omite 'id'"),
    (_section(note="''"), "Invoice.qrcode.size: javaOmittedOperationParams nota invalida: ''"),
    (_section(note='"two\\nlines"'), "Invoice.qrcode.size: javaOmittedOperationParams nota invalida: 'two\\nlines'"),
    (_section(note="'closes */ early'"), "Invoice.qrcode.size: javaOmittedOperationParams nota invalida: 'closes */ early'"),
    (_section(note="'back \\ slash'"), "Invoice.qrcode.size: javaOmittedOperationParams nota invalida: 'back \\\\ slash'"),
    (_section(note="7"), "Invoice.qrcode.size: javaOmittedOperationParams nota invalida: 7"),
    ("javaOmittedOperationParams:\n  Invoice: qrcode\n", "Invoice: javaOmittedOperationParams deve ser um mapa"),
    ("javaOmittedOperationParams:\n  Invoice:\n    qrcode: size\n",
     "Invoice.qrcode: javaOmittedOperationParams deve ser um mapa"),
    ("javaOmittedOperationParams: [Invoice]\n", "javaOmittedOperationParams deve ser um mapa"),
])
def test_invalidOmittedOperationParamsFailNamingResourceOperationParameterAndValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        _load(extractSchema, tmpPath, text)


def test_aRequiredParameterCannotBeOmitted(extractSchema, tmpPath):
    with pytest.raises(ValueError, match="Event.parse.content: javaOmittedOperationParams nao omite o obrigatorio"):
        _load(extractSchema, tmpPath, _section("Event", "parse", "content"), "Event")


# 6. a omissao no Javadoc

def _renderedPdf(docs: dict, omitted: dict) -> dict:
    javaDocs = _loadTool("java-docs.py")
    rendered = javaDocs.renderDocs(
        "Thing", docs, {"x-sdk-pdf"}, {"description": "", "fields": []}, omittedParams=omitted
    )["lines"]
    return {key: lines for key, lines in rendered.items() if key.startswith("pdf")}


_PDF_DOCS = {"pdf": {
    "summary": "Retrieve a specific Thing pdf",
    "description": ["Receive a single Thing pdf."],
    "params": [
        {"name": "id", "type": "string", "section": "required", "description": "object unique id"},
        {"name": "layout", "type": "string", "section": "optional", "description": "page layout"},
        {"name": "user", "type": "Organization/Project object", "section": "optional", "description": "user"},
    ],
    "returns": ["Thing pdf file"],
}}


def test_anOmittedOptionalParameterIsNotListedAndKeepsThePlainTwoOverloadsOfTheOperation():
    plain = _renderedPdf(_PDF_DOCS, {"pdf": {"layout": "Layout is fixed."}})
    withLayout = _renderedPdf(_PDF_DOCS, {})

    assert sorted(plain) == ["pdfPlain", "pdfUser"]
    assert sorted(withLayout) == ["pdfParams", "pdfParamsUser", "pdfPlain", "pdfUser"]
    assert not [line for line in plain["pdfPlain"] if "layout" in line.split(" ")[:2]]
    assert "Layout is fixed." in plain["pdfPlain"]
    assert plain["pdfPlain"].index("Layout is fixed.") == plain["pdfPlain"].index("Receive a single Thing pdf.") + 1


def test_withoutOmittedParamsTheRenderedDocsAreIdentical():
    assert _renderedPdf(_PDF_DOCS, {}) == _renderedPdf(_PDF_DOCS, {"get": {"layout": "Not this operation."}})


def test_anOmittedOnlyOptionalParameterOfGetKeepsTheCodeAndTheDocsOnTheSameTwoOverloads(buildResource, tmp_path):
    """A omissao tira o `expand` do unico opcional do get: o java-docs deixa de ver o overload com Map, e o codigo gerado
    tem de seguir o mesmo caminho em vez de emitir as sobrecargas com Javadoc vazio."""
    conventions = yaml.safe_load(_realConventionsText())
    conventions["javaOmittedOperationParams"] = {"CorporateCard": {"get": {"expand": "Expand is fixed."}}}
    path = tmp_path / "type-conventions.yaml"
    path.write_text(yaml.safe_dump(conventions), encoding="utf-8")

    staged = buildResource.stageJavaDocs("CorporateCard", tmp_path / "staged", path)["spec"]

    entry = buildResource.loadFast(staged.read_text(encoding="utf-8"))["components"]["schemas"]["CorporateCard"]
    assert "getParams" not in entry["x-sdk-java-code"]
    assert sorted(key for key in entry["x-sdk-java-docs"] if key.startswith("get")) == ["getPlain", "getUser"]
