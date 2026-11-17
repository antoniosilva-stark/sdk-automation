import re
from hashlib import sha1

import pytest
import yaml

from conftest import requiresGenerator, requiresJavaSdk
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText

SHAPE = {"returns": "wildcard", "param": "previews"}
REAL_SIGNATURES = [
    "public static List<?> create(List<?> previews) throws Exception",
    "public static List<?> create(List<?> previews, User user) throws Exception",
]


def _entries() -> dict:
    return yaml.safe_load(_realConventionsText())["javaCreateShapes"]


def _signatures(source: str) -> list:
    return [" ".join(found.split()).rstrip(" {") for found in
            re.findall(r"^    public static List<[^\n]*? create\([^\n]*", source, re.M)]


# 1. a entrada contra o real

@requiresJavaSdk
def test_thePaymentPreviewEntryIsTheShapeOfTheRealCreateAndIsTheOnlyEntry():
    assert _entries() == {"PaymentPreview": SHAPE}


@requiresJavaSdk
def test_theRealPaymentPreviewCreatesReturnTheWildcardListAndNameTheParameterPreviews():
    source = (REAL_SOURCES / "PaymentPreview.java").read_text(encoding="utf-8")

    assert _signatures(source) == REAL_SIGNATURES


# 2. o gerado

@requiresGenerator
def test_generatedPaymentPreviewCreateHasTheSignaturesAndTheBodyOfTheReal(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "PaymentPreview", _realConventionsText())

    assert _signatures(source) == REAL_SIGNATURES
    assert "for (Object paymentPreview : previews) {" in source
    assert "return Rest.post(data, paymentPreviewList, user);" in source
    assert "paymentPreviews" not in source  # nem o @param do Javadoc, que cita o parametro real
    assert source.count("@param previews [list of PaymentPreview objects or HashMaps]") == 2


@requiresGenerator
def test_withoutAnEntryTheCreateKeepsTheTypedListAndThePluralParameter(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "PaymentPreview", "javaCreateShapes:\n")

    assert _signatures(source) == [
        "public static List<PaymentPreview> create(List<?> paymentPreviews) throws Exception",
        "public static List<PaymentPreview> create(List<?> paymentPreviews, User user) throws Exception",
    ]


@requiresGenerator
def test_aResourceWithoutEntryIsUntouchedByTheShapeBlock(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "Webhook", _realConventionsText())

    assert "List<?> create(" not in source


# 3. a borda

def _load(extractSchema, tmpPath, text: str, resource: str = "PaymentPreview"):
    conventions = tmpPath / f"{sha1(text.encode()).hexdigest()}.yaml"  # loadConventions memoiza por caminho
    conventions.write_text(text, encoding="utf-8")
    return extractSchema.javaCreateShape(resource, conventions)


def test_javaCreateShapeReturnsTheEntryAndNoneWithoutEntryOrSection(extractSchema, tmpPath):
    entry = "javaCreateShapes:\n  PaymentPreview: {returns: wildcard, param: previews}\n"

    assert _load(extractSchema, tmpPath, entry) == SHAPE
    assert _load(extractSchema, tmpPath, entry, "Transfer") is None
    assert _load(extractSchema, tmpPath, "javaTypes:\n") is None


@pytest.mark.parametrize("text, message", [
    ("javaCreateShapes:\n  PaymentPreviews: {returns: wildcard, param: previews}\n", "PaymentPreviews: javaCreateShapes cita recurso"),
    ("javaCreateShapes:\n  PaymentPreview: {returns: wildcard, param: previews, extra: 1}\n",
     "PaymentPreview: javaCreateShapes chave desconhecida: extra"),
    ("javaCreateShapes:\n  PaymentPreview: {returns: list, param: previews}\n",
     "PaymentPreview: javaCreateShapes.returns invalido: 'list'"),
    ("javaCreateShapes:\n  PaymentPreview: {returns: [wildcard], param: previews}\n",
     "PaymentPreview: javaCreateShapes.returns invalido: \\['wildcard'\\]"),
    ("javaCreateShapes:\n  PaymentPreview: {returns: wildcard, param: Previews}\n",
     "PaymentPreview: javaCreateShapes.param invalido: 'Previews'"),
    ("javaCreateShapes:\n  PaymentPreview: {returns: wildcard, param: 'a b'}\n",
     "PaymentPreview: javaCreateShapes.param invalido: 'a b'"),
    ("javaCreateShapes:\n  PaymentPreview: {param: previews}\n", "PaymentPreview: javaCreateShapes.returns ausente"),
    ("javaCreateShapes:\n  PaymentPreview: {returns: wildcard}\n", "PaymentPreview: javaCreateShapes.param ausente"),
    ("javaCreateShapes:\n  PaymentPreview: wildcard\n", "PaymentPreview: javaCreateShapes deve ser um mapa"),
    ("javaCreateShapes: [PaymentPreview]\n", "javaCreateShapes deve ser um mapa"),
])
def test_invalidJavaCreateShapesDataFailsNamingTheResourceTheKeyAndTheValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=message):
        _load(extractSchema, tmpPath, text)


def test_renderCreateShapeRefusesAnythingButAPlainListCreate(javaCode):
    plain = {"isList": True, "hasExpand": False}

    assert javaCode.renderCreateShape("PaymentPreview", SHAPE, plain) == {
        **plain, "wildcard": True, "listParam": "previews"}
    with pytest.raises(ValueError, match="exige um create em lista"):
        javaCode.renderCreateShape("PaymentPreview", SHAPE, None)
    with pytest.raises(ValueError, match="exige um create em lista"):
        javaCode.renderCreateShape("PaymentPreview", SHAPE, {"isList": False, "hasExpand": False})
    with pytest.raises(ValueError, match="nao vale com expand"):
        javaCode.renderCreateShape("PaymentPreview", SHAPE, {"isList": True, "hasExpand": True})


def test_theListParameterNameOfTheShapeAppliesOnlyToTheCreateLine(javaDocs):
    context = {"className": "PaymentPreview", "listParam": "previews"}

    assert javaDocs.entityListLine(context, "create", []).startswith("@param previews [")
    assert javaDocs.entityListLine(context, "put", []).startswith("@param paymentPreviews [")
