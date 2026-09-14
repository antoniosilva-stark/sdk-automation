import yaml
from pathlib import Path

from conftest import REPO_ROOT, requiresGenerator, runTool

ARTIFACT = "src/main/java/com/starkbank/SplitProfile.java"
WORKFLOW = REPO_ROOT / ".github/workflows/sdk-sync.yaml"


def _dispatchInputs() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = workflow["on"] if "on" in workflow else workflow[True]
    return trigger["workflow_dispatch"]["inputs"]


def _languageInput() -> dict:
    return _dispatchInputs()["language"]


def _target(tmpPath: Path) -> Path:
    root = tmpPath / "target"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_generatorsContemJavaENode(buildResource):
    assert set(buildResource.GENERATORS) == {"java", "node"}


def test_javaTemDoisRunsNodeTemTres(buildResource):
    assert len(buildResource.GENERATORS["java"]) == 2
    assert len(buildResource.GENERATORS["node"]) == 3


def test_papeisDoJavaCobremRecursoETeste(buildResource):
    assert [run["role"] for run in buildResource.GENERATORS["java"]] == ["main", "test"]


def test_papeisDoNodeCobremImplBarrelTypes(buildResource):
    papeis = [run["role"] for run in buildResource.GENERATORS["node"]]
    assert papeis == ["impl", "barrel", "types"]


def test_workflowOfereceTodaLinguagemComGerador(buildResource):
    assert set(buildResource.GENERATORS) <= set(_languageInput()["options"])


def test_workflowNaoOfereceLinguagemSemGerador(buildResource):
    assert set(_languageInput()["options"]) <= set(buildResource.GENERATORS)


def test_defaultDoWorkflowEhUmaLinguagemOferecida():
    language = _languageInput()
    assert language["default"] in language["options"]


def test_dispatchPedeApenasRecursoELinguagem():
    assert set(_dispatchInputs()) == {"resource", "language"}


def test_dispatchNaoPedeDestinoNemBase():
    raw = WORKFLOW.read_text(encoding="utf-8")
    for derived in ("inputs.owner", "inputs.repo", "inputs.base"):
        assert derived not in raw


def test_ownerVemDoRepositorioQueExecuta():
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "github.repository_owner" in raw


def test_baseNaoEhFixadaNoWorkflow():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["sync"]["steps"]

    pr = next(s for s in steps if "gh pr create" in (s.get("run") or ""))
    assert pr["env"]["BASE"] == "${{ steps.base.outputs.base }}"
    assert "--base \"$BASE\"" in pr["run"]

    checkout = next(s for s in steps if "Checkout target" in (s.get("name") or ""))
    assert "ref" not in (checkout.get("with") or {}), "ref fixo reintroduz o defeito nº12"


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
    code, out = runTool("build-resource.py", "Account", "--lang", "java",
                        "--into", str(_target(tmpPath)))
    assert code == 1
    assert "SCAFFOLDING" in out


def test_recursoInexistenteAbortaNoLint(tmpPath):
    code, out = runTool("build-resource.py", "NaoExisteNaSpec", "--lang", "java",
                        "--into", str(_target(tmpPath)))
    assert code == 1
    assert "UNDECLARED" in out


def test_lintVemAntesDoGenerate(tmpPath):
    code, out = runTool("build-resource.py", "Account", "--lang", "java",
                        "--into", str(_target(tmpPath)))
    assert "lint-spec" in out
    assert "generate" not in out


def test_validacaoDeArgumentoVemAntesDoLint(tmpPath):
    code, out = runTool("build-resource.py", "Account", "--lang", "cobol",
                        "--into", str(_target(tmpPath)))
    assert code == 2
    assert "lint-spec" not in out


def test_nadaEhEscritoQuandoLintReprova(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Account", "--lang", "java", "--into", str(target))
    assert list(target.rglob("*")) == []


@requiresGenerator
def test_pilotoProduzArtefatoVerificado(tmpPath):
    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java", "--into", str(target))

    assert code == 0, out
    placed = target / ARTIFACT
    assert placed.is_file()

    source = placed.read_text(encoding="utf-8")
    assert source.count("public static SplitProfile get(") == 2
    assert source.count("public static Generator<SplitProfile> query(") == 4
    assert source.count("public static Page page(") == 4
    assert source.count("public static Log get(") == 2
    assert source.count("public static Generator<Log> query(") == 4
    assert source.count("public static Log.Page page(") == 4
    assert "{{" not in source


@requiresGenerator
def test_geradoNaoChamaRestPutQueNaoExisteNoSdkJava(tmpPath):
    """`Rest.java` do sdk-java @ c7f40b8 nao tem `put` de entidade — so `patch` e `putRaw`.

    Gerar `put` produzia arquivo que nao compila, e o alvo nao tem CI para pegar.
    Decisao 43: piloto sai com get/query/page; `put` fica como `todo` no contrato.
    `page` continua, porque `Rest.getPage` existe (Rest.java:100).
    """
    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java", "--into", str(target))

    assert code == 0, out
    source = (target / ARTIFACT).read_text(encoding="utf-8")
    assert "Rest.put(" not in source, "chama primitivo que nao existe no SDK real"
    assert "Rest.getPage(" in source


@requiresGenerator
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


@requiresGenerator
def test_nodeProduzOsTresArtefatos(tmpPath):
    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))

    assert code == 0, out
    for relative in NODE_ARTIFACTS:
        assert (target / relative).is_file(), relative


@requiresGenerator
def test_nodeUsaOShapeDeFuncoesDeModulo(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))
    source = (target / "sdk/transaction/transaction.js").read_text(encoding="utf-8")

    assert "exports.get = async function" in source
    assert "static get(" not in source
    assert "let resource = {'class': exports.Transaction, 'name': 'Transaction'}" in source
    assert "require('starkcore').Resource" in source


@requiresGenerator
def test_nodeAplicaCheckDatetimeEmCampoDeData(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))
    source = (target / "sdk/transaction/transaction.js").read_text(encoding="utf-8")

    assert "check.datetime(created)" in source


@requiresGenerator
def test_nodeBarrelSoExportaOperacaoDeclarada(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))
    barrel = (target / "sdk/transaction/index.js").read_text(encoding="utf-8")

    assert "exports.create" in barrel
    assert "exports.page" in barrel
    assert "exports.delete" not in barrel


@requiresGenerator
def test_testeGeradoAcompanhaORecurso(tmpPath):
    """Os 42 recursos do sdk-java tem TestX.java sem excecao: PR sem teste e PR incompleto,
    e e onde a intervencao humana voltaria."""
    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "Invoice", "--lang", "java", "--into", str(target))

    assert code == 0, out
    generated = target / "src/test/java/TestInvoice.java"
    assert generated.is_file()

    source = generated.read_text(encoding="utf-8")
    assert "public class TestInvoice {" in source
    assert "Settings.user = utils.User.defaultProject();" in source
    assert "@Test" in source
    assert "{{" not in source


@requiresGenerator
def test_testeGeradoSoExercitaOperacaoDeclarada(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "DictKey", "--lang", "java", "--into", str(target))
    source = (target / "src/test/java/TestDictKey.java").read_text(encoding="utf-8")

    assert "DictKey.query(" in source
    assert "DictKey.page(" in source
    assert "DictKey.create(" not in source


@requiresGenerator
def test_oDoisArtefatosJavaSaoPosicionados(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Invoice", "--lang", "java", "--into", str(target))

    assert (target / "src/main/java/com/starkbank/Invoice.java").is_file()
    assert (target / "src/test/java/TestInvoice.java").is_file()


@requiresGenerator
def test_deleteEPdfSaemQuandoDeclarados(tmpPath):
    """Transfer exporta delete e pdf no SDK Python, e os dois tem primitivo no Rest real:
    Rest.delete(data, id, user) e Rest.getContent(data, id, "pdf", user, ...)."""
    target = _target(tmpPath)
    runTool("build-resource.py", "Transfer", "--lang", "java", "--into", str(target))
    source = (target / "src/main/java/com/starkbank/Transfer.java").read_text(encoding="utf-8")

    assert "public static Transfer delete(String id)" in source
    assert "public static Transfer delete(String id, User user)" in source
    assert "return Rest.delete(data, id, user);" in source

    assert "public static InputStream pdf(String id)" in source
    assert 'return Rest.getContent(data, id, "pdf", user, new HashMap<>());' in source
    assert "import java.io.InputStream;" in source


@requiresGenerator
def test_updateSaiQuandoDeclarado(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Invoice", "--lang", "java", "--into", str(target))
    source = (target / "src/main/java/com/starkbank/Invoice.java").read_text(encoding="utf-8")

    assert "public static Invoice update(String id, Map<String, Object> patchData)" in source
    assert "return Rest.patch(data, id, patchData, user);" in source


@requiresGenerator
def test_cancelSaiQuandoDeclarado(tmpPath):
    """cancel e Rest.delete com outro nome (InvoicePullRequest.java:355).

    CorporateCard exporta update e cancel no Python, entao exercita as duas flags.
    """
    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "CorporateCard", "--lang", "java", "--into", str(target))

    assert code == 0, out
    source = (target / "src/main/java/com/starkbank/CorporateCard.java").read_text(encoding="utf-8")
    assert "public static CorporateCard cancel(String id)" in source
    assert "public static CorporateCard update(String id, Map<String, Object> patchData)" in source
    assert "return Rest.delete(data, id, user);" in source


@requiresGenerator
def test_recursoSoDeLeituraNaoCarregaImportMorto(tmpPath):
    """Balance, CardMethod, CorporateBalance e PaymentPreview so expoem get/query.

    Generator, ArrayList e List so existem para query/create/page/log — sem condicionar,
    o gate reprova os 4 por DEAD_IMPORT e a spec inteira para de gerar.
    """
    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "Balance", "--lang", "java", "--into", str(target))

    assert code == 0, out
    source = (target / "src/main/java/com/starkbank/Balance.java").read_text(encoding="utf-8")

    assert "import com.starkbank.utils.Generator;" not in source
    assert "import java.util.ArrayList;" not in source
    assert "import java.util.List;" not in source
    assert "import java.util.Map;" in source


@requiresGenerator
def test_semAFlagNaoSaiInputStreamMorto(tmpPath):
    """Recurso sem pdf nao pode carregar import de InputStream — o gate reprova import morto."""
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "java", "--into", str(target))
    source = (target / "src/main/java/com/starkbank/Transaction.java").read_text(encoding="utf-8")

    assert "InputStream" not in source
    assert "delete(" not in source


@requiresGenerator
def test_logSaiComoClasseInternaQuandoDeclarado(tmpPath):
    """No sdk-java o Log nao e arquivo proprio: e classe interna do recurso, com
    ClassData(Log.class, "InvoiceLog"), e o endpoint /invoice/log sai do Api.endpoint."""
    target = _target(tmpPath)
    runTool("build-resource.py", "Invoice", "--lang", "java", "--into", str(target))
    source = (target / "src/main/java/com/starkbank/Invoice.java").read_text(encoding="utf-8")

    assert "public final static class Log extends Resource" in source
    assert 'new ClassData(Log.class, "InvoiceLog")' in source
    assert "public static Log get(String id)" in source
    assert "public static Generator<Log> query()" in source
    assert "public static Log.Page page()" in source
    assert "public Invoice invoice;" in source


@requiresGenerator
def test_recursoSemLogNaoGanhaClasseInterna(tmpPath):
    """Transaction nao tem log/ no SDK Python — gerar Log ali seria inventar recurso."""
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "java", "--into", str(target))
    source = (target / "src/main/java/com/starkbank/Transaction.java").read_text(encoding="utf-8")

    assert "class Log" not in source


@requiresGenerator
def test_pageSaiQuandoASpecDeclara(tmpPath):
    """Todo recurso real do Stark Bank tem page(); a spec não declarava em nenhum.

    O template sempre soube produzir — faltava a flag, então nenhum SDK gerado
    saía com paginação manual.
    """
    target = _target(tmpPath)
    runTool("build-resource.py", "Invoice", "--lang", "java", "--into", str(target))
    source = (target / "src/main/java/com/starkbank/Invoice.java").read_text(encoding="utf-8")

    assert "public static Page page()" in source
    assert "public final static class Page" in source


@requiresGenerator
def test_nodeTypesUsaDeclareModule(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target))
    types = (target / "types/transaction/transaction.d.ts").read_text(encoding="utf-8")

    assert "declare module 'starkbank'" in types
    assert "export namespace transaction" in types
    assert "&lt;" not in types


def test_geradorQueRetornaZeroSemProduzirEhAcusado(tmpPath):
    fakeBin = tmpPath / "bin"
    fakeBin.mkdir()
    fake = fakeBin / "npx"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)

    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java",
                        "--into", str(target), env={"PATH": str(fakeBin)})

    assert code == 1
    assert "retornou 0 mas não produziu o artefato" in out
    assert "SplitProfile.java" in out


def test_nadaEhPosicionadoQuandoOGeradorNaoProduz(tmpPath):
    fakeBin = tmpPath / "bin"
    fakeBin.mkdir()
    fake = fakeBin / "npx"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)

    target = _target(tmpPath)
    runTool("build-resource.py", "SplitProfile", "--lang", "java",
            "--into", str(target), env={"PATH": str(fakeBin)})

    assert list(target.rglob("*")) == []
