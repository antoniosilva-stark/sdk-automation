import re
import shutil
import subprocess

import yaml
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavac, requiresJavaSdk
from test_java_field_types import _balancedBlock, _generated, _innerEntries, _realConventionsText, _realInnerBlock

SOURCEPATH = REPO_ROOT / "_references/sdk-java/src/main/java"


def _entries() -> dict:
    return yaml.safe_load(_realConventionsText())["javaInnerClasses"]["PaymentRequest"]


def _members(block: str) -> list:
    """O bloco sem Javadoc, uma linha por declaracao e com os espacos normalizados; o real escreve `Log(){` e o gerador
    `Log() {`, e o `data` vem antes dos campos no real, entao a comparacao e por conjunto ordenado de linhas."""
    code = re.sub(r"/\*\*.*?\*/", "", block, flags=re.S)
    code = re.sub(r",\s*\n\s*", ", ", code)  # o real quebra assinaturas longas depois da virgula
    lines = (re.sub(r"\s*\{", " {", " ".join(line.split())) for line in code.splitlines())
    return sorted(line for line in lines if line)


def _generatedBlock(source: str, path: str) -> str:
    name = path.rsplit(".", 1)[-1]
    match = re.search(rf"^ {{{4 * (path.count('.') + 1)}}}public (?:final )?static class {name}\b[^{{]*\{{", source, re.M)
    return _balancedBlock(source, match)


@pytest.fixture(scope="module")
def generatedPaymentRequest(tmp_path_factory):
    from conftest import _loadTool
    return _generated(_loadTool("build-resource.py"), tmp_path_factory.mktemp("payment-request"), "PaymentRequest",
                      _realConventionsText())


# 1. os dados contra o real (os testes parametrizados de test_java_field_types.py cobrem campos, ctors, ClassData e
# extends de cada entrada; aqui o que e proprio do Log)

def test_theLogEntryExtendsResourceNestsUserAndIsNotAJavaLogsShape():
    log = _entries()["Log"]

    assert log["extends"] == "Resource"
    assert list(log["inner"]) == ["User"]
    assert log["fields"]["user"] == "PaymentRequest.Log.User"
    assert log["fields"]["request"] == "PaymentRequest"
    assert "PaymentRequest" not in yaml.safe_load(_realConventionsText())["javaLogs"]


@requiresJavaSdk
def test_theRealLogHasNoMapConstructorAndNoOperationsSoNoneIsEmitted():
    block = _realInnerBlock("PaymentRequest", "Log")

    assert "Map<String, Object>" not in block
    assert not re.search(r"public static .*\b(get|query|page)\(", block)
    assert "map" not in _entries()["Log"]
    assert "map" not in _entries()["Log"]["inner"]["User"]


# 2. o PaymentRequest gerado

@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("path", ["Log", "Log.User"])
def test_generatedLogAndUserMatchTheRealDeclarations(generatedPaymentRequest, path):
    real = _realInnerBlock("PaymentRequest", path)
    generated = _generatedBlock(generatedPaymentRequest, path)

    if path == "Log":
        real = real.replace(_realInnerBlock("PaymentRequest", "Log.User"), "")
        generated = generated.replace(_generatedBlock(generatedPaymentRequest, "Log.User"), "")
    assert _members(generated) == _members(real)


@requiresGenerator
def test_generatedLogIsTheSingleLogOfThePaymentRequestAndHasNoOperations(generatedPaymentRequest):
    assert generatedPaymentRequest.count("    public final static class Log extends Resource {\n") == 1
    assert generatedPaymentRequest.count("class Log extends Resource") == 1
    assert generatedPaymentRequest.count("class User extends SubResource") == 1
    log = _generatedBlock(generatedPaymentRequest, "Log")
    assert not re.search(r"public static .*\b(get|query|page)\(", log)
    assert "class Page" not in log


_MAIN = """import com.starkbank.*;

public class Main {
    public static void main(String[] args) throws Exception {
        PaymentRequest.Log.User user = new PaymentRequest.Log.User("a@b.c", "7", "Tony", "http://p", "active", "member");
        PaymentRequest.Log log = new PaymentRequest.Log("c", "u", "created", new String[] {"e"}, null, user, "99");
        System.out.println("log -> " + log.id + " " + log.created + " " + log.type + " " + log.errors[0] + " " + log.user.email);
        PaymentRequest.Log empty = new PaymentRequest.Log();
        System.out.println("empty -> " + empty.id + " " + empty.user);
        System.out.println("user -> " + new PaymentRequest.Log.User().name);
        Event.PaymentRequestEvent event = null;
        PaymentRequest.Log eventLog = event == null ? null : event.log;
        System.out.println("event log -> " + eventLog);
    }
}
"""


def _withoutThePaymentRequestDeserializer() -> str:
    """O `Rest` do real chama o `GsonAdapters`, que registra o `PaymentRequest.Deserializer`; o gerador ainda nao o emite
    (esta waived em apis/java-handwritten.yaml), entao o teste compila uma copia do GsonAdapters sem essa linha."""
    real = (SOURCEPATH / "com/starkbank/utils/GsonAdapters.java").read_text(encoding="utf-8")
    lines = [line for line in real.splitlines() if "PaymentRequest.Deserializer" not in line]
    assert len(lines) == len(real.splitlines()) - 1
    return "\n".join(lines) + "\n"


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedPaymentRequestAndEventCompileWithTheRealSiblingsAndTheLogBehavesAsTheReal(
    builtOnce, tmp_path_factory, javaRuntimeClasspath
):
    code, out, target = builtOnce("PaymentRequest", "java")
    assert code == 0, out
    eventCode, eventOut, eventTarget = builtOnce("Event", "java", "--advisory")
    assert eventCode == 0, eventOut
    work = tmp_path_factory.mktemp("payment-request-runtime")
    generated = [work / f"src/com/starkbank/{name}.java" for name in ("PaymentRequest", "Event")]
    generated[0].parent.mkdir(parents=True)
    shutil.copy(target / "src/main/java/com/starkbank/PaymentRequest.java", generated[0])
    shutil.copy(eventTarget / "src/main/java/com/starkbank/Event.java", generated[1])
    (work / "src/Main.java").write_text(_MAIN, encoding="utf-8")
    shim = work / "src/com/starkbank/utils/GsonAdapters.java"
    shim.parent.mkdir(parents=True)
    shim.write_text(_withoutThePaymentRequestDeserializer(), encoding="utf-8")

    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath", str(SOURCEPATH),
         *map(str, generated), str(shim), str(work / "src/Main.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run(["java", "-cp", f"{work / 'out'}:{javaRuntimeClasspath}", "Main"], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == [
        "log -> 99 c created e a@b.c",
        "empty -> null null",
        "user -> null",
        "event log -> null",
    ]


@requiresGenerator
@requiresJavac
def test_generatedPaymentRequestPassesTheStrictGate(builtOnce):
    code, out, _ = builtOnce("PaymentRequest", "java")

    assert code == 0, out
    assert "sintaxe (javac)" in out
    assert "DEAD_IMPORT" not in out


# 3. as classes internas que ja eram geradas seguem iguais ao real

# derivada das entradas: uma classe interna nova ganha este teste sem ninguem lembrar de listar. Transfer.Rule fica de
# fora: o real carrega o Deserializer dispensado e le `value` sem cast, e tests/test_java_extra_fields.py a compara assim
_EXISTING_INNER = [(resource, path) for resource, path, definition in _innerEntries()
                   if "." not in path and "extends" not in definition and (resource, path) != ("Transfer", "Rule")]


@pytest.fixture(scope="module")
def generatedExisting(tmp_path_factory):
    from conftest import _loadTool
    buildResource = _loadTool("build-resource.py")
    return {resource: _generated(buildResource, tmp_path_factory.mktemp(resource), resource, _realConventionsText())
            for resource in sorted({resource for resource, _ in _EXISTING_INNER})}


@requiresGenerator
@requiresJavaSdk
@pytest.mark.parametrize("resource, name", _EXISTING_INNER)
def test_existingInnerClassesStillMatchTheRealDeclarationsAfterExtendsAndInner(generatedExisting, resource, name):
    real = _realInnerBlock(resource, name)
    generated = _generatedBlock(generatedExisting[resource], name)

    assert _members(generated) == _members(real)
