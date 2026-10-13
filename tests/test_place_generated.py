import subprocess
from pathlib import Path

from conftest import runTool

JAVA_SOURCE = "main/src/main/java/com/starkbank/SplitProfile.java"
JAVA_TEST_SOURCE = "test/src/main/java/com/starkbank/SplitProfile.java"
JAVA_TARGET = "src/main/java/com/starkbank/SplitProfile.java"
JAVA_TEST_TARGET = "src/test/java/TestSplitProfile.java"


def _generated(tmpPath: Path, relative: str = JAVA_SOURCE, body: str = "class X {}\n") -> Path:
    root = tmpPath / "generated"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    if relative == JAVA_SOURCE:
        companion = root / JAVA_TEST_SOURCE
        companion.parent.mkdir(parents=True, exist_ok=True)
        companion.write_text("class TestSplitProfile {}\n", encoding="utf-8")

    return root


def _targetRepo(tmpPath: Path) -> Path:
    root = tmpPath / "repo"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_placesTheJavaFile(tmpPath):
    generated = _generated(tmpPath, body="class SplitProfile {}\n")
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 0, out
    placed = repo / JAVA_TARGET
    assert placed.is_file()
    assert placed.read_text(encoding="utf-8") == "class SplitProfile {}\n"


def test_placesBothJavaArtifacts(tmpPath):
    generated = _generated(tmpPath, body="class SplitProfile {}\n")
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 0, out
    assert (repo / JAVA_TARGET).is_file()
    assert (repo / JAVA_TEST_TARGET).read_text(encoding="utf-8") == "class TestSplitProfile {}\n"


def test_targetsDeclaresBothPaths():
    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java", "--targets")

    assert code == 0
    assert out.split() == [JAVA_TARGET, JAVA_TEST_TARGET]


def test_createsIntermediateDirectories(tmpPath):
    generated = _generated(tmpPath)
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 0, out
    assert (repo / "src/main/java/com/starkbank").is_dir()


def test_overwritesExistingFile(tmpPath):
    generated = _generated(tmpPath, body="novo\n")
    repo = _targetRepo(tmpPath)
    existing = repo / JAVA_TARGET
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("antigo\n", encoding="utf-8")

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 0, out
    assert existing.read_text(encoding="utf-8") == "novo\n"


def test_idempotentAcrossTwoRuns(tmpPath):
    generated = _generated(tmpPath, body="estavel\n")
    repo = _targetRepo(tmpPath)
    args = ("SplitProfile", "--lang", "java", "--from", str(generated), "--to", str(repo))

    first, _ = runTool("place-generated.py", *args)
    content = (repo / JAVA_TARGET).read_text(encoding="utf-8")
    second, _ = runTool("place-generated.py", *args)

    assert (first, second) == (0, 0)
    assert (repo / JAVA_TARGET).read_text(encoding="utf-8") == content


def test_varNameDerivesTheDirectoryName(placeGenerated):
    assert placeGenerated.varName("SplitProfile") == "splitProfile"
    assert placeGenerated.varName("Invoice") == "invoice"


def test_repoPrintsTheTargetRepository():
    for language, expected in (("java", "sdk-java"), ("node", "sdk-node")):
        code, out = runTool("place-generated.py", "--lang", language, "--repo")
        assert (code, out.strip()) == (0, expected)


def test_repoDoesNotRequireResource():
    code, out = runTool("place-generated.py", "--lang", "java", "--repo")
    assert code == 0
    assert "obrigatório" not in out


def test_repoOfLanguageWithoutTargetReturnsTwo():
    code, out = runTool("place-generated.py", "--lang", "cobol", "--repo")
    assert code == 2
    assert "linguagem sem repositório alvo mapeado" in out


def test_missingResourceOutsideQueryModesReturnsTwo():
    code, out = runTool("place-generated.py", "--lang", "java", "--list")
    assert code == 2
    assert "resource é obrigatório fora dos modos --repo e --slug" in out


def test_slugDerivesTheResourceKebab():
    for resource, expected in (("SplitProfile", "split-profile"), ("Invoice", "invoice"),
                               ("DictKey", "dict-key"), ("Transaction", "transaction")):
        code, out = runTool("place-generated.py", resource, "--slug")
        assert (code, out.strip()) == (0, expected), resource


def test_slugDoesNotRequireLang():
    code, out = runTool("place-generated.py", "Invoice", "--slug")
    assert code == 0
    assert "obrigatório" not in out


def test_slugRejectsShellMetacharacters():
    for hostile in ("X$(id)", "X`id`", "X;id", "X|id", "X&id", "X\nbranch=evil"):
        code, out = runTool("place-generated.py", hostile, "--slug")
        assert code == 2, f"{hostile!r} passou"
        assert "nome de recurso inválido" in out


def test_slugRejectsNonUpperCamelCaseForms():
    for invalid in ("lower", "With Space", "has-dash", "has_underscore", "9Leading"):
        code, out = runTool("place-generated.py", invalid, "--slug")
        assert code == 2, f"{invalid!r} passou"


def test_slugWithoutResourceReturnsTwo():
    code, out = runTool("place-generated.py", "--slug")
    assert code == 2
    assert "resource é obrigatório no modo --slug" in out


def test_missingLangOutsideQueryReturnsTwo(tmpPath):
    code, out = runTool("place-generated.py", "SplitProfile", "--list")
    assert code == 2
    assert "--lang é obrigatório" in out


def test_everyLanguageWithLayoutHasATargetRepository(placeGenerated):
    assert set(placeGenerated.LAYOUTS) == set(placeGenerated.TARGETS)


def test_eachLanguageHasADistinctTarget(placeGenerated):
    alvos = list(placeGenerated.TARGETS.values())
    assert len(alvos) == len(set(alvos))


def test_languageWithoutLayoutReturnsTwo(tmpPath):
    generated = _generated(tmpPath)
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "cobol",
                        "--from", str(generated), "--to", str(repo))

    assert code == 2
    assert "linguagem sem layout mapeado" in out


def test_missingFileInTheOutputReturnsOne(tmpPath):
    generated = tmpPath / "generated"
    generated.mkdir()
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 1
    assert "ausente(s) na saída do gerador" in out
    assert JAVA_SOURCE in out


def test_missingOutputDirectoryReturnsTwo(tmpPath):
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(tmpPath / "nao-existe"), "--to", str(repo))

    assert code == 2
    assert "saída do gerador não é um diretório" in out


def test_missingTargetReturnsTwo(tmpPath):
    generated = _generated(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(tmpPath / "nao-existe"))

    assert code == 2
    assert "repositório alvo não é um diretório" in out


def test_nothingIsWrittenWhenTheSourceIsMissing(tmpPath):
    generated = tmpPath / "generated"
    generated.mkdir()
    repo = _targetRepo(tmpPath)

    runTool("place-generated.py", "SplitProfile", "--lang", "java",
            "--from", str(generated), "--to", str(repo))

    assert list(repo.rglob("*")) == []


def test_targetsPrintsOnlyTheDestinations():
    code, out = runTool("place-generated.py", "Transfer", "--lang", "node", "--targets")
    linhas = out.strip().splitlines()

    assert code == 0
    assert linhas == ["sdk/transfer/transfer.js", "sdk/transfer/index.js", "types/transfer/transfer.d.ts"]
    assert all("->" not in linha for linha in linhas)


def test_targetsHasTheSameCountAsList():
    _, targets = runTool("place-generated.py", "Invoice", "--lang", "node", "--targets")
    _, pares = runTool("place-generated.py", "Invoice", "--lang", "node", "--list")
    assert len(targets.strip().splitlines()) == len(pares.strip().splitlines())


def test_strayFileInTheTargetStaysOutOfTheCommit(tmpPath):
    repo = tmpPath / "alvo"
    repo.mkdir()
    for command in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java", "--targets")
    assert code == 0
    declared = out.strip().splitlines()

    for relative in declared:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("gerado\n", encoding="utf-8")
    (repo / "sobra.tmp").write_text("lixo do gerador\n", encoding="utf-8")

    for relative in declared:
        subprocess.run(["git", "add", "--", relative], cwd=repo, check=True, capture_output=True)

    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo,
                            capture_output=True, text=True, check=True).stdout.split()
    assert staged == declared
    assert "sobra.tmp" not in staged


def test_resourceNameIsValidatedInEveryMode(tmpPath):
    for extra in (["--list"], ["--targets"], ["--from", str(tmpPath), "--to", str(tmpPath)]):
        code, out = runTool("place-generated.py", "../etc/passwd", "--lang", "java", *extra)
        assert code == 2, f"nome invalido aceito em {extra}: {out}"
        assert "nome de recurso inválido" in out
