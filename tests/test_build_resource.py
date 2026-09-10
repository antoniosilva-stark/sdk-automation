import shutil
import pytest
from pathlib import Path

from conftest import runTool

ARTIFACT = "src/main/java/com/starkbank/SplitProfile.java"
GENERATOR_READY = shutil.which("npx") is not None


def _target(tmpPath: Path) -> Path:
    root = tmpPath / "target"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_generatorsContemJavaENode(buildResource):
    assert set(buildResource.GENERATORS) == {"java", "node"}


def test_javaTemUmRunNodeTemTres(buildResource):
    assert len(buildResource.GENERATORS["java"]) == 1
    assert len(buildResource.GENERATORS["node"]) == 3


def test_papeisDoNodeCobremImplBarrelTypes(buildResource):
    papeis = [run["role"] for run in buildResource.GENERATORS["node"]]
    assert papeis == ["impl", "barrel", "types"]


def test_typescriptAxiosNaoEhUsado(buildResource):
    geradores = {run["generator"] for runs in buildResource.GENERATORS.values() for run in runs}
    assert "typescript-axios" not in geradores


def test_linguagemSemGeradorRetornaDois(tmpPath):
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "cobol",
                        "--into", str(_target(tmpPath)))
    assert code == 2
    assert "linguagem sem gerador configurado" in out


def test_destinoInexistenteRetornaDois(tmpPath):
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java",
                        "--into", str(tmpPath / "nao-existe"))
    assert code == 2
    assert "destino não é um diretório" in out


def test_recursoScaffoldingAbortaNoLint(tmpPath):
    code, out = runTool("build-resource.py", "Balance", "--lang", "java",
                        "--into", str(_target(tmpPath)))
    assert code == 1
    assert "SCAFFOLDING" in out


def test_recursoInexistenteAbortaNoLint(tmpPath):
    code, out = runTool("build-resource.py", "NaoExisteNaSpec", "--lang", "java",
                        "--into", str(_target(tmpPath)))
    assert code == 1
    assert "UNDECLARED" in out


def test_lintVemAntesDoGenerate(tmpPath):
    code, out = runTool("build-resource.py", "Balance", "--lang", "java",
                        "--into", str(_target(tmpPath)))
    assert "lint-spec" in out
    assert "generate" not in out


def test_validacaoDeArgumentoVemAntesDoLint(tmpPath):
    code, out = runTool("build-resource.py", "Balance", "--lang", "cobol",
                        "--into", str(_target(tmpPath)))
    assert code == 2
    assert "lint-spec" not in out


def test_nadaEhEscritoQuandoLintReprova(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Balance", "--lang", "java", "--into", str(target))
    assert list(target.rglob("*")) == []


@pytest.mark.skipif(not GENERATOR_READY, reason="npx ausente")
def test_pilotoProduzArtefatoVerificado(tmpPath):
    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java", "--into", str(target))

    assert code == 0, out
    placed = target / ARTIFACT
    assert placed.is_file()

    source = placed.read_text(encoding="utf-8")
    assert source.count("public static") == 8
    assert "{{" not in source


@pytest.mark.skipif(not GENERATOR_READY, reason="npx ausente")
def test_duasExecucoesProduzemOMesmoConteudo(tmpPath):
    target = _target(tmpPath)
    args = ("SplitProfile", "--lang", "java", "--into", str(target))

    first, _ = runTool("build-resource.py", *args)
    content = (target / ARTIFACT).read_text(encoding="utf-8")
    second, _ = runTool("build-resource.py", *args)

    assert (first, second) == (0, 0)
    assert (target / ARTIFACT).read_text(encoding="utf-8") == content

NODE_ARTIFACTS = (
    "sdk/transaction/transaction.js",
    "sdk/transaction/index.js",
    "types/transaction/transaction.d.ts",
)


@pytest.mark.skipif(not GENERATOR_READY, reason="npx ausente")
def test_nodeProduzOsTresArtefatos(tmpPath):
    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))

    assert code == 0, out
    for relative in NODE_ARTIFACTS:
        assert (target / relative).is_file(), relative


@pytest.mark.skipif(not GENERATOR_READY, reason="npx ausente")
def test_nodeUsaOShapeDeFuncoesDeModulo(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))
    source = (target / "sdk/transaction/transaction.js").read_text(encoding="utf-8")

    assert "exports.get = async function" in source
    assert "static get(" not in source
    assert "let resource = {'class': exports.Transaction, 'name': 'Transaction'}" in source
    assert "require('starkcore').Resource" in source


@pytest.mark.skipif(not GENERATOR_READY, reason="npx ausente")
def test_nodeAplicaCheckDatetimeEmCampoDeData(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))
    source = (target / "sdk/transaction/transaction.js").read_text(encoding="utf-8")

    assert "check.datetime(created)" in source


@pytest.mark.skipif(not GENERATOR_READY, reason="npx ausente")
def test_nodeBarrelSoExportaOperacaoDeclarada(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))
    barrel = (target / "sdk/transaction/index.js").read_text(encoding="utf-8")

    assert "exports.create" in barrel
    assert "exports.page" not in barrel


@pytest.mark.skipif(not GENERATOR_READY, reason="npx ausente")
def test_nodeTypesUsaDeclareModule(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))
    types = (target / "types/transaction/transaction.d.ts").read_text(encoding="utf-8")

    assert "declare module 'starkbank'" in types
    assert "export namespace transaction" in types
    assert "&lt;" not in types
