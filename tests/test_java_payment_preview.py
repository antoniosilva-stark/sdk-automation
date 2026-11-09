import re
import subprocess
import shutil

import yaml
import pytest

from conftest import REPO_ROOT, _loadTool, requiresGenerator, requiresJavac, requiresJavaSdk
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText
from test_java_payment_request_log import SOURCEPATH

FIELD = re.compile(r"^    public (\w+) payment;", re.M)
CONSTRUCTOR = re.compile(r"^    public PaymentPreview\(String id, String scheduled, String type, String (\w+)\) \{", re.M)


@pytest.fixture(scope="module")
def generatedPaymentPreview(tmp_path_factory):
    return _generated(_loadTool("build-resource.py"), tmp_path_factory.mktemp("payment-preview"), "PaymentPreview",
                      _realConventionsText())


# 1. os dados (javaTypes + javaConstructorTypes) contra o real

@requiresJavaSdk
def test_theRealPaymentPreviewDeclaresAnObjectFieldAndReceivesAStringInTheConstructor():
    real = (REAL_SOURCES / "PaymentPreview.java").read_text(encoding="utf-8")

    assert FIELD.search(real).group(1) == "Object"
    assert CONSTRUCTOR.search(real)


def test_thePaymentPreviewEntriesAreTheFieldTypeAndTheConstructorParameterOfTheReal():
    conventions = yaml.safe_load(_realConventionsText())

    assert conventions["javaTypes"]["PaymentPreview"] == {"payment": "Object"}
    assert conventions["javaConstructorTypes"]["PaymentPreview"] == {"payment": "String"}


# 2. o PaymentPreview gerado

@requiresGenerator
def test_generatedPaymentPreviewHasTheObjectFieldAndTheStringConstructorParameter(generatedPaymentPreview):
    assert FIELD.search(generatedPaymentPreview).group(1) == "Object"
    assert CONSTRUCTOR.search(generatedPaymentPreview)
    assert "        this.payment = payment;\n" in generatedPaymentPreview


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedPaymentPreviewCompilesWithTheRealSiblingsAndPassesTheStrictGate(
    builtOnce, tmp_path_factory, javaRuntimeClasspath
):
    code, out, target = builtOnce("PaymentPreview", "java")
    assert code == 0, out
    assert "DEAD_IMPORT" not in out
    work = tmp_path_factory.mktemp("payment-preview-runtime")
    generated = work / "src/com/starkbank/PaymentPreview.java"
    generated.parent.mkdir(parents=True)
    shutil.copy(target / "src/main/java/com/starkbank/PaymentPreview.java", generated)

    compiled = subprocess.run(
        ["javac", "-Xlint:-options", "-d", str(work / "out"), "-cp", javaRuntimeClasspath,
         "-sourcepath", str(SOURCEPATH), str(generated)],
        capture_output=True, text=True,
    )

    assert compiled.returncode == 0, compiled.stderr


# 3. so o PaymentPreview muda

@requiresGenerator
def test_aResourceWithoutTheEntryKeepsItsStringField(tmp_path):
    source = _generated(_loadTool("build-resource.py"), tmp_path, "Transfer", _realConventionsText())

    assert not re.search(r"^    public Object ", source, re.M)
