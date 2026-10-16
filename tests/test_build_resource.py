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


def _loadPlaceGenerated():
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("place_generated", REPO_ROOT / "tools/place-generated.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target(tmpPath: Path) -> Path:
    root = tmpPath / "target"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_generatorsContainJavaAndNode(buildResource):
    assert set(buildResource.GENERATORS) == {"java", "node"}


def test_javaHasOneRunNodeHasThree(buildResource):
    assert len(buildResource.GENERATORS["java"]) == 1
    assert len(buildResource.GENERATORS["node"]) == 3


def test_javaHasOnlyTheResourceRole(buildResource):
    assert [run["role"] for run in buildResource.GENERATORS["java"]] == ["main"]


def test_nodeRolesCoverImplBarrelTypes(buildResource):
    papeis = [run["role"] for run in buildResource.GENERATORS["node"]]
    assert papeis == ["impl", "barrel", "types"]


PAUSED_LANGUAGES = {"node"}


def test_workflowOffersEveryUnpausedLanguageWithAGenerator(buildResource):
    assert set(buildResource.GENERATORS) - PAUSED_LANGUAGES <= set(_languageInput()["options"])


def test_pausedLanguageIsNotOfferedByTheWorkflow():
    assert PAUSED_LANGUAGES & set(_languageInput()["options"]) == set()


def test_workflowDoesNotOfferLanguageWithoutGenerator(buildResource):
    assert set(_languageInput()["options"]) <= set(buildResource.GENERATORS)


def test_workflowDefaultIsAnOfferedLanguage():
    language = _languageInput()
    assert language["default"] in language["options"]


def test_dispatchAsksOnlyForResourceAndLanguage():
    assert set(_dispatchInputs()) == {"resource", "language"}


def test_dispatchAsksForNeitherTargetNorBase():
    raw = WORKFLOW.read_text(encoding="utf-8")
    for derived in ("inputs.owner", "inputs.repo", "inputs.base"):
        assert derived not in raw


def test_ownerComesFromTheRunningRepository():
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "github.repository_owner" in raw


def test_baseIsNotHardcodedInTheWorkflow():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["sync"]["steps"]

    pr = next(s for s in steps if "gh pr create" in (s.get("run") or ""))
    assert pr["env"]["BASE"] == "${{ steps.base.outputs.base }}"
    assert "--base \"$BASE\"" in pr["run"]

    checkout = next(s for s in steps if "Checkout target" in (s.get("name") or ""))
    assert "ref" not in (checkout.get("with") or {}), "ref fixo reintroduz o defeito nº12"


def test_typescriptAxiosIsNotUsed(buildResource):
    geradores = {run["generator"] for runs in buildResource.GENERATORS.values() for run in runs}
    assert "typescript-axios" not in geradores


def test_languageWithoutGeneratorReturnsTwo(tmpPath):
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "cobol",
                        "--into", str(_target(tmpPath)))
    assert code == 2
    assert "linguagem sem gerador configurado" in out


def test_missingDestinationReturnsTwo(tmpPath):
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java",
                        "--into", str(tmpPath / "nao-existe"))
    assert code == 2
    assert "destino não é um diretório" in out


def test_unknownResourceAbortsAtTheLint(tmpPath):
    code, out = runTool("build-resource.py", "NaoExisteNaSpec", "--lang", "java",
                        "--into", str(_target(tmpPath)))
    assert code == 1
    assert "UNDECLARED" in out


def test_lintComesBeforeGenerate(tmpPath):
    code, out = runTool("build-resource.py", "NaoExisteNaSpec", "--lang", "java",
                        "--into", str(_target(tmpPath)))
    assert "lint-spec" in out
    assert "generate" not in out


def test_argumentValidationComesBeforeTheLint(tmpPath):
    code, out = runTool("build-resource.py", "NaoExisteNaSpec", "--lang", "cobol",
                        "--into", str(_target(tmpPath)))
    assert code == 2
    assert "lint-spec" not in out


def test_nothingIsWrittenWhenTheLintFails(tmpPath):
    target = _target(tmpPath)
    runTool("build-resource.py", "NaoExisteNaSpec", "--lang", "java", "--into", str(target))
    assert list(target.rglob("*")) == []


@requiresGenerator
def test_pilotProducesAVerifiedArtifact(builtOnce):
    code, out, target = builtOnce("SplitProfile", "java")

    assert code == 0, out
    placed = target / ARTIFACT
    assert placed.is_file()

    source = placed.read_text(encoding="utf-8")
    assert source.count("public static SplitProfile get(") == 2
    assert source.count("public static Generator<SplitProfile> query(") == 4
    assert source.count("public static Page page(") == 4
    assert source.count("public static SplitProfile.Log get(") == 2
    assert source.count("public static Generator<SplitProfile.Log> query(") == 4
    assert source.count("public static SplitProfile.Log.Page page(") == 4
    assert source.startswith("package com.starkbank;")
    assert "{{" not in source


@requiresGenerator
def test_putAndPageGoThroughTheRestPrimitives(builtOnce):
    code, out, target = builtOnce("SplitProfile", "java")

    assert code == 0, out
    source = (target / ARTIFACT).read_text(encoding="utf-8")
    assert "Rest.put(" in source
    assert "Rest.getPage(" in source


@requiresGenerator
def test_twoRunsProduceTheSameContent(tmpPath):
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
def test_nodeProducesTheThreeArtifacts(builtOnce):
    code, out, target = builtOnce("Transaction", "node", "--advisory")

    assert code == 0, out
    for relative in NODE_ARTIFACTS:
        assert (target / relative).is_file(), relative


@requiresGenerator
def test_nodeUsesTheModuleFunctionShape(builtOnce):
    code, out, target = builtOnce("Transaction", "node", "--advisory")
    source = (target / "sdk/transaction/transaction.js").read_text(encoding="utf-8")

    assert "exports.get = async function" in source
    assert "static get(" not in source
    assert "let resource = {'class': exports.Transaction, 'name': 'Transaction'}" in source
    assert "require('starkcore').Resource" in source


@requiresGenerator
def test_nodeAppliesCheckDatetimeOnDateFields(builtOnce):
    code, out, target = builtOnce("Transaction", "node", "--advisory")
    source = (target / "sdk/transaction/transaction.js").read_text(encoding="utf-8")

    assert "check.datetime(created)" in source


@requiresGenerator
def test_nodeBarrelExportsOnlyDeclaredOperations(builtOnce):
    code, out, target = builtOnce("Transaction", "node", "--advisory")
    barrel = (target / "sdk/transaction/index.js").read_text(encoding="utf-8")

    assert "exports.create" in barrel
    assert "exports.page" in barrel
    assert "exports.delete" not in barrel


@requiresGenerator
def test_noSdkTestIsGenerated(builtOnce):
    # o teste gerado chamaria a sandbox; a validacao vive neste repo, nao no SDK alvo
    code, out, target = builtOnce("Invoice", "java", "--advisory")

    assert code == 0, out
    assert (target / "src/main/java/com/starkbank/Invoice.java").is_file()
    assert not (target / "src/test").exists()


@requiresGenerator
def test_deleteAndPdfAreEmittedWhenDeclared(builtOnce):
    code, out, target = builtOnce("Transfer", "java", "--advisory")
    source = (target / "src/main/java/com/starkbank/Transfer.java").read_text(encoding="utf-8")

    assert "public static Transfer delete(String id)" in source
    assert "public static Transfer delete(String id, User user)" in source
    assert "return Rest.delete(data, id, user);" in source

    assert "public static InputStream pdf(String id)" in source
    assert 'return Rest.getContent(data, id, "pdf", user, new HashMap<>());' in source
    assert "import java.io.InputStream;" in source


@requiresGenerator
def test_updateIsEmittedWhenDeclared(builtOnce):
    code, out, target = builtOnce("Invoice", "java", "--advisory")
    source = (target / "src/main/java/com/starkbank/Invoice.java").read_text(encoding="utf-8")

    assert "public static Invoice update(String id, Map<String, Object> patchData)" in source
    assert "return Rest.patch(data, id, patchData, user);" in source


@requiresGenerator
def test_cancelIsEmittedWhenDeclared(builtOnce):
    code, out, target = builtOnce("CorporateCard", "java", "--advisory")

    assert code == 0, out
    source = (target / "src/main/java/com/starkbank/CorporateCard.java").read_text(encoding="utf-8")
    assert "public static CorporateCard cancel(String id)" in source
    assert "public static CorporateCard update(String id, Map<String, Object> patchData)" in source
    assert "return Rest.delete(data, id, user);" in source


@requiresGenerator
def test_readOnlyResourceCarriesNoDeadImport(builtOnce):
    code, out, target = builtOnce("Balance", "java", "--advisory")

    assert code == 0, out
    source = (target / "src/main/java/com/starkbank/Balance.java").read_text(encoding="utf-8")

    assert "import com.starkbank.utils.Generator;" not in source
    assert "import java.util.ArrayList;" not in source
    assert "import java.util.List;" not in source
    assert "import java.util.Map;" in source


@requiresGenerator
def test_withoutTheFlagNoDeadInputStreamIsEmitted(builtOnce):
    code, out, target = builtOnce("Transaction", "java", "--advisory")
    source = (target / "src/main/java/com/starkbank/Transaction.java").read_text(encoding="utf-8")

    assert "InputStream" not in source
    assert "delete(" not in source


@requiresGenerator
def test_logIsEmittedAsInnerClassWhenDeclared(builtOnce):
    code, out, target = builtOnce("Invoice", "java", "--advisory")
    source = (target / "src/main/java/com/starkbank/Invoice.java").read_text(encoding="utf-8")

    assert "public final static class Log extends Resource" in source
    assert 'new ClassData(Invoice.Log.class, "InvoiceLog")' in source
    assert "public static Invoice.Log get(String id)" in source
    assert "public static Generator<Invoice.Log> query()" in source
    assert "public static Invoice.Log.Page page()" in source
    assert "public List<ErrorElement> errors;" in source
    assert "import com.starkbank.error.ErrorElement;" in source
    assert "public Invoice invoice;" in source


@requiresGenerator
def test_resourceWithoutLogGetsNoInnerClass(builtOnce):
    code, out, target = builtOnce("Transaction", "java", "--advisory")
    source = (target / "src/main/java/com/starkbank/Transaction.java").read_text(encoding="utf-8")

    assert "class Log" not in source


@requiresGenerator
def test_pageIsEmittedWhenTheSpecDeclaresIt(builtOnce):
    code, out, target = builtOnce("Invoice", "java", "--advisory")
    source = (target / "src/main/java/com/starkbank/Invoice.java").read_text(encoding="utf-8")

    assert "public static Page page()" in source
    assert "public final static class Page" in source


@requiresGenerator
def test_nodeTypesUsesDeclareModule(builtOnce):
    code, out, target = builtOnce("Transaction", "node", "--advisory")
    types = (target / "types/transaction/transaction.d.ts").read_text(encoding="utf-8")

    assert "declare module 'starkbank'" in types
    assert "export namespace transaction" in types
    assert "&lt;" not in types


def test_generatorReturningZeroWithoutOutputIsReported(tmpPath):
    fakeBin = tmpPath / "bin"
    fakeBin.mkdir()
    fake = fakeBin / "npx"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)

    target = _target(tmpPath)
    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java",
                        "--into", str(target), env={"PATH": f"{fakeBin}:/usr/bin:/bin"})

    assert code == 1
    assert "retornou 0 mas não produziu o artefato" in out
    assert "SplitProfile.java" in out


def test_nothingIsPlacedWhenTheGeneratorProducesNothing(tmpPath):
    fakeBin = tmpPath / "bin"
    fakeBin.mkdir()
    fake = fakeBin / "npx"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)

    target = _target(tmpPath)
    runTool("build-resource.py", "SplitProfile", "--lang", "java",
            "--into", str(target), env={"PATH": str(fakeBin)})

    assert list(target.rglob("*")) == []


def test_rulerIsDerivedBeforeTheGate(buildResource):
    source = (REPO_ROOT / "tools/build-resource.py").read_text(encoding="utf-8")

    assert "derive-contract.py" in source
    assert source.index("deriveRuler") < source.index("def assertGenerated")
    assert '"--contract"' in source


def test_absentUpstreamIsTheOnlyWayToSkipTheRuler(buildResource):
    source = (REPO_ROOT / "tools/build-resource.py").read_text(encoding="utf-8")

    assert "EXIT_ABSENT_UPSTREAM" in source
    assert "--allow-missing-contract" in source


def test_strictIsNotOptionalInTheWorkflow():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "build-resource.py" in workflow
    assert "--advisory" not in workflow


@requiresGenerator
def test_substitutionIsAnnouncedAsSuch(builtOnce):
    code, out, _ = builtOnce("Invoice", "java", "--advisory")

    assert code == 0, out
    assert "SUBSTITUI" in out


def test_gapFillingIsAnnouncedAsSuch(buildResource, capsys):
    buildResource.announceIntent("Widget", "java", absent=2, total=2)

    assert "preenche a lacuna" in capsys.readouterr().out


@requiresGenerator
def test_referenceShaIsRecordedInTheRun(builtOnce):
    code, out, target = builtOnce("SplitProfile", "java")

    assert code == 0, out
    assert "sdk-java @" in out


@requiresGenerator
def test_subResourceIsImportedNotQualifiedInline(builtOnce):
    code, out, target = builtOnce("Invoice", "java", "--advisory")
    assert code == 0, out

    source = (target / "src/main/java/com/starkbank/Invoice.java").read_text(encoding="utf-8")
    assert "import com.starkcore.utils.SubResource;" in source
    assert "for (SubResource " in source
    assert "for (com.starkcore.utils.SubResource" not in source


@requiresGenerator
def test_resourceWithoutPageDoesNotImportSubResource(builtOnce):
    code, out, target = builtOnce("Balance", "java", "--advisory")
    assert code == 0, out

    source = (target / "src/main/java/com/starkbank/Balance.java").read_text(encoding="utf-8")
    assert "SubResource" not in source


def test_rolesAreMatchedByNameNotByPosition(buildResource):
    pares = buildResource.plannedPairs("Transfer", "node")

    assert set(pares) == {"impl", "barrel", "types"}
    for papel, (origem, _) in pares.items():
        assert origem.startswith(f"{papel}/"), f"{papel} aponta para {origem}"

    invertidos = {papel: pares[papel] for papel in reversed(list(pares))}
    assert invertidos["impl"] == pares["impl"], "a ordem do dicionario mudou o par do papel"


def test_layoutAndGeneratorsDeclareTheSameRoles(buildResource):
    placeGenerated = _loadPlaceGenerated()
    for language, runs in buildResource.GENERATORS.items():
        layoutRoles = [role for role, _, _ in placeGenerated.LAYOUTS[language]]
        assert [run["role"] for run in runs] == layoutRoles, language


def test_objectArrayRequestsTheListImportAlone(buildResource):
    properties = buildResource.javaImports({"x-sdk-get"}, objectArray=True)

    assert "usesObjectList=true" in properties
    assert "usesList=true" not in properties, "ArrayList so entra com operacao que monta lista"
    assert "usesObjectList=true" not in buildResource.javaImports({"x-sdk-get"})


def test_onlyObjectArraysCountAsList(buildResource):
    assert buildResource.hasObjectArray("CorporateRule") is True
    assert buildResource.hasObjectArray("Balance") is False


@requiresGenerator
def test_objectArrayResourceImportsList(builtOnce):
    code, out, target = builtOnce("CorporateRule", "java", "--advisory")

    assert code == 0, out
    source = (target / "src/main/java/com/starkbank/CorporateRule.java").read_text(encoding="utf-8")
    assert "import java.util.List;" in source
    assert "import java.util.ArrayList;" not in source


@requiresGenerator
def test_dateFieldsAreStringsLikeDatetimes(builtOnce):
    code, out, target = builtOnce("UtilityPayment", "java", "--advisory")

    assert code == 0, out
    source = (target / "src/main/java/com/starkbank/UtilityPayment.java").read_text(encoding="utf-8")
    assert "public String scheduled;" in source
    assert "public LocalDate" not in source


def test_javaImportsNeverAskForTheRemovedSettingsFlag(buildResource):
    properties = buildResource.javaImports({"x-sdk-query", "x-sdk-page", "x-sdk-log"})

    assert "usesSettings=true" not in properties


@requiresGenerator
def test_relativeTargetIsResolvedAgainstTheCallersDirectory(tmpPath):
    import subprocess, sys
    target = tmpPath / "sdk-java"
    (target / "src/main/java/com/starkbank").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools/build-resource.py"), "SplitProfile", "--lang", "java",
         "--into", "sdk-java", "--advisory"],
        cwd=tmpPath, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "src/main/java/com/starkbank/SplitProfile.java").is_file()
