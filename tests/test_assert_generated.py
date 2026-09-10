import shutil
import pytest
from pathlib import Path

from conftest import requiresJavac, runTool

WORKSPACE = Path.home() / "workspace"


def _resolveSdk(*candidates: str) -> Path | None:
    for candidate in candidates:
        path = WORKSPACE / candidate
        if path.is_dir():
            return path
    return None


REAL_JAVA = _resolveSdk("bank/sdk-java/src/main/java/com/starkbank", "sdk-java/src/main/java/com/starkbank")
REAL_NODE = _resolveSdk("bank/sdk-node/sdk", "sdk-node/sdk")

CLEAN_JAVA = """package com.starkbank;

import com.starkbank.utils.Rest;

public class Widget extends Resource {
    public String status;

    public Widget(String id, String status) {
        super(id);
        this.status = status;
    }

    public static Widget get(String id) throws Exception {
        return Widget.get(id, null);
    }

    public static Widget get(String id, User user) throws Exception {
        return Rest.getId(data, id, user);
    }
}
"""


def _write(tmpPath: Path, name: str, source: str) -> Path:
    target = tmpPath / name
    target.write_text(source, encoding="utf-8")
    return target


def _copyReal(tmpPath: Path, source: Path) -> Path:
    target = tmpPath / source.name
    shutil.copy(source, target)
    return target


def test_arquivoLimpoPassa(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target))
    assert code == 0
    assert "[OK]" in out


def test_relataVerificacoesExecutadas(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target))
    assert "verificações executadas" in out
    assert "estrutura" in out


@requiresJavac
def test_sintaxeRelatadaQuandoJavacFunciona(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target))
    assert "sintaxe (javac)" in out


def test_sintaxeNaoRelatadaQuandoJavacInoperante(tmpPath):
    fakeBin = tmpPath / "bin"
    fakeBin.mkdir()
    fake = fakeBin / "javac"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)

    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target), env={"PATH": str(fakeBin)})
    assert "sintaxe (javac)" not in out
    assert "verificação de sintaxe indisponível" in out
    assert code == 0


@pytest.mark.skipif(REAL_JAVA is None, reason="sdk-java não clonado")
@pytest.mark.parametrize("name", ["Invoice.java", "Transaction.java"])
def test_javaRealDeProducaoPassa(tmpPath, name):
    target = _copyReal(tmpPath, REAL_JAVA / name)
    code, out = runTool("assert-generated.py", str(target))
    assert code == 0, out


@pytest.mark.skipif(REAL_NODE is None, reason="sdk-node não clonado")
@pytest.mark.parametrize("name", ["transfer/transfer.js", "transaction/transaction.js"])
def test_nodeRealDeProducaoPassa(tmpPath, name):
    target = _copyReal(tmpPath, REAL_NODE / name)
    code, out = runTool("assert-generated.py", str(target), "--lang", "node")
    assert code == 0, out


def test_assinaturaAlinhadaNaoEhFalsoPositivo(assertGenerated):
    source = "    public Invoice(Number amount, String due,\n                   String taxId,  String name) {\n"
    assert assertGenerated.checkEmptyTokens(source, "x.java") == []


def test_metodoSemArgumentosNaoEhFalsoPositivo(assertGenerated):
    source = "    public static Generator<Invoice> query() throws Exception {\n"
    assert assertGenerated.checkEmptyTokens(source, "x.java") == []


def test_placeholderResidualReprova(tmpPath):
    target = _write(tmpPath, "Widget.java", "public class {{classname}} extends Resource {}\n")
    code, out = runTool("assert-generated.py", str(target))
    assert code == 1
    assert "PLACEHOLDER" in out


def test_identificadorPerdidoReprova(assertGenerated):
    issues = assertGenerated.checkEmptyTokens("        for (Object  : items) {\n", "x.java")
    assert len(issues) == 1
    assert issues[0].code == "EMPTY_TOKEN"


def test_slotVazioReprova(assertGenerated):
    issues = assertGenerated.checkEmptyTokens("            if ( instanceof Map)\n", "x.java")
    assert len(issues) == 1


def test_argumentoVazioReprova(assertGenerated):
    issues = assertGenerated.checkEmptyTokens("        return Rest.put(data, , user);\n", "x.java")
    assert len(issues) == 1


def test_arquivoInexistenteRetornaDois():
    code, out = runTool("assert-generated.py", "/tmp/nao-existe-mesmo.java")
    assert code == 2
    assert "[ERROR]" in out


def test_contratoIgnoraComentarios(assertGenerated, tmpPath):
    contract = _write(tmpPath, "x.contract", "# comentario\n\n## secao\nsignature public static void go()\n")
    parsed = assertGenerated.parseContract(contract)
    assert parsed["signature"] == ["public static void go()"]


def test_gapDeContratoNaoReprovaSemStrict(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "signature public static void ausente()\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract))
    assert code == 0
    assert "gap(s) de contrato" in out


def test_gapDeContratoReprovaComStrict(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "signature public static void ausente()\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")
    assert code == 1
    assert "CONTRACT_GAP" in out


def test_assinaturaPresenteNaoEhGap(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "signature public static Widget get(String id)\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")
    assert code == 0, out


def test_importDuplicadoReprova(assertGenerated):
    source = "import java.util.List;\nimport java.util.List;\nclass X { List<String> a; }\n"
    issues = assertGenerated.checkDeadImports(source, "X.java")
    assert len(issues) == 1
    assert issues[0].code == "DEAD_IMPORT"
    assert "duplicado" in issues[0].message


def test_importNaoUsadoReprova(assertGenerated):
    source = "import java.time.OffsetDateTime;\nclass X { String a; }\n"
    issues = assertGenerated.checkDeadImports(source, "X.java")
    assert len(issues) == 1
    assert "nao usado: java.time.OffsetDateTime" in issues[0].message


def test_importUsadoNaoEhReprovado(assertGenerated):
    source = "import java.util.List;\nimport java.util.Map;\nclass X { List<String> a; Map<String,Object> b; }\n"
    assert assertGenerated.checkDeadImports(source, "X.java") == []


def test_importDeTipoUsadoSoEmGenericoPassa(assertGenerated):
    source = "import java.util.ArrayList;\nclass X { void f() { new ArrayList<>(); } }\n"
    assert assertGenerated.checkDeadImports(source, "X.java") == []


def test_wildcardNaoEhAvaliado(assertGenerated):
    source = "import java.util.*;\nclass X { List<String> a; }\n"
    assert assertGenerated.checkDeadImports(source, "X.java") == []


@pytest.mark.skipif(REAL_JAVA is None, reason="sdk-java nao clonado")
def test_importMortoNoJavaRealEhDetectado(tmpPath):
    """O Transfer.java de producao importa GsonEvent sem usar — o gate deve acusar."""
    target = _copyReal(tmpPath, REAL_JAVA / "Transfer.java")
    code, out = runTool("assert-generated.py", str(target))

    assert code == 1
    assert "GsonEvent" in out
