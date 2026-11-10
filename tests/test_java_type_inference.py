import ast
import re
import shutil
import subprocess

import yaml
import pytest

from conftest import (
    PYTHON_SDK,
    _loadTool,
    requiresGenerator,
    requiresJavac,
    requiresJavaSdk,
    requiresPythonSdk,
)
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText
from test_java_payment_request_log import SOURCEPATH, _withoutThePaymentRequestDeserializer

PYTHON_PAYMENT_REQUEST = "starkbank/paymentrequest/__paymentrequest.py"
ALL_ARGS = re.compile(r"^    public PaymentRequest\(String id, .*?\) throws Exception \{\n.*?^    \}\n", re.M | re.S)


def _entry() -> dict:
    return yaml.safe_load(_realConventionsText())["javaTypeInference"]["PaymentRequest"]


def _realGetType() -> str:
    source = (REAL_SOURCES / "PaymentRequest.java").read_text(encoding="utf-8")
    return re.search(r"private static String getType\(Resource payment\) throws Exception\s*\{(.*?)\n    \}", source, re.S).group(1)


def _realMapping(body: str) -> dict:
    return dict(re.findall(r'if\s*\(payment instanceof (\w+)\)\s*\{?\s*return "([^"]+)";', body))


def _realMessage(body: str) -> str:
    thrown = re.search(r"throw new Exception\((.*?)\);", body, re.S).group(1)
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', thrown))


def _pythonMapping() -> dict:
    tree = ast.parse((PYTHON_SDK / PYTHON_PAYMENT_REQUEST).read_text(encoding="utf-8"))
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_parse_payment")
    pairs = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Call):
            continue
        call = node.test
        if getattr(call.func, "id", None) != "isinstance" or not isinstance(call.args[1], ast.Name):
            continue
        returned = node.body[0].value if isinstance(node.body[0], ast.Return) else None
        if isinstance(returned, ast.Tuple) and isinstance(returned.elts[1], ast.Constant):
            pairs[call.args[1].id] = returned.elts[1].value
    return pairs


def _conventions(tmpPath, entry) -> str:
    path = tmpPath / "type-conventions.yaml"
    path.write_text(yaml.safe_dump({"javaTypeInference": {"PaymentRequest": entry}}), encoding="utf-8")
    return path


def _withEntry(**changes) -> str:
    conventions = yaml.safe_load(_realConventionsText())
    conventions["javaTypeInference"]["PaymentRequest"] = {**_entry(), **changes}
    return yaml.safe_dump(conventions)


@pytest.fixture(scope="module")
def generatedPaymentRequest(tmp_path_factory):
    return _generated(_loadTool("build-resource.py"), tmp_path_factory.mktemp("type-inference"), "PaymentRequest",
                      _realConventionsText())


# 1. os dados contra o real e contra o sdk-python

@requiresJavaSdk
def test_theEntryIsTheMappingAndTheMessageOfTheRealGetType():
    body = _realGetType()

    assert list(_entry()["mapping"].items()) == list(_realMapping(body).items())
    assert _entry()["message"] == _realMessage(body)
    assert (_entry()["field"], _entry()["from"]) == ("type", "payment")


@requiresPythonSdk
def test_theEntryMappingIsTheSetOfPairsOfTheSdkPythonIsinstanceChain():
    assert set(_entry()["mapping"].items()) == set(_pythonMapping().items())
    assert len(_pythonMapping()) == 7


# 2. validacao na fronteira

@pytest.mark.parametrize("change, message", [
    ({"extra": 1}, "PaymentRequest: javaTypeInference chave desconhecida: extra"),
    ({"field": "Type"}, "PaymentRequest: javaTypeInference.field invalido"),
    ({"from": "pay-ment"}, "PaymentRequest: javaTypeInference.from invalido"),
    ({"mapping": {}}, "PaymentRequest: javaTypeInference.mapping"),
    ({"mapping": ["Transfer"]}, "PaymentRequest: javaTypeInference.mapping"),
    ({"mapping": {"transfer": "transfer"}}, "PaymentRequest: javaTypeInference.mapping classe invalida"),
    ({"mapping": {"Transfer": "Transfer"}}, "PaymentRequest: javaTypeInference.mapping.Transfer valor invalido"),
    ({"mapping": {"Transfer": "transfer", "Transaction": "transfer"}},
     "PaymentRequest: javaTypeInference.mapping repete o valor 'transfer'"),
    ({"message": ""}, "PaymentRequest: javaTypeInference.message invalida"),
    ({"message": 'say "hi"'}, "PaymentRequest: javaTypeInference.message invalida"),
    ({"message": "one\ntwo"}, "PaymentRequest: javaTypeInference.message invalida"),
    ({"message": "one\rtwo"}, "PaymentRequest: javaTypeInference.message invalida"),
    ({"message": "one\\two"}, "PaymentRequest: javaTypeInference.message invalida"),
    ({"message": "   "}, "PaymentRequest: javaTypeInference.message invalida"),
    ({"message": 7}, "PaymentRequest: javaTypeInference.message invalida"),
    ({"field": "class"}, "PaymentRequest: javaTypeInference.field invalido: 'class'"),
    ({"from": "return"}, "PaymentRequest: javaTypeInference.from invalido: 'return'"),
    ({"field": 3}, "PaymentRequest: javaTypeInference.field invalido: 3"),
    ({"mapping": {"Transfer": 1}}, "PaymentRequest: javaTypeInference.mapping.Transfer valor invalido: 1"),
    ({"mapping": {"Transfer": None}}, "PaymentRequest: javaTypeInference.mapping.Transfer valor invalido: None"),
    ({"mapping": {"Transfer": ["transfer"]}},
     "PaymentRequest: javaTypeInference.mapping.Transfer valor invalido: ['transfer']"),
    ({"mapping": {1: "transfer"}}, "PaymentRequest: javaTypeInference.mapping classe invalida: 1"),
])
def test_aMalformedEntryFailsNamingTheResourceAndTheKey(extractSchema, tmpPath, change, message):
    conventions = _conventions(tmpPath, {**_entry(), **change})

    with pytest.raises(ValueError, match=re.escape(message)):
        extractSchema.javaTypeInference("PaymentRequest", conventions)


def test_aMessageWithCommonPunctuationAndAccentsIsAccepted(extractSchema, tmpPath):
    message = "Pagamento invalido, use Transfer ou Boleto; (veja a doc) - nao e aceito: 100%."
    entry = {**_entry(), "message": message, "field": "type", "from": "payment"}

    assert extractSchema.javaTypeInference("PaymentRequest", _conventions(tmpPath, entry))["message"] == message


def test_aKebabCaseValueWithDigitsAndAMultiWordClassAreAccepted(extractSchema, tmpPath):
    entry = {**_entry(), "mapping": {"BoletoPayment": "boleto-payment-2"}}

    assert extractSchema.javaTypeInference("PaymentRequest", _conventions(tmpPath, entry))["mapping"] == {
        "BoletoPayment": "boleto-payment-2"
    }


@pytest.mark.parametrize("key", ["field", "from", "mapping", "message"])
def test_anEntryWithoutAKeyFailsNamingIt(extractSchema, tmpPath, key):
    entry = {name: value for name, value in _entry().items() if name != key}

    with pytest.raises(ValueError, match=f"PaymentRequest: javaTypeInference.{key} ausente"):
        extractSchema.javaTypeInference("PaymentRequest", _conventions(tmpPath, entry))


def test_anEntryThatIsNotAMapFailsNamingTheResource(extractSchema, tmpPath):
    with pytest.raises(ValueError, match="PaymentRequest: javaTypeInference deve ser um mapa"):
        extractSchema.javaTypeInference("PaymentRequest", _conventions(tmpPath, ["type"]))


def test_anUnknownResourceFailsNamingIt(extractSchema, tmpPath):
    conventions = tmpPath / "type-conventions.yaml"
    conventions.write_text("javaTypeInference:\n  Transferr: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Transferr.*javaTypeInference"):
        extractSchema.javaTypeInference("PaymentRequest", conventions)


def test_theLoaderReturnsTheEntryOrNothing(extractSchema):
    assert extractSchema.javaTypeInference("PaymentRequest") == _entry()
    assert extractSchema.javaTypeInference("Transfer") is None


@pytest.mark.parametrize("change, message", [
    ({"field": "missing"}, "PaymentRequest: javaTypeInference.field 'missing' nao e campo do recurso"),
    ({"from": "missing"}, "PaymentRequest: javaTypeInference.from 'missing' nao e campo do recurso"),
    ({"from": "due"}, "PaymentRequest: javaTypeInference.from 'due' deve ser Resource"),
    ({"field": "amount"}, "PaymentRequest: javaTypeInference.field 'amount' deve ser String, e Long"),
    ({"field": "payment", "from": "payment"}, "PaymentRequest: javaTypeInference.field 'payment' deve ser String"),
])
def test_theFieldsOfTheEntryMustBeFieldsOfTheResourceWithTheRightJavaTypes(buildResource, tmpPath, change, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        _generated(buildResource, tmpPath, "PaymentRequest", _withEntry(**change))


# 3. o PaymentRequest gerado

@requiresGenerator
def test_generatedPaymentRequestInfersTheTypeInBothConstructorsAndDeclaresThePrivateGetType(generatedPaymentRequest):
    allArgs = ALL_ARGS.search(generatedPaymentRequest).group(0)
    mapStart = generatedPaymentRequest.index("public PaymentRequest(Map<String, Object> data) throws Exception {")
    mapBody = generatedPaymentRequest[mapStart:generatedPaymentRequest.index("Unknown parameters", mapStart)]
    block = "        if (this.type == null) {\n            this.type = getType(payment);\n        }\n"

    assert "String due, String[] tags, Long amount, String description, String status" in allArgs
    assert allArgs.endswith(f"        this.created = created;\n{block}    }}\n")
    assert mapBody.index('this.type = (String) dataCopy.remove("type");') < mapBody.index(block)
    assert generatedPaymentRequest.count("private static String getType(Resource payment) throws Exception {") == 1
    assert generatedPaymentRequest.count("getType(payment)") == 2


@requiresGenerator
def test_generatedGetTypeFollowsTheMappingOrderAndThrowsTheMessage(generatedPaymentRequest):
    body = re.search(r"private static String getType\(Resource payment\) throws Exception \{\n(.*?)\n    \}\n",
                     generatedPaymentRequest, re.S).group(1)

    assert list(_realMapping(body).items()) == list(_entry()["mapping"].items())
    assert body.endswith(f'        throw new Exception("{_entry()["message"]}");')
    assert generatedPaymentRequest.index("getType(Resource payment)") < generatedPaymentRequest.index("class Log extends")
    assert generatedPaymentRequest.rindex("public static") < generatedPaymentRequest.index("getType(Resource payment)")


@requiresGenerator
@requiresJavaSdk
def test_generatedAllArgsConstructorHasTheSignatureOfTheReal(generatedPaymentRequest):
    real = (REAL_SOURCES / "PaymentRequest.java").read_text(encoding="utf-8")
    realSignature = re.search(r"public PaymentRequest\(String id, (.*?)\) throws Exception", real, re.S).group(1)
    generatedSignature = ALL_ARGS.search(generatedPaymentRequest).group(0).split(") throws Exception", 1)[0]

    assert generatedSignature.split("(", 1)[1] == "String id, " + " ".join(realSignature.split())


# 4. so o PaymentRequest muda

@requiresGenerator
@pytest.mark.parametrize("resource", ["Transfer", "Invoice", "CorporateHolder"])
def test_aResourceWithoutTheEntryHasNoGetTypeAndItsAllArgsConstructorDoesNotThrow(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())

    assert "getType" not in source
    assert "this.type == null" not in source
    allArgs = re.search(rf"^    public {resource}\((?!Map<)(?!\)).*", source, re.M).group(0)
    assert "throws Exception" not in allArgs


# 5. comportamento em runtime

_TYPES = {
    "Transfer": "transfer",
    "Transaction": "transaction",
    "BoletoPayment": "boleto-payment",
    "UtilityPayment": "utility-payment",
    "TaxPayment": "tax-payment",
    "DarfPayment": "darf-payment",
    "BrcodePayment": "brcode-payment",
}

_MAIN = """import com.starkbank.*;
import com.starkbank.utils.Resource;
import java.util.HashMap;
import java.util.Map;

public class Main {
    static Map<String, Object> data(Resource payment, String type) {
        Map<String, Object> data = new HashMap<>();
        data.put("centerId", "1");
        data.put("payment", payment);
        if (type != null) data.put("type", type);
        return data;
    }

    static void run(String label, java.util.concurrent.Callable<String> action) {
        try {
            System.out.println(label + " -> " + action.call());
        } catch (Exception exception) {
            System.out.println(label + " -> EXCEPTION " + exception.getMessage());
        }
    }

    public static void main(String[] args) throws Exception {
        Resource[] payments = {new Transfer(), new Transaction(), new BoletoPayment(), new UtilityPayment(),
            new TaxPayment(), new DarfPayment(), new BrcodePayment()};
        String[] names = {"Transfer", "Transaction", "BoletoPayment", "UtilityPayment", "TaxPayment", "DarfPayment",
            "BrcodePayment"};
        for (int i = 0; i < payments.length; i++) {
            final Resource payment = payments[i];
            run("map " + names[i], () -> new PaymentRequest(data(payment, null)).type);
            run("allargs " + names[i], () -> new PaymentRequest(null, "1", payment, null, null, null, null, null,
                null, null, null, null).type);
        }
        run("explicit", () -> new PaymentRequest(data(new Transfer(), "boleto-payment")).type);
        run("explicit allargs", () -> new PaymentRequest(null, "1", new Transfer(), "custom", null, null, null, null,
            null, null, null, null).type);
        run("other map", () -> new PaymentRequest(data(new Boleto(), null)).type);
        run("other allargs", () -> new PaymentRequest(null, "1", new Boleto(), null, null, null, null, null, null,
            null, null, null).type);
        run("unknown key", () -> {
            Map<String, Object> data = data(new Transfer(), null);
            data.put("bogus", 1);
            return new PaymentRequest(data).type;
        });
    }
}
"""


@pytest.fixture(scope="module")
def runtimeLines(builtOnce, tmp_path_factory, javaRuntimeClasspath):
    code, out, target = builtOnce("PaymentRequest", "java")
    assert code == 0, out
    work = tmp_path_factory.mktemp("type-inference-runtime")
    generated = work / "src/com/starkbank/PaymentRequest.java"
    generated.parent.mkdir(parents=True)
    shutil.copy(target / "src/main/java/com/starkbank/PaymentRequest.java", generated)
    (work / "src/Main.java").write_text(_MAIN, encoding="utf-8")
    shim = work / "src/com/starkbank/utils/GsonAdapters.java"
    shim.parent.mkdir(parents=True)
    shim.write_text(_withoutThePaymentRequestDeserializer(), encoding="utf-8")
    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath", str(SOURCEPATH),
         str(generated), str(shim), str(work / "src/Main.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run(["java", "-cp", f"{work / 'out'}:{javaRuntimeClasspath}", "Main"], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    return dict(line.split(" -> ", 1) for line in ran.stdout.splitlines())


@requiresGenerator
@requiresJavac
@requiresJavaSdk
@pytest.mark.parametrize("payment, type", sorted(_TYPES.items()))
def test_theMapConstructorInfersTheTypeFromThePaymentClass(runtimeLines, payment, type):
    assert runtimeLines[f"map {payment}"] == type


@requiresGenerator
@requiresJavac
@requiresJavaSdk
@pytest.mark.parametrize("payment, type", sorted(_TYPES.items()))
def test_theAllArgsConstructorInfersTheTypeFromThePaymentClass(runtimeLines, payment, type):
    assert runtimeLines[f"allargs {payment}"] == type


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_anExplicitTypeIsKept(runtimeLines):
    assert runtimeLines["explicit"] == "boleto-payment"
    assert runtimeLines["explicit allargs"] == "custom"


@requiresGenerator
@requiresJavac
@requiresJavaSdk
@pytest.mark.parametrize("label", ["other map", "other allargs"])
def test_aPaymentOfAnotherClassWithoutATypeThrowsTheMessage(runtimeLines, label):
    assert runtimeLines[label] == f"EXCEPTION {_entry()['message']}"


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_unknownParametersStillFailAfterTheInference(runtimeLines):
    assert runtimeLines["unknown key"] == "EXCEPTION Unknown parameters used in constructor: [bogus]"
