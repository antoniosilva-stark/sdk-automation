from conftest import runTool


def _tree(tmpPath):
    root = tmpPath / "staging"
    (root / "src/main").mkdir(parents=True)
    (root / "src/main/A.java").write_text("class A {}", encoding="utf-8")
    return root


def test_sameContentGivesTheSameDigestAndPassesTheCheck(tmpPath):
    root = _tree(tmpPath)
    code, out = runTool("artifact-digest.py", str(root))
    assert code == 0
    digest = out.strip()

    code, _ = runTool("artifact-digest.py", str(root), "--expect", digest)
    assert code == 0


def test_changedContentFailsTheCheck(tmpPath):
    root = _tree(tmpPath)
    _, out = runTool("artifact-digest.py", str(root))
    (root / "src/main/A.java").write_text("class A { evil }", encoding="utf-8")

    code, _ = runTool("artifact-digest.py", str(root), "--expect", out.strip())

    assert code == 1


def test_extraFileFailsTheCheck(tmpPath):
    root = _tree(tmpPath)
    _, out = runTool("artifact-digest.py", str(root))
    (root / "extra.txt").write_text("x", encoding="utf-8")

    code, _ = runTool("artifact-digest.py", str(root), "--expect", out.strip())

    assert code == 1


def test_renamedFileFailsTheCheck(tmpPath):
    root = _tree(tmpPath)
    _, out = runTool("artifact-digest.py", str(root))
    (root / "src/main/A.java").rename(root / "src/main/B.java")

    code, _ = runTool("artifact-digest.py", str(root), "--expect", out.strip())

    assert code == 1


def test_symlinkInTheArtifactFailsEvenWithoutExpectation(tmpPath):
    root = _tree(tmpPath)
    (root / "link.java").symlink_to(root / "src/main/A.java")

    code, _ = runTool("artifact-digest.py", str(root))

    assert code == 1
