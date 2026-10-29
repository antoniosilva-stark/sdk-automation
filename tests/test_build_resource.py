import re
import hashlib
from pathlib import Path
import yaml
import pytest

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
    steps = workflow["jobs"]["deliver"]["steps"]

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
def test_twoRunsProduceTheSameContent(tmpPath, referenceWithout):
    target = _target(tmpPath)
    args = ("SplitProfile", "--lang", "java", "--into", str(target))
    reference = referenceWithout("java", "SplitProfile")

    first, _ = runTool("build-resource.py", *args, env=reference)
    content = (target / ARTIFACT).read_text(encoding="utf-8")
    second, _ = runTool("build-resource.py", *args, env=reference)

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
def test_existingResourceIsAnnouncedAsAParityReport(tmpPath):
    code, out = runTool("build-resource.py", "Invoice", "--lang", "java", "--into", str(_target(tmpPath)))

    assert code == 4, out
    assert "já existe" in out
    assert "nada será entregue" in out
    assert "SUBSTITUI" not in out


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
def test_relativeTargetIsResolvedAgainstTheCallersDirectory(tmpPath, referenceWithout):
    import os, subprocess, sys
    target = tmpPath / "sdk-java"
    (target / "src/main/java/com/starkbank").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools/build-resource.py"), "SplitProfile", "--lang", "java",
         "--into", "sdk-java", "--advisory"],
        cwd=tmpPath, capture_output=True, text=True,
        env={**os.environ, **referenceWithout("java", "SplitProfile")},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "src/main/java/com/starkbank/SplitProfile.java").is_file()


def _forgedJavaSdk(tmpPath: Path, generated: str, extra: str = "") -> Path:
    """SDK Java descartavel onde SplitProfile ja existe: o recurso vira substituicao, com regua derivada."""
    sdk = tmpPath / "forged-sdk-java"
    directory = sdk / "src/main/java/com/starkbank"
    directory.mkdir(parents=True)
    source = generated if not extra else generated.rstrip().rstrip("}") + extra + "\n}\n"
    (directory / "SplitProfile.java").write_text(source, encoding="utf-8")
    return sdk


@requiresGenerator
def test_aFailingGateOnAnExistingResourceStillExitsWithTheExistsCode(builtOnce, tmpPath):
    code, out, built = builtOnce("SplitProfile", "java")
    assert code == 0, out
    generated = (built / "src/main/java/com/starkbank/SplitProfile.java").read_text(encoding="utf-8")
    sdk = _forgedJavaSdk(tmpPath, generated, extra="\n    public static String onlyInProduction() { return null; }\n")
    target = tmpPath / "into"
    target.mkdir()

    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java", "--into", str(target),
                        env={"SDK_JAVA": str(sdk)})

    assert code == 4, out
    assert "CONTRACT_GAP" in out
    assert "onlyInProduction" in out
    assert "0 passaram, 1 reprovaram" in out
    assert list(target.rglob("*")) == [], "o que reprovou no gate nao pode ter sido colocado"


@requiresGenerator
def test_theParityReportCountsTheRolesThatPassedTheGate(builtOnce, tmpPath):
    code, out, built = builtOnce("SplitProfile", "java")
    assert code == 0, out
    generated = (built / "src/main/java/com/starkbank/SplitProfile.java").read_text(encoding="utf-8")
    sdk = _forgedJavaSdk(tmpPath, generated)
    target = tmpPath / "into"
    target.mkdir()

    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java", "--into", str(target),
                        env={"SDK_JAVA": str(sdk)})

    assert code == 4, out
    assert "1 passaram, 0 reprovaram" in out
    assert list(target.rglob("*")) == []


@requiresGenerator
def test_logJavadocParamNamesTheConstructorParameter(builtOnce):
    code, out, target = builtOnce("SplitProfile", "java")
    source = (target / "src/main/java/com/starkbank/SplitProfile.java").read_text(encoding="utf-8")

    assert "public Log(String created, String type, List<ErrorElement> errors, SplitProfile profile, String id)" in source
    assert "@param profile [SplitProfile]" in source
    assert "@param splitProfile [SplitProfile]" not in source


def _snapshot(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


@requiresGenerator
def test_existingResourceYieldsTheExistsCodeAndLeavesTheTargetUntouched(tmpPath):
    # Mutacao: religar o ramo "existe" para `place` cria SplitProfile.java em `into`
    # e este teste passa a falhar (codigo 0 e arquivo novo).
    target = _target(tmpPath)
    (target / "keep.txt").write_text("alvo\n", encoding="utf-8")
    before = _snapshot(target)

    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java", "--into", str(target))

    assert code == 4, out
    assert "já existe" in out
    assert _snapshot(target) == before
    assert not (target / ARTIFACT).exists()


@requiresGenerator
def test_absentResourceIsStillGeneratedAndPlacedWithExitZero(tmpPath, referenceWithout):
    target = _target(tmpPath)

    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java", "--into", str(target),
                        env=referenceWithout("java", "SplitProfile"))

    assert code == 0, out
    assert "preenche a lacuna" in out
    assert (target / ARTIFACT).is_file()


@requiresGenerator
def test_partiallyExistingResourceIsReportedAndNothingIsPlaced(tmpPath, referenceWithout):
    target = _target(tmpPath)

    code, out = runTool("build-resource.py", "Transaction", "--lang", "node", "--into", str(target),
                        env=referenceWithout("node", "Transaction", keepRoles=("impl",)))

    assert code == 4, out
    assert "existe parcialmente" in out
    # papel sem regua fica fora do gate e conta como ausente: sem isso o relatorio mentiria
    assert "0 passaram, 1 reprovaram, 2 ausentes no SDK de referência" in out
    assert list(target.rglob("*")) == []


@requiresGenerator
def test_advisoryDoesNotSoftenTheParityReportOfAnExistingResource(tmpPath):
    target = _target(tmpPath)

    # Invoice reprova o gate estrito; se --advisory afrouxasse o relatorio, o veredito viraria "passou"
    code, out = runTool("build-resource.py", "Invoice", "--lang", "java", "--into", str(target),
                        "--advisory")

    assert code == 4, out
    assert "0 passaram, 1 reprovaram" in out
    assert list(target.rglob("*")) == []


def test_existsCodeDoesNotCollideWithTheOtherExitCodes(buildResource):
    taken = {0, 1, 2, buildResource.EXIT_ABSENT_UPSTREAM, buildResource.EXIT_NO_EXECUTABLE}

    assert buildResource.EXIT_EXISTS == 4
    assert buildResource.EXIT_EXISTS not in taken


def _stagedSpec(buildResource, tmpPath: Path, resource: str) -> dict:
    staged = buildResource.stageJavaDocs(resource, tmpPath)
    return yaml.safe_load(Path(staged["spec"]).read_text(encoding="utf-8"))["components"]["schemas"][resource]


def test_stagedCopyCarriesTheRenderedDocsBesideTheFlagsOfTheResource(buildResource, tmpPath):
    entry = _stagedSpec(buildResource, tmpPath, "SplitProfile")

    assert entry["x-sdk-java-docs"]["getPlain"][0] == "Retrieve a specific SplitProfile"
    assert entry["x-sdk-java-docs"]["dataDoc"][0] == "SplitProfile object"
    assert entry["x-sdk-get"] is True


def test_stagingNeverWritesTheSpecOrTheSchemasOfTheRepo(buildResource, tmpPath):
    before = _snapshot(REPO_ROOT / "apis")

    buildResource.stageJavaDocs("SplitProfile", tmpPath)

    assert _snapshot(REPO_ROOT / "apis") == before
    assert "x-sdk-java-docs" not in (REPO_ROOT / "apis/spec-v2.openapi.yaml").read_text(encoding="utf-8")


def test_stagedCopyKeepsTheOriginalDocsAndTheSchemasForTheRefs(buildResource, tmpPath):
    staged = buildResource.stageJavaDocs("SplitProfile", tmpPath)

    copiedSchema = Path(staged["spec"]).parent / "schemas/splitprofile.yaml"
    assert copiedSchema.read_bytes() == (REPO_ROOT / "apis/schemas/splitprofile.yaml").read_bytes()


def test_stagingReportsTheIrregularPluralInsteadOfGuessingIt(buildResource, tmpPath):
    staged = buildResource.stageJavaDocs("BoletoHolmes", tmpPath)

    assert any("BoletoHolmes" in warning for warning in staged["warnings"])


@requiresGenerator
def test_aBuildLeavesTheSpecAndSchemasByteIdenticalAndRemovesItsCopy(tmpPath, referenceWithout):
    before = _snapshot(REPO_ROOT / "apis")
    scratch = tmpPath / "scratch"
    scratch.mkdir()

    code, out = runTool("build-resource.py", "SplitProfile", "--lang", "java", "--into", str(_target(tmpPath)),
                        env={**referenceWithout("java", "SplitProfile"), "TMPDIR": str(scratch)})

    assert code == 0, out
    assert _snapshot(REPO_ROOT / "apis") == before
    assert list(scratch.glob("build-apis-*")) == []
    assert list(scratch.glob("build-resource-*")) == []


JAVA_SOURCES = "src/main/java/com/starkbank"
PYTHON_RESIDUE = (r"datetime\.\w", r"timedelta", r"\bNone\b", r"\bTrue\b", r"\bFalse\b", r'\\"', r"starkbank\.user\b",
                  r"User\.defaultUser")
USER_LINE = ("@param user [Organization/Project object, default null]: Organization or Project object."
             " Not necessary if starkbank.Settings.user was set before function call")


def _generatedSource(builtOnce, resource: str, *extra: str) -> str:
    code, out, target = builtOnce(resource, "java", *extra)
    assert code == 0, out
    return (target / JAVA_SOURCES / f"{resource}.java").read_text(encoding="utf-8")


def _javadocBefore(source: str, signature: str) -> list[str]:
    """Linhas do Javadoc que fecha logo acima da assinatura, ignorando anotacoes."""
    head = source[:source.index(signature)].rstrip().splitlines()
    while head and head[-1].strip().startswith("@") and not head[-1].strip().startswith("@param"):
        head.pop()
    assert head and head[-1].strip() == "*/", f"sem Javadoc acima de {signature}"
    block = []
    while head[-1].strip() != "/**":
        block.append(head.pop().strip())
        if not head:
            break
    return [line.removeprefix("* ").removeprefix("*") for line in reversed(block[1:])]


@requiresGenerator
def test_generatedClassHasNoClassJavadocAndTheDocLivesOnTheDataField(builtOnce):
    source = _generatedSource(builtOnce, "SplitProfile")

    declaration = source.index("public final class SplitProfile")
    assert "/**" not in source[:declaration]
    doc = _javadocBefore(source, "static ClassData data = new ClassData(SplitProfile.class")
    assert doc[0] == "SplitProfile object"
    assert doc.count("Parameters:") == 1


@requiresGenerator
def test_generatedFieldDocHasOneLinePerFieldWithTheArrayDescription(builtOnce):
    source = _generatedSource(builtOnce, "SplitProfile")

    doc = _javadocBefore(source, "static ClassData data = new ClassData(SplitProfile.class")
    assert "tags [list of strings]: list of strings for tagging" in doc
    assert 'created [string]: creation datetime for the SplitProfile. ex: "2020-03-10 10:30:00.000000+00:00"' in doc


@requiresGenerator
def test_generatedConstructorsAreDocumentedExceptTheEmptyOne(builtOnce):
    source = _generatedSource(builtOnce, "SplitProfile")

    allArgs = _javadocBefore(source, "public SplitProfile(String interval, Integer delay")
    mapDoc = _javadocBefore(source, "public SplitProfile(Map<String, Object> data)")
    assert "@param interval [string]: frequency of transfer, default \"week\". Options: \"day\", \"week\", \"month\"" in allArgs
    assert "@param data map of properties for the creation of the SplitProfile" in mapDoc
    assert "Attributes (return-only):" in mapDoc
    emptyConstructor = source.index("public SplitProfile() {")
    assert not source[:emptyConstructor].rstrip().endswith("*/")


@requiresGenerator
@pytest.mark.parametrize("resource,extra", [("SplitProfile", ()), ("Transfer", ("--advisory",)),
                                            ("VerifiedTransfer", ("--advisory",)), ("Boleto", ("--advisory",)),
                                            ("Invoice", ("--advisory",)), ("Event", ("--advisory",)),
                                            ("Webhook", ("--advisory",))])
def test_generatedJavadocHasNoPythonResidueNorEmptyDescription(builtOnce, resource, extra):
    source = _generatedSource(builtOnce, resource, *extra)

    for number, line in enumerate(source.splitlines(), start=1):
        if not line.strip().startswith("*"):
            continue
        assert not any(re.search(pattern, line) for pattern in PYTHON_RESIDUE), f"{resource}:{number}: {line}"
        assert not re.search(r"\]\s*(, optional)?:\s*$", line), f"{resource}:{number}: descricao vazia: {line}"
        assert line == line.rstrip(), f"{resource}:{number}: espaco no fim"


@requiresGenerator
def test_generatedQueryAndPageListTheirFiltersAndTheDecidedUserText(builtOnce):
    source = _generatedSource(builtOnce, "SplitProfile")

    query = _javadocBefore(source, "public static Generator<SplitProfile> query(Map<String, Object> params, User user)")
    page = _javadocBefore(source, "public static Page page(Map<String, Object> params, User user)")
    assert "@param params map of parameters for the query" in query
    assert "limit [integer, default null]: maximum number of objects to be retrieved. Unlimited if null. ex: 35" in query
    assert 'after [string, default null] date filter for objects created or updated only after specified date. ex: "2020-03-10"' in query
    assert "@param params parameters of the query" in page
    assert "cursor [string, default null]: cursor returned on the previous page function call" in page
    assert any(line.startswith("tags [list of strings, default null]") for line in page)
    assert USER_LINE in query and USER_LINE in page


@requiresGenerator
def test_generatedOverloadsListOnlyTheirOwnParameters(builtOnce):
    source = _generatedSource(builtOnce, "SplitProfile")

    noArguments = _javadocBefore(source, "public static Generator<SplitProfile> query() throws Exception")
    onlyUser = _javadocBefore(source, "public static Generator<SplitProfile> query(User user)")
    onlyParams = _javadocBefore(source, "public static Generator<SplitProfile> query(Map<String, Object> params) ")
    assert "Parameters:" not in noArguments
    assert [line for line in onlyUser if line.startswith("@param")] == [USER_LINE]
    assert not any(line.startswith("@param user") for line in onlyParams)


@requiresGenerator
def test_generatedTitlesFollowTheVerbPhrases(builtOnce):
    source = _generatedSource(builtOnce, "SplitProfile")

    titles = {
        "public static List<SplitProfile> put(List<?> splitProfiles) ": "Create or update a SplitProfile",
        "public static SplitProfile get(String id) ": "Retrieve a specific SplitProfile",
        "public static Generator<SplitProfile> query() ": "Retrieve SplitProfiles",
        "public static Page page() ": "Retrieve paged SplitProfiles",
        "public static SplitProfile.Log get(String id) ": "Retrieve a specific SplitProfile Log",
        "public static Generator<SplitProfile.Log> query() ": "Retrieve SplitProfile Logs",
        "public static SplitProfile.Log.Page page() ": "Retrieve paged SplitProfile.Logs",
    }
    for signature, title in titles.items():
        assert _javadocBefore(source, signature)[0] == title, signature


@requiresGenerator
def test_generatedReturnBlockDescribesThePageObject(builtOnce):
    source = _generatedSource(builtOnce, "SplitProfile")

    doc = _javadocBefore(source, "public static Page page() ")
    assert doc[-5:] == [
        "Return:",
        "@return SplitProfile.Page object:",
        "SplitProfile.Page.splitProfiles: list of SplitProfile objects with updated attributes",
        "SplitProfile.Page.cursor: cursor to retrieve the next page of SplitProfile objects",
        "@throws Exception error in the request",
    ]


@requiresGenerator
def test_generatedLogOperationsKeepTheirFiltersAndLogReturn(builtOnce):
    source = _generatedSource(builtOnce, "SplitProfile")

    doc = _javadocBefore(source, "public static Generator<SplitProfile.Log> query(Map<String, Object> params, User user)")
    assert any(line.startswith("profileIds [list of strings, default null]") for line in doc)
    assert "@return generator of SplitProfile Log objects with updated attributes" in doc
    assert USER_LINE in doc


@requiresGenerator
def test_generatedResourceWithLogAndPdfDocumentsEveryOperation(builtOnce):
    source = _generatedSource(builtOnce, "Transfer", "--advisory")

    assert _javadocBefore(source, "public static List<Transfer> create(List<?> transfers) ")[0] == "Create Transfers"
    pdf = _javadocBefore(source, "public static InputStream pdf(String id, User user)")
    assert pdf[0] == "Retrieve a specific Transfer pdf file"
    assert USER_LINE in pdf
    logQuery = _javadocBefore(source, "public static Generator<Transfer.Log> query(Map<String, Object> params) ")
    assert any(line.startswith("types [list of strings, default null]") for line in logQuery)


@requiresGenerator
def test_generatedCreateOnlyResourceDocumentsItsCreate(builtOnce):
    source = _generatedSource(builtOnce, "VerifiedTransfer", "--advisory")

    doc = _javadocBefore(source, "public static List<VerifiedTransfer> create(List<?> verifiedTransfers) ")
    assert doc[0] == "Create VerifiedTransfers"
    assert "@param verifiedTransfers [list of VerifiedTransfer objects or HashMaps]: " \
           "list of VerifiedTransfer objects to be created in the API" in doc
    assert "Retrieve" not in source


def _gateCommand(buildResource, monkeypatch, tmpPath, **options) -> list[str]:
    commands = []
    monkeypatch.setattr(buildResource, "runStep", lambda label, command, **kwargs: (commands.append(command), (0, ""))[1])
    buildResource.assertGenerated(tmpPath / "Widget.java", "java", "main", None, True, False, **options)
    return commands[0]


def test_theGateCommandCarriesTheStyleFlagOnlyWhenAsked(buildResource, monkeypatch, tmpPath):
    assert "--style" in _gateCommand(buildResource, monkeypatch, tmpPath, style=True)
    assert "--style" not in _gateCommand(buildResource, monkeypatch, tmpPath)


def _gateChecks(out: str) -> str:
    return next(line for line in out.splitlines() if "verificações executadas" in line)


@requiresGenerator
def test_creationRunsTheStyleChecksInTheStrictGate(builtOnce):
    code, out, _ = builtOnce("SplitProfile", "java")
    assert code == 0, out
    assert "estilo" in _gateChecks(out)


@requiresGenerator
def test_advisoryCreationDoesNotRunTheStyleChecks(builtOnce):
    code, out, _ = builtOnce("Transfer", "java", "--advisory")
    assert code == 0, out
    assert "estilo" not in _gateChecks(out)


@requiresGenerator
def test_theParityReportOfAnExistingResourceDoesNotRunTheStyleChecks(tmpPath):
    code, out = runTool("build-resource.py", "Invoice", "--lang", "java", "--into", str(_target(tmpPath)))

    assert code == 4, out
    assert "estilo" not in _gateChecks(out)


@requiresGenerator
def test_styleWarningsOfACreatedResourceAreCountedAndDoNotBlockTheBuild(builtOnce):
    code, out, target = builtOnce("Transfer", "java")

    assert code == 0, out
    assert "STYLE_PYTHON_CTOR" in out
    assert "aviso(s) de estilo que não bloqueiam" in out
    assert (target / JAVA_SOURCES / "Transfer.java").is_file()
