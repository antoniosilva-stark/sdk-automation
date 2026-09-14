import json
import pytest
from pathlib import Path

from conftest import runTool

SDK_PYTHON = Path.home() / "workspace/bank/sdk-python"
REAL_JAVA = Path("_references/sdk-java/src/main/java/com/starkbank")


def _python(root: Path, modules: dict[str, list[tuple[str, str]]]) -> Path:
    """modules: {diretorio: [(nomeDoModulo, nomeDaClasse), ...]}"""
    for directory, entries in modules.items():
        target = root / "starkbank" / directory
        target.mkdir(parents=True)
        for module, className in entries:
            base = "Resource" if className else "SubResource"
            body = f"class {className}({base}):\n    pass\n" if className else "def get(id):\n    pass\n"
            (target / f"__{module}.py").write_text(body, encoding="utf-8")
    return root


def _java(root: Path, classes: list[str]) -> Path:
    target = root / "src/main/java/com/starkbank"
    target.mkdir(parents=True)
    for className in classes:
        (target / f"{className}.java").write_text(f"public class {className} {{}}\n", encoding="utf-8")
    return target


def _run(tmpPath: Path, modules: dict, javaClasses: list[str], *extra: str):
    python = _python(tmpPath / "py", modules)
    java = _java(tmpPath / "java", javaClasses)
    return runTool("list-gaps.py", "--python", str(python), "--java", str(java), *extra)


def _json(tmpPath: Path, modules: dict, javaClasses: list[str]) -> dict:
    code, out = _run(tmpPath, modules, javaClasses, "--json")
    assert code == 0, out
    return json.loads(out)


def test_recursoAusenteNoJavaEhGap(tmpPath):
    report = _json(tmpPath, {"widget": [("widget", "Widget")]}, [])
    assert report["gaps"] == ["Widget"]


def test_recursoPresenteNosDoisNaoEhGap(tmpPath):
    report = _json(tmpPath, {"widget": [("widget", "Widget")]}, ["Widget"])
    assert report["gaps"] == []


def test_classeDeAutenticacaoNaoEhGapNemExtra(tmpPath):
    """User, Project, Organization e afins vivem no starkcore do lado Python.

    Sem a exclusao explicita, davam 6 falsos positivos no sentido inverso.
    """
    report = _json(tmpPath, {"widget": [("widget", "Widget")]}, ["Widget", "User", "Project", "Settings"])
    assert report["extras"] == []


def test_subObjetoQueEhClasseInternaNoJavaNaoEhGap(tmpPath):
    """invoice/__payment.py declara Payment, que no Java e Invoice.Payment — classe
    interna, sem arquivo proprio. Tratar como gap mandaria gerar recurso inexistente."""
    modules = {"invoice": [("invoice", "Invoice"), ("payment", "Payment")]}
    report = _json(tmpPath, modules, ["Invoice"])

    assert report["gaps"] == []
    assert "Payment" in report["subObjects"]


def test_moduloSecundarioComArquivoProprioContaComoPresente(tmpPath):
    """paymentpreview/__boletopreview.py declara BoletoPreview, que no Java TEM arquivo
    proprio. E o lado Java que discrimina, nao um palpite sobre o nome."""
    modules = {"paymentpreview": [("paymentpreview", "PaymentPreview"), ("boletopreview", "BoletoPreview")]}
    report = _json(tmpPath, modules, ["PaymentPreview", "BoletoPreview"])

    assert report["gaps"] == []
    assert report["subObjects"] == []


def test_diretorioSemClasseEhAnunciadoEnaoSilenciado(tmpPath):
    """request/__request.py nao declara classe — e passthrough de HTTP cru.

    Sumir com ele em silencio esconderia um recurso que a automacao nao cobre.
    """
    report = _json(tmpPath, {"request": [("request", "")]}, ["Request"])

    assert report["gaps"] == []
    assert "request" in report["withoutClass"]


def test_extraDoJavaEhAnunciadoSemVirarTrabalho(tmpPath):
    report = _json(tmpPath, {"widget": [("widget", "Widget")]}, ["Widget", "VerifiedAccount"])

    assert report["gaps"] == []
    assert report["extras"] == ["VerifiedAccount"]


def test_saidaHumanaListaOGap(tmpPath):
    code, out = _run(tmpPath, {"widget": [("widget", "Widget")]}, [])
    assert code == 0
    assert "Widget" in out
    assert "[INFO]" in out


def test_raizInvalidaRetornaDois(tmpPath):
    code, out = runTool("list-gaps.py", "--python", str(tmpPath / "nao-existe"), "--java", str(tmpPath))
    assert code == 2
    assert "[ERROR]" in out


@pytest.mark.skipif(not (SDK_PYTHON / "starkbank").is_dir(), reason="sdk-python do Stark Bank não clonado")
@pytest.mark.skipif(not REAL_JAVA.is_dir(), reason="sdk-java não clonado em _references/")
def test_gapRealEhApenasSplitProfile():
    """Se devolver mais que isto, a premissa do gap unico caiu e o plano da Entrega 6 muda."""
    code, out = runTool("list-gaps.py", "--json")
    assert code == 0, out

    report = json.loads(out)
    assert report["gaps"] == ["SplitProfile"], report["gaps"]
