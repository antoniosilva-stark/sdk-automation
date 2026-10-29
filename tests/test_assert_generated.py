import shutil
import pytest
from pathlib import Path

from conftest import JAVA_SDK, NODE_SDK, REPO_ROOT, requiresJavaSdk, requiresJavac, requiresNodeSdk, runTool

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


def test_cleanFilePasses(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target))
    assert code == 0
    assert "[OK]" in out


def test_reportsTheChecksItRan(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target))
    assert "verificações executadas" in out
    assert "estrutura" in out


@requiresJavac
def test_syntaxIsReportedWhenJavacWorks(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target))
    assert "sintaxe (javac)" in out


def test_syntaxIsNotReportedWhenJavacIsBroken(tmpPath):
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


@requiresJavaSdk
@pytest.mark.parametrize("name", ["Invoice.java", "Transaction.java"])
def test_realProductionJavaPasses(tmpPath, name):
    target = _copyReal(tmpPath, JAVA_SDK / name)
    code, out = runTool("assert-generated.py", str(target))
    assert code == 0, out


@requiresNodeSdk
@pytest.mark.parametrize("name", ["transfer/transfer.js", "transaction/transaction.js"])
def test_realProductionNodePasses(tmpPath, name):
    target = _copyReal(tmpPath, NODE_SDK / name)
    code, out = runTool("assert-generated.py", str(target), "--lang", "node")
    assert code == 0, out


def test_wrappedSignatureIsNotAFalsePositive(assertGenerated):
    source = "    public Invoice(Number amount, String due,\n                   String taxId,  String name) {\n"
    assert assertGenerated.checkEmptyTokens(source, "x.java") == []


def test_methodWithoutArgumentsIsNotAFalsePositive(assertGenerated):
    source = "    public static Generator<Invoice> query() throws Exception {\n"
    assert assertGenerated.checkEmptyTokens(source, "x.java") == []


def test_leftoverPlaceholderFails(tmpPath):
    target = _write(tmpPath, "Widget.java", "public class {{classname}} extends Resource {}\n")
    code, out = runTool("assert-generated.py", str(target))
    assert code == 1
    assert "PLACEHOLDER" in out


def test_droppedIdentifierFails(assertGenerated):
    issues = assertGenerated.checkEmptyTokens("        for (Object  : items) {\n", "x.java")
    assert len(issues) == 1
    assert issues[0].code == "EMPTY_TOKEN"


def test_emptySlotFails(assertGenerated):
    issues = assertGenerated.checkEmptyTokens("            if ( instanceof Map)\n", "x.java")
    assert len(issues) == 1


def test_emptyArgumentFails(assertGenerated):
    issues = assertGenerated.checkEmptyTokens("        return Rest.put(data, , user);\n", "x.java")
    assert len(issues) == 1


def test_missingFileReturnsTwo():
    code, out = runTool("assert-generated.py", "/tmp/nao-existe-mesmo.java")
    assert code == 2
    assert "[ERROR]" in out


def test_contractIgnoresComments(assertGenerated, tmpPath):
    contract = _write(tmpPath, "x.contract", "# comentario\n\n## secao\nsignature public static void go()\n")
    parsed = assertGenerated.parseContract(contract)
    assert parsed["signature"] == ["public static void go()"]


def test_contractGapDoesNotFailWithoutStrict(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "signature public static void ausente()\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract))
    assert code == 0
    assert "gap(s) de contrato" in out


def test_contractGapFailsWithStrict(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "signature public static void ausente()\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")
    assert code == 1
    assert "CONTRACT_GAP" in out


def test_presentSignatureIsNotAGap(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "signature public static Widget get(String id)\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")
    assert code == 0, out


def test_duplicateImportFails(assertGenerated):
    source = "import java.util.List;\nimport java.util.List;\nclass X { List<String> a; }\n"
    issues = assertGenerated.checkDeadImports(source, "X.java")
    assert len(issues) == 1
    assert issues[0].code == "DEAD_IMPORT"
    assert "duplicado" in issues[0].message


def test_unusedImportFails(assertGenerated):
    source = "import java.time.OffsetDateTime;\nclass X { String a; }\n"
    issues = assertGenerated.checkDeadImports(source, "X.java")
    assert len(issues) == 1
    assert "nao usado: java.time.OffsetDateTime" in issues[0].message


def test_usedImportIsNotFlagged(assertGenerated):
    source = "import java.util.List;\nimport java.util.Map;\nclass X { List<String> a; Map<String,Object> b; }\n"
    assert assertGenerated.checkDeadImports(source, "X.java") == []


def test_typeImportUsedOnlyInGenericsPasses(assertGenerated):
    source = "import java.util.ArrayList;\nclass X { void f() { new ArrayList<>(); } }\n"
    assert assertGenerated.checkDeadImports(source, "X.java") == []


def test_wildcardIsNotEvaluated(assertGenerated):
    source = "import java.util.*;\nclass X { List<String> a; }\n"
    assert assertGenerated.checkDeadImports(source, "X.java") == []


@requiresJavaSdk
def test_deadImportInRealJavaIsDetected(tmpPath):
    target = _copyReal(tmpPath, JAVA_SDK / "Transfer.java")
    code, out = runTool("assert-generated.py", str(target))

    assert code == 1
    assert "GsonEvent" in out


CONTRACT_DIR = Path(__file__).resolve().parent / "contract"


def test_everyKindUsedInContractsIsDeclared(assertGenerated):
    usados = set()
    for path in sorted(CONTRACT_DIR.glob("*.contract")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            usados.add(line.split(" ")[0])
    declarados = set(assertGenerated.CONTRACT_KINDS)
    desconhecidos = usados - declarados
    assert desconhecidos == set(), f"kind usado e nao declarado: {sorted(desconhecidos)}"


def test_inlineCommentIsNotPartOfTheExpectedString(assertGenerated, tmpPath):
    contract = _write(tmpPath, "x.contract", "field public Long amount;  # anotacao\n")
    assert assertGenerated.parseContract(contract)["field"] == ["public Long amount;"]


def test_unknownKindFailsTheParse(assertGenerated, tmpPath):
    contract = _write(tmpPath, "x.contract", "shape isto nao e um kind\n")
    with pytest.raises(ValueError, match="kind desconhecido"):
        assertGenerated.parseContract(contract)


def test_kindWithoutValueFailsTheParse(assertGenerated, tmpPath):
    contract = _write(tmpPath, "x.contract", "signature\n")
    with pytest.raises(ValueError, match="sem valor"):
        assertGenerated.parseContract(contract)


def test_invalidContractReturnsTwoOnTheCli(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "inexistente alguma coisa\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract))
    assert code == 2
    assert "contrato inválido" in out


def test_noMessageClaimsACheckItDoesNotRun(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target))

    assert code == 0, out
    assert "roda no CI" not in out
    assert "compilação contra o SDK real" in out
    assert "fora do alcance desta verificação" in out


def test_fieldPresentInTheSourceIsNotAGap(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "field public String status;\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")
    assert code == 0, out


def test_missingFieldIsAGap(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "field public List<Widget.Rule> rules;\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")
    assert code == 1
    assert "field ausente" in out


def test_divergentTypeIsAGapAndSaysWhatCameOut(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "field public long status;\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")

    assert code == 1
    assert "field divergente" in out
    assert "public long status;" in out
    assert "public String status;" in out


def _strictCheck(tmpPath: Path, source: str, entry: str):
    target = _write(tmpPath, "Widget.java", source)
    contract = _write(tmpPath, "widget.contract", entry + "\n")
    return runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")


def test_javaParameterNameDoesNotDecideASignature(tmpPath):
    code, out = _strictCheck(tmpPath, CLEAN_JAVA, "signature public static Widget get(String key)")
    assert code == 0, out


def test_javaParameterTypeStillDecidesASignature(tmpPath):
    code, out = _strictCheck(tmpPath, CLEAN_JAVA, "signature public static Widget get(Long id)")
    assert code == 1
    assert "CONTRACT_GAP" in out


def test_javaParameterOrderStillDecidesASignature(tmpPath):
    code, out = _strictCheck(tmpPath, CLEAN_JAVA, "signature public static Widget get(User user, String id)")
    assert code == 1
    assert "CONTRACT_GAP" in out


def test_javaParameterNameDoesNotDecideAConstructor(tmpPath):
    code, out = _strictCheck(tmpPath, CLEAN_JAVA, "constructor public Widget(String key, String state)")
    assert code == 0, out


def test_javaGenericParameterWithACommaIsOneParameter(tmpPath):
    source = CLEAN_JAVA.replace(
        "    public static Widget get(String id) throws Exception {",
        "    public static Widget find(Map<String, Object> params) throws Exception {\n"
        "        return null;\n    }\n\n"
        "    public static Widget get(String id) throws Exception {",
    )
    code, out = _strictCheck(tmpPath, source, "signature public static Widget find(Map<String, Object> filters)")
    assert code == 0, out

    code, out = _strictCheck(tmpPath, source, "signature public static Widget find(Map<String, Long> filters)")
    assert code == 1


def test_fieldWithPrefixNameDoesNotSatisfyTheContract(tmpPath):
    source = CLEAN_JAVA.replace("public String status;", "public String statusCode;")
    target = _write(tmpPath, "Widget.java", source)
    contract = _write(tmpPath, "widget.contract", "field public String status;\n")
    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract), "--strict")

    assert code == 1
    assert "field ausente" in out


def test_todoNoLongerParsesAsAKind(assertGenerated, tmpPath):
    contract = _write(tmpPath, "widget.contract", "todo field public long status;\n")
    with pytest.raises(ValueError, match="kind desconhecido"):
        assertGenerated.parseContract(contract)


def test_missingRequireIsAGap(tmpPath):
    target = _write(tmpPath, "widget.js", "const a = 1;\n")
    contract = _write(tmpPath, "widget.contract", "require const rest = require('../utils/rest.js')\n")
    code, out = runTool("assert-generated.py", str(target), "--lang", "node",
                        "--contract", str(contract), "--strict")
    assert code == 1
    assert "require ausente" in out


def test_everyFieldInRealContractsEndsWithSemicolon(assertGenerated):
    contracts = sorted(Path(assertGenerated.CONTRACT_DIR).glob("*.contract"))
    assert contracts

    for contract in contracts:
        entries = assertGenerated.parseContract(contract)
        for entry in entries["field"]:
            assert entry.endswith(";"), f"{contract.name}: {entry}"


def test_fieldCoverageReachesResourcesWithJavaContract(assertGenerated):
    for name in ("java-transaction", "java-invoice"):
        entries = assertGenerated.parseContract(Path(assertGenerated.CONTRACT_DIR) / f"{name}.contract")
        declared = [e for e in entries["field"] if e.startswith("public ")]
        assert len(declared) >= 5, f"{name}: {len(declared)} campos declarados"


def test_waiverNeedsAReason(assertGenerated, tmpPath):
    waivers = _write(tmpPath, "java.waivers", "[widget/main]\nfield public long status;\n")

    with pytest.raises(ValueError, match="sem motivo"):
        assertGenerated.parseWaivers(waivers)


def test_waiverRejectsWildcard(assertGenerated, tmpPath):
    waivers = _write(tmpPath, "java.waivers", "[widget/main]\nfield public * status;  # motivo\n")

    with pytest.raises(ValueError, match="curinga"):
        assertGenerated.parseWaivers(waivers)


def test_waiverNeedsASection(assertGenerated, tmpPath):
    waivers = _write(tmpPath, "java.waivers", "field public long status;  # motivo\n")

    with pytest.raises(ValueError, match="seção"):
        assertGenerated.parseWaivers(waivers)


def test_waiverIsScopedByResourceAndRole(assertGenerated, tmpPath):
    waivers = _write(
        tmpPath, "java.waivers",
        "[widget/main]\nfield public long status;  # motivo do main\n\n"
        "[widget/test]\nsignature public void testCreate()  # motivo do test\n",
    )
    parsed = assertGenerated.parseWaivers(waivers)

    assert [entry["value"] for entry in parsed[("widget", "main")]] == ["public long status;"]
    assert parsed[("widget", "test")][0]["reason"] == "motivo do test"


def test_waivedGapIsAnnouncedNotBlocking(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "field public long status;\n")
    waivers = _write(tmpPath, "java.waivers", "[widget/main]\nfield public long status;  # primitivo nao alcancavel\n")

    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract),
                        "--waivers", str(waivers), "--role", "main", "--strict")

    assert code == 0, out
    assert "dispensado" in out
    assert "primitivo nao alcancavel" in out


def test_gapWithoutWaiverStillBlocks(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "field public long status;\n")
    waivers = _write(tmpPath, "java.waivers", "[widget/main]\nfield public String other;  # outro item\n")

    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract),
                        "--waivers", str(waivers), "--role", "main", "--strict")

    assert code == 1
    assert "field divergente" in out or "field ausente" in out


def test_unusedWaiverIsReported(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "field public String status;\n")
    waivers = _write(tmpPath, "java.waivers", "[widget/main]\nfield public String status;  # ja resolvido\n")

    code, out = runTool("assert-generated.py", str(target), "--contract", str(contract),
                        "--waivers", str(waivers), "--role", "main", "--strict")

    assert code == 0, out
    assert "dispensa não utilizada" in out
    assert "public String status;" in out


def test_realWaiverFilesParseAndCarryReasons(assertGenerated):
    files = sorted(Path(assertGenerated.WAIVER_DIR).glob("*.waivers"))
    assert files

    total = 0
    for path in files:
        for entries in assertGenerated.parseWaivers(path).values():
            for entry in entries:
                assert entry["reason"], f"{path.name}: {entry['value']} sem motivo"
                total += 1

    assert total >= 40


def test_roleResolvesItsOwnContract(assertGenerated, tmpPath):
    resolved = {
        role: assertGenerated.resolveContract(Path("Transfer.js"), "node", None, role)
        for role in ("impl", "barrel", "types")
    }
    assert all(path is not None for path in resolved.values()), resolved
    assert len({path.name for path in resolved.values()}) == 3, resolved


def test_withoutRoleItFallsBackToTheResourceContract(assertGenerated):
    resolved = assertGenerated.resolveContract(Path("Invoice.java"), "java", None, None)
    assert resolved is not None
    assert resolved.name == "java-invoice.contract"


def test_roleWithoutOwnContractFallsBack(assertGenerated):
    resolved = assertGenerated.resolveContract(Path("Invoice.java"), "java", None, "main")
    assert resolved.name == "java-invoice.contract"


def test_missingRulerBlocks(tmpPath):
    target = _write(tmpPath, "Inexistente.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target), "--strict")

    assert code == 1
    assert "MISSING_CONTRACT" in out


def test_missingRulerIsAllowedOnlyWhenDeclared(tmpPath):
    target = _write(tmpPath, "Inexistente.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target), "--strict", "--allow-missing-contract")

    assert code == 0, out
    assert "recurso sem arquivo real no SDK alvo" in out


def test_missingRulerWithoutStrictIsOnlyAWarning(tmpPath):
    target = _write(tmpPath, "Inexistente.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target))

    assert code == 0, out
    assert "sem contrato" in out


def test_noContractStillCarriesATodo():
    carregam = [path.name for path in sorted(CONTRACT_DIR.glob("*.contract"))
                if any(line.startswith("todo ")
                       for line in path.read_text(encoding="utf-8").splitlines())]
    assert carregam == [], f"todo sobrevivente, migre para tests/waivers: {carregam}"


def test_buildResourcePassesStrictAndRole(buildResource, tmpPath):
    source = (REPO_ROOT / "tools/build-resource.py").read_text(encoding="utf-8")
    assert '"--strict"' in source
    assert '"--role", role' in source


def test_waiverDoesNotApplyWhenSubstitutingProduction(tmpPath):
    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    contract = _write(tmpPath, "widget.contract", "declaration public final class Widget extends Resource\n")
    waivers = _write(tmpPath, "java.waivers",
                     "[widget/main]\ndeclaration public final class Widget extends Resource  # o template nao emite final\n")

    lacuna, out = runTool("assert-generated.py", str(target), "--contract", str(contract),
                          "--waivers", str(waivers), "--strict")
    assert lacuna == 0, out
    assert "dispensado" in out

    substituicao, out = runTool("assert-generated.py", str(target), "--contract", str(contract),
                                "--waivers", str(waivers), "--strict", "--substitution")
    assert substituicao == 1
    assert "dispensa não vale em substituição" in out


def test_nodeSyntaxIsCheckedLikeJava(tmpPath):
    target = _write(tmpPath, "widget.js", "exports.get = async function ( { \n")
    code, out = runTool("assert-generated.py", str(target), "--lang", "node",
                        "--allow-missing-contract", "--strict")

    assert code == 1
    assert "SYNTAX" in out


def test_validNodeSourcePassesTheSyntaxCheck(tmpPath):
    target = _write(tmpPath, "widget.js", "const rest = require('../utils/rest.js');\nexports.get = async function (id) { return id; };\n")
    code, out = runTool("assert-generated.py", str(target), "--lang", "node",
                        "--allow-missing-contract", "--strict")

    assert code == 0, out
    assert "sintaxe (node)" in out


@requiresJavac
def test_javacNeverWritesToASharedFixedDirectory(assertGenerated, tmpPath):
    import tempfile
    source = tmpPath / "Widget.java"
    source.write_text("package com.starkbank;\npublic class Widget {}\n", encoding="utf-8")
    before = set(Path(tempfile.gettempdir()).glob("assert-generated*"))

    ok, issues = assertGenerated.javacSyntax(source)

    assert ok and issues == []
    assert set(Path(tempfile.gettempdir()).glob("assert-generated*")) == before, "javac deixou saida para tras"


def test_signatureOnlyInALineCommentIsAGap(tmpPath):
    source = CLEAN_JAVA.replace(
        "    public static Widget get(String id) throws Exception {",
        "    // public static Widget find(String id)\n    public static Widget get(String id) throws Exception {",
    )
    code, out = _strictCheck(tmpPath, source, "signature public static Widget find(String id)")
    assert code == 1, out
    assert "signature ausente" in out


def test_fieldOnlyInAJavadocIsAGap(tmpPath):
    source = CLEAN_JAVA.replace(
        "    public String status;",
        "    /**\n     * public long amount;\n     */\n    public String status;",
    )
    code, out = _strictCheck(tmpPath, source, "field public long amount;")
    assert code == 1, out
    assert "field ausente" in out


def test_entryOnlyInsideABlockCommentIsAGap(tmpPath):
    source = CLEAN_JAVA.replace("    public String status;", "    /* public long amount; */\n    public String status;")
    code, out = _strictCheck(tmpPath, source, "field public long amount;")
    assert code == 1, out


def test_commentMarkerInsideAStringDoesNotHideCode(tmpPath):
    source = CLEAN_JAVA.replace(
        "    public String status;",
        '    public static final String url = "http://x";\n    public String status;',
    )
    code, out = _strictCheck(tmpPath, source, "field public String status;")
    assert code == 0, out


def test_entryInCodeStillPassesWhenAlsoCommented(tmpPath):
    source = CLEAN_JAVA.replace(
        "    public String status;",
        "    // public String status;\n    public String status;",
    )
    code, out = _strictCheck(tmpPath, source, "field public String status;")
    assert code == 0, out


def test_nodeRequireOnlyInACommentIsAGap(tmpPath):
    target = _write(tmpPath, "widget.js", "// const rest = require('../utils/rest.js')\nconst a = 1;\n")
    contract = _write(tmpPath, "widget.contract", "require const rest = require('../utils/rest.js')\n")
    code, out = runTool("assert-generated.py", str(target), "--lang", "node",
                        "--contract", str(contract), "--strict")
    assert code == 1, out
    assert "require ausente" in out


def test_javaSignatureOnlyInABlockCommentIsAGap(tmpPath):
    source = CLEAN_JAVA.replace(
        "    public static Widget get(String id) throws Exception {",
        "    /*\n    public static Widget find(String id) throws Exception {\n    */\n    public static Widget get(String id) throws Exception {",
    )
    code, out = _strictCheck(tmpPath, source, "signature public static Widget find(String id)")
    assert code == 1, out


def test_javaConstructorOnlyInABlockCommentIsAGap(tmpPath):
    source = CLEAN_JAVA.replace(
        "    public Widget(String id, String status) {",
        "    /*\n    public Widget(String id, String status, long amount) {\n    */\n    public Widget(String id, String status) {",
    )
    code, out = _strictCheck(tmpPath, source, "constructor public Widget(String id, String status, long amount)")
    assert code == 1, out


def test_gapForACommentedFieldDoesNotClaimItWasGenerated(tmpPath):
    source = CLEAN_JAVA.replace("    public String status;", "    // public long amount;\n    public String status;")
    code, out = _strictCheck(tmpPath, source, "field public long amount;")
    assert code == 1
    assert "field ausente" in out
    assert "divergente" not in out


def test_unclosedQuoteInARegexDoesNotSwallowTheRestOfTheFile(tmpPath):
    target = _write(tmpPath, "widget.js", "const r = /[\"']/;\n// const rest = require('x')\n")
    contract = _write(tmpPath, "widget.contract", "require const rest = require('x')\n")
    code, out = runTool("assert-generated.py", str(target), "--lang", "node",
                        "--contract", str(contract), "--strict")
    assert code == 1, out


@requiresJavac
def test_unclosedStringLiteralIsASyntaxError(tmpPath):
    source = CLEAN_JAVA.replace("public String status;", 'public String status = "abc;')
    target = _write(tmpPath, "Widget.java", source)
    code, out = runTool("assert-generated.py", str(target))
    assert code == 1, out
    assert "SYNTAX" in out


@requiresJavac
@pytest.mark.parametrize("broken", ["int x = ;", "else { }", "char c = 'ab';", "/* aberto"])
def test_anyParseErrorIsASyntaxError(tmpPath, broken):
    source = CLEAN_JAVA.replace("public String status;", broken)
    target = _write(tmpPath, "Widget.java", source)
    code, out = runTool("assert-generated.py", str(target))
    assert code == 1, out
    assert "SYNTAX" in out


@requiresJavac
def test_unresolvedSymbolsAreNotSyntaxErrors(tmpPath):
    source = CLEAN_JAVA.replace("public String status;", "public Desconhecido status;")
    target = _write(tmpPath, "Widget.java", source)
    code, out = runTool("assert-generated.py", str(target))
    assert code == 0, out


def test_javacFailingWithoutDiagnosticBlocks(tmpPath):
    fakeBin = tmpPath / "bin"
    fakeBin.mkdir()
    fake = fakeBin / "javac"
    fake.write_text('#!/bin/sh\n[ "$1" = "-version" ] && exit 0\nexit 1\n', encoding="utf-8")
    fake.chmod(0o755)

    target = _write(tmpPath, "Widget.java", CLEAN_JAVA)
    code, out = runTool("assert-generated.py", str(target), env={"PATH": str(fakeBin)})
    assert code == 1, out
    assert "SYNTAX" in out


@requiresJavac
def test_modernSyntaxWithUnresolvedTypesPasses(tmpPath):
    source = """package com.starkbank;

import com.starkbank.utils.Missing;

public record Widget(String id) {
    static final String TEXT = \"\"\"
        bloco // com barras
        \"\"\";

    static Object pick(Object value) {
        Runnable task = () -> Missing.run();
        return value instanceof String text ? text : task;
    }
}
"""
    target = _write(tmpPath, "Widget.java", source)
    code, out = runTool("assert-generated.py", str(target))
    assert code == 0, out


def _legacyNamed(tmpPath: Path) -> Path:
    """Artefato cujo nome casa com a fixture legada tests/contract/java-transaction.contract."""
    return _write(tmpPath, "Transaction.java", CLEAN_JAVA.replace("Widget", "Transaction"))


def test_legacyFixtureIsNotUsedWhenTheMissingContractIsAllowed(tmpPath):
    target = _legacyNamed(tmpPath)
    code, out = runTool("assert-generated.py", str(target), "--lang", "java", "--strict",
                        "--allow-missing-contract")
    assert code == 0, out
    assert "CONTRACT_GAP" not in out


def test_legacyFixtureStillAppliesWithoutTheAllowFlag(tmpPath):
    target = _legacyNamed(tmpPath)
    code, out = runTool("assert-generated.py", str(target), "--lang", "java", "--strict")
    assert code == 1
    assert "CONTRACT_GAP" in out


def test_explicitContractStillAppliesWhenTheMissingContractIsAllowed(tmpPath):
    target = _legacyNamed(tmpPath)
    contract = _write(tmpPath, "x.contract", "signature public static void ausente()\n")
    code, out = runTool("assert-generated.py", str(target), "--lang", "java", "--strict",
                        "--allow-missing-contract", "--contract", str(contract))
    assert code == 1
    assert "ausente" in out


STYLE_JAVA = """package com.starkbank;

import java.util.HashMap;
import java.util.Map;

public final class Widget {
    /**
     * Widget object
     * <p>
     * Parameters:
     * name [string]: widget name
     * count [integer]: how many. ex: 3
     */
    static ClassData data = new ClassData(Widget.class, "Widget");

    public String name;
    public Integer count;

    /**
     * Widget object
     * <p>
     * Parameters:
     * @param name [string]: widget name
     * @param count [integer]: how many. ex: 3
     */
    public Widget(String name, Integer count) {
        this.name = name;
        this.count = count;
    }

    /**
     * Widget object
     * <p>
     * Parameters:
     * @param data map of properties for the creation of the Widget
     * name [string]: widget name
     * @throws Exception error in the request
     */
    public Widget(Map<String, Object> data) throws Exception {
        HashMap<String, Object> dataCopy = new HashMap<>(data);
        this.name = (String) dataCopy.remove("name");
        Number count = (Number) dataCopy.remove("count");
        this.count = count == null ? null : count.intValue();
        if (!dataCopy.isEmpty()) {
            throw new Exception("Unknown parameters used in constructor: " + dataCopy.keySet());
        }
    }

    /**
     * Retrieve a specific Widget
     * <p>
     * Parameters:
     * @param id [string]: object unique id. ex: "5656565656565656"
     * @param user [Organization/Project object, default null]: Organization or Project object.
     * <p>
     * Return:
     * @return Widget object with updated attributes
     * @throws Exception error in the request
     */
    @SuppressWarnings("unchecked")
    public static Widget get(
        String id,
        User user
    ) throws Exception {
        for (String key : keys) {
            if (key == null) {
                continue;
            }
        }
        if (id == null) {
            return null;
        } else if (user == null) {
            return null;
        }
        return Rest.getId(data, id, user);
    }
}
"""


def _styleRun(tmpPath, source, *flags):
    target = _write(tmpPath, "Widget.java", source)
    return runTool("assert-generated.py", str(target), "--lang", "java", "--allow-missing-contract",
                   "--strict", *flags)


def _dirty(old: str, new: str) -> str:
    assert old in STYLE_JAVA, f"o alvo da corrupcao nao existe na fixture: {old!r}"
    return STYLE_JAVA.replace(old, new, 1)


def _lineOf(source: str, snippet: str) -> int:
    return next(number for number, line in enumerate(source.splitlines(), start=1) if snippet in line)


def test_styleCleanFilePassesAndReportsTheCheck(tmpPath):
    code, out = _styleRun(tmpPath, STYLE_JAVA, "--style")
    assert code == 0, out
    assert "estilo" in out


BLOCKING_STYLE_CASES = [
    ("STYLE_PYTHON_RESIDUE", "     * name [string]: widget name\n     * count",
     "     * name [string]: widget name. ex: datetime.date(2020, 3, 10)\n     * count"),
    ("STYLE_PYTHON_RESIDUE", "ex: 3\n     */\n    static", "ex: 3 and timedelta\n     */\n    static"),
    ("STYLE_PYTHON_RESIDUE", "default null]: Organization", "default None]: Organization"),
    ("STYLE_PYTHON_RESIDUE", "default null]: Organization", "default True]: Organization"),
    ("STYLE_PYTHON_RESIDUE", "default null]: Organization", "default False]: Organization"),
    ("STYLE_PYTHON_RESIDUE", "ex: \"5656565656565656\"", "ex: \\\"5656565656565656\\\""),
    ("STYLE_PYTHON_RESIDUE", "Organization or Project object.\n", "Organization or Project object. Not necessary if starkbank.user was set\n"),
    ("STYLE_PYTHON_RESIDUE", "Organization or Project object.\n", "Organization or Project object. See User.defaultUser\n"),
    ("STYLE_EMPTY_DOC", "@param name [string]: widget name", "@param name [string]:"),
    ("STYLE_EMPTY_DOC", "     * count [integer]: how many. ex: 3\n     */\n    static", "     * count [integer]:\n     */\n    static"),
    ("STYLE_TRAILING_SPACE", "this.name = name;", "this.name = name; "),
    ("STYLE_PARAM_ORPHAN", "@param count [integer]: how many. ex: 3\n     */\n    public Widget(",
     "@param amount [integer]: how many. ex: 3\n     */\n    public Widget("),
    ("STYLE_NUMERIC_CAST", "Number count = (Number) dataCopy.remove(\"count\");", "Integer count = (Integer) dataCopy.remove(\"count\");"),
    ("STYLE_NUMERIC_CAST", "Number count = (Number) dataCopy.remove(\"count\");", "Long count = (Long) dataCopy.remove(\"count\");"),
    ("STYLE_NUMERIC_CAST", "Number count = (Number) dataCopy.remove(\"count\");", "Double count = ( Double ) dataCopy.remove(\"count\");"),
    ("STYLE_UNBRACED", "if (id == null) {\n            return null;\n        } else if", "if (id == null) return null;\n        else if"),
    ("STYLE_UNBRACED", "for (String key : keys) {\n            if (key == null) {\n                continue;\n            }\n        }",
     "for (String key : keys)\n            continue;"),
]


@pytest.mark.parametrize("kind,old,new", BLOCKING_STYLE_CASES)
def test_styleBlockingKindFailsWithFileLineAndSnippet(tmpPath, kind, old, new):
    source = _dirty(old, new)
    code, out = _styleRun(tmpPath, source, "--style")

    assert code == 1, out
    assert kind in out
    changed = next(line for line in source.splitlines() if line not in STYLE_JAVA.splitlines())
    assert f"Widget.java:{_lineOf(source, changed.strip())} {kind}" in out
    assert changed.strip() in out or changed.strip()[:30] in out


@pytest.mark.parametrize("kind,old,new", BLOCKING_STYLE_CASES)
def test_styleDefectsAreIgnoredWithoutTheFlag(tmpPath, kind, old, new):
    code, out = _styleRun(tmpPath, _dirty(old, new))
    assert code == 0, out
    assert "STYLE_" not in out


def test_styleResidueInsideACodeStringLiteralIsNotAccused(tmpPath):
    source = _dirty('throw new Exception("Unknown parameters used in constructor: " + dataCopy.keySet());',
                    'throw new Exception("None of datetime.date \\"True\\" starkbank.user");')
    code, out = _styleRun(tmpPath, source, "--style")
    assert code == 0, out
    assert "STYLE_PYTHON_RESIDUE" not in out


def test_styleParamsMatchTheSignatureOfTheDeclarationThatFollows(tmpPath):
    code, out = _styleRun(tmpPath, STYLE_JAVA, "--style")
    assert code == 0, out
    assert "STYLE_PARAM_ORPHAN" not in out


def test_styleOrphanParamOnTheDataFieldIsBlocked(tmpPath):
    source = _dirty("     * count [integer]: how many. ex: 3\n     */\n    static",
                    "     * @param count [integer]: how many. ex: 3\n     */\n    static")
    code, out = _styleRun(tmpPath, source, "--style")
    assert code == 1
    assert "STYLE_PARAM_ORPHAN" in out


def test_styleCastOfAStringAndOfNumberIsNotAccused(tmpPath):
    code, out = _styleRun(tmpPath, STYLE_JAVA, "--style")
    assert "STYLE_NUMERIC_CAST" not in out


def test_styleWarningsDoNotBlockAndAreCounted(tmpPath):
    source = _dirty("     * name [string]: widget name\n     * count",
                    "     * name [string]: widget name. ex: Widget.Rule(key=\"x\") and CardMethod(code=1)\n"
                    "     * my_description [string]: unknown snake name\n     * count")
    code, out = _styleRun(tmpPath, source, "--style")

    assert code == 0, out
    assert "[OK]" in out
    assert f"Widget.java:{_lineOf(source, 'Widget.Rule(key=')} STYLE_PYTHON_CTOR" in out
    assert f"Widget.java:{_lineOf(source, 'my_description')} STYLE_SNAKE_CASE" in out
    assert "2 aviso(s) de estilo" in out
    assert "[ERROR]" not in out


def test_styleSnakeCaseInACodeStringIsNotAWarning(tmpPath):
    source = _dirty('"Unknown parameters used in constructor: "', '"my_key_name "')
    code, out = _styleRun(tmpPath, source, "--style")
    assert code == 0, out
    assert "STYLE_SNAKE_CASE" not in out


def test_styleBlockingCountsAreReportedTogetherWithTheOtherBlockers(tmpPath):
    source = _dirty("default null]: Organization", "default None]: Organization").replace("this.name = name;", "this.name = name; ")
    code, out = _styleRun(tmpPath, source, "--style")
    assert code == 1
    assert "STYLE_PYTHON_RESIDUE" in out and "STYLE_TRAILING_SPACE" in out
    assert "2 problema(s) bloqueante(s)" in out


def test_styleIsOnlyForJava(tmpPath):
    target = _write(tmpPath, "widget.js", "module.exports = { name: 'None ' };\n")
    code, out = runTool("assert-generated.py", str(target), "--lang", "node", "--allow-missing-contract",
                        "--strict", "--style")
    assert "STYLE_" not in out
