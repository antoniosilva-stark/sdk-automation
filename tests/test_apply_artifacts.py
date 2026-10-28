import os
import pytest
from pathlib import Path

from conftest import runTool

DECLARED = "src/main/java/com/starkbank/Widget.java"


@pytest.fixture
def layout(tmpPath):
    staging = tmpPath / "staging"
    target = tmpPath / "target"
    (staging / "src/main/java/com/starkbank").mkdir(parents=True)
    target.mkdir()
    (staging / DECLARED).write_text("class Widget {}", encoding="utf-8")
    paths = tmpPath / "paths.txt"
    paths.write_text(DECLARED + "\n", encoding="utf-8")
    return {"staging": staging, "target": target, "paths": paths, "root": tmpPath}


def _apply(layout):
    return runTool("apply-artifacts.py", "--staging", str(layout["staging"]),
                   "--target", str(layout["target"]), "--paths-file", str(layout["paths"]))


def test_declaredFileIsCopiedAndExistingOneIsReplaced(layout):
    destination = layout["target"] / DECLARED
    destination.parent.mkdir(parents=True)
    destination.write_text("old", encoding="utf-8")

    code, out = _apply(layout)

    assert code == 0, out
    assert destination.read_text(encoding="utf-8") == "class Widget {}"


def test_undeclaredFileInTheArtifactIsNotCopied(layout):
    (layout["staging"] / "pom.xml").write_text("<evil/>", encoding="utf-8")

    code, out = _apply(layout)

    assert code == 0, out
    assert not (layout["target"] / "pom.xml").exists()


def test_symlinkAtTheDestinationIsRefusedAndNothingIsWrittenThroughIt(layout):
    victim = layout["root"] / "tools-place-generated.py"
    victim.write_text("original", encoding="utf-8")
    destination = layout["target"] / DECLARED
    destination.parent.mkdir(parents=True)
    destination.symlink_to(victim)

    code, out = _apply(layout)

    assert code == 1
    assert victim.read_text(encoding="utf-8") == "original"


def test_symlinkedParentDirectoryIsRefused(layout):
    outside = layout["root"] / "outside"
    outside.mkdir()
    (layout["target"] / "src").symlink_to(outside)

    code, out = _apply(layout)

    assert code == 1
    assert list(outside.rglob("*")) == []


def test_symlinkInTheArtifactIsRefused(layout):
    source = layout["staging"] / DECLARED
    source.unlink()
    source.symlink_to(layout["root"] / "paths.txt")

    code, out = _apply(layout)

    assert code == 1
    assert not (layout["target"] / DECLARED).exists()


@pytest.mark.parametrize("hostile", ["../escape.java", "/etc/passwd", ".git/config", "src/.git/hooks/pre-push", "./x.java"])
def test_pathsThatEscapeOrTouchGitAreRefused(layout, hostile):
    layout["paths"].write_text(hostile + "\n", encoding="utf-8")

    code, out = _apply(layout)

    assert code == 1


def test_missingArtifactFileIsRefused(layout):
    (layout["staging"] / DECLARED).unlink()

    code, out = _apply(layout)

    assert code == 1


def test_emptyPathListIsRefused(layout):
    layout["paths"].write_text("\n", encoding="utf-8")

    code, out = _apply(layout)

    assert code == 1
