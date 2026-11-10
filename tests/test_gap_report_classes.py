import re
import importlib.util

import pytest

from conftest import TOOLS_DIR

spec = importlib.util.spec_from_file_location("gap_report", TOOLS_DIR / "gap-report.py")
gapReport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gapReport)

DELETE_GAP = "signature ausente: public static MerchantPurchase delete(String id)"
GET_GAP = "signature ausente: public static CorporateTransaction get()"


def writeClasses(tmp_path, text):
    path = tmp_path / "gap-classes.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_versionedFileLoadsAndEveryResourceIsKnown():
    classes = gapReport.loadClasses(path=gapReport.CLASSES_FILE)
    assert set(classes) == {"MerchantPurchase", "PaymentPreview", "MerchantSession", "CorporateTransaction", "Transfer"}
    assert all(value in gapReport.VALID_CLASSES for entries in classes.values() for value in entries.values())


def test_versionedFileHoldsExactlyTheEightClassifiedGaps():
    classes = gapReport.loadClasses(path=gapReport.CLASSES_FILE)
    assert sum(len(entries) for entries in classes.values()) == 8
    byClass = {}
    for resource, entries in classes.items():
        for (kind, _), value in entries.items():
            byClass.setdefault(value, []).append((resource, kind))
    assert sorted(byClass["sem-fonte"]) == sorted([
        ("MerchantPurchase", "signature"),
        ("MerchantPurchase", "signature"),
        ("PaymentPreview", "signature"),
        ("PaymentPreview", "signature"),
        ("MerchantSession", "require"),
        ("CorporateTransaction", "signature"),
    ])
    assert sorted(byClass["muda-api-publica"]) == [("MerchantPurchase", "constructor"), ("Transfer", "constructor")]
    assert set(byClass) == {"sem-fonte", "muda-api-publica"}


def test_merchantPurchaseDeleteByIdStaysClassified():
    classes = gapReport.loadClasses(path=gapReport.CLASSES_FILE)
    assert classes["MerchantPurchase"][("signature", DELETE_GAP)] == "sem-fonte"


def test_classOfReturnsTheEntryClass():
    classes = gapReport.loadClasses(path=gapReport.CLASSES_FILE)
    assert gapReport.classOf(classes=classes, resource="CorporateTransaction", kind="signature", text=GET_GAP) == "sem-fonte"


def test_classOfWithoutEntryIsUnclassified():
    classes = gapReport.loadClasses(path=gapReport.CLASSES_FILE)
    assert gapReport.classOf(classes=classes, resource="Split", kind="signature", text=GET_GAP) == gapReport.SEM_CLASSIFICAR
    assert gapReport.classOf(classes=classes, resource="CorporateTransaction", kind="field", text=GET_GAP) == "sem-classificar"


@pytest.mark.parametrize("text, message", [
    ("- a\n", "esperado um mapa recurso"),
    ("NoSuchResource:\n  - kind: signature\n", "NoSuchResource: gap-classes cita recurso que nao existe"),
    ("Split: x\n", "Split: gap-classes deve ser uma lista"),
    ("Split:\n  - x\n", "Split[0]: entrada deve ser um mapa"),
    ("Split:\n  - kind: signature\n    text: t\n    class: sem-fonte\n    why: x\n", "Split[0].why: chave desconhecida"),
    ("Split:\n  - kind: method\n    text: t\n    class: sem-fonte\n", "Split[0].kind invalido"),
    ("Split:\n  - kind: signature\n    text: ''\n    class: sem-fonte\n", "Split[0].text invalido"),
    ("Split:\n  - kind: signature\n    text: 3\n    class: sem-fonte\n", "Split[0].text invalido"),
    ("Split:\n  - kind: signature\n    text: t\n    class: talvez\n", "Split[0].class invalido"),
    (
        "Split:\n  - kind: signature\n    text: t\n    class: sem-fonte\n  - kind: signature\n    text: t\n    class: tem-fonte\n",
        "Split[1]: entrada duplicada",
    ),
])
def test_invalidFileIsRejectedAtTheBoundary(tmp_path, text, message):
    path = writeClasses(tmp_path, text)
    with pytest.raises(ValueError, match=re.escape(message)):
        gapReport.loadClasses(path=path)
