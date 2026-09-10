from pathlib import Path

from conftest import runTool

JAVA_SOURCE = "main/src/main/java/com/starkbank/SplitProfile.java"
JAVA_TARGET = "src/main/java/com/starkbank/SplitProfile.java"


def _generated(tmpPath: Path, relative: str = JAVA_SOURCE, body: str = "class X {}\n") -> Path:
    root = tmpPath / "generated"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return root


def _targetRepo(tmpPath: Path) -> Path:
    root = tmpPath / "repo"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_posicionaArquivoJava(tmpPath):
    generated = _generated(tmpPath, body="class SplitProfile {}\n")
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 0, out
    placed = repo / JAVA_TARGET
    assert placed.is_file()
    assert placed.read_text(encoding="utf-8") == "class SplitProfile {}\n"


def test_criaDiretoriosIntermediarios(tmpPath):
    generated = _generated(tmpPath)
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 0, out
    assert (repo / "src/main/java/com/starkbank").is_dir()


def test_sobrescreveArquivoExistente(tmpPath):
    generated = _generated(tmpPath, body="novo\n")
    repo = _targetRepo(tmpPath)
    existing = repo / JAVA_TARGET
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("antigo\n", encoding="utf-8")

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 0, out
    assert existing.read_text(encoding="utf-8") == "novo\n"


def test_idempotenteEmDuasExecucoes(tmpPath):
    generated = _generated(tmpPath, body="estavel\n")
    repo = _targetRepo(tmpPath)
    args = ("SplitProfile", "--lang", "java", "--from", str(generated), "--to", str(repo))

    first, _ = runTool("place-generated.py", *args)
    content = (repo / JAVA_TARGET).read_text(encoding="utf-8")
    second, _ = runTool("place-generated.py", *args)

    assert (first, second) == (0, 0)
    assert (repo / JAVA_TARGET).read_text(encoding="utf-8") == content


def test_varNameDerivaNomeDoDiretorio(placeGenerated):
    assert placeGenerated.varName("SplitProfile") == "splitProfile"
    assert placeGenerated.varName("Invoice") == "invoice"


def test_linguagemSemLayoutRetornaDois(tmpPath):
    generated = _generated(tmpPath)
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "cobol",
                        "--from", str(generated), "--to", str(repo))

    assert code == 2
    assert "linguagem sem layout mapeado" in out


def test_arquivoAusenteNaSaidaRetornaUm(tmpPath):
    generated = tmpPath / "generated"
    generated.mkdir()
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(repo))

    assert code == 1
    assert "ausente(s) na saída do gerador" in out
    assert JAVA_SOURCE in out


def test_saidaInexistenteRetornaDois(tmpPath):
    repo = _targetRepo(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(tmpPath / "nao-existe"), "--to", str(repo))

    assert code == 2
    assert "saída do gerador não é um diretório" in out


def test_alvoInexistenteRetornaDois(tmpPath):
    generated = _generated(tmpPath)

    code, out = runTool("place-generated.py", "SplitProfile", "--lang", "java",
                        "--from", str(generated), "--to", str(tmpPath / "nao-existe"))

    assert code == 2
    assert "repositório alvo não é um diretório" in out


def test_nadaEhEscritoQuandoFonteAusente(tmpPath):
    generated = tmpPath / "generated"
    generated.mkdir()
    repo = _targetRepo(tmpPath)

    runTool("place-generated.py", "SplitProfile", "--lang", "java",
            "--from", str(generated), "--to", str(repo))

    assert list(repo.rglob("*")) == []
