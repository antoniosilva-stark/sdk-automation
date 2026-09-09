"""Shared helpers for the tools tests."""

import sys
import pytest
import subprocess
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"


def _loadTool(fileName: str):
    modulePath = TOOLS_DIR / fileName
    moduleName = modulePath.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(moduleName, modulePath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runTool(fileName: str, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / fileName), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return result.returncode, result.stdout


@pytest.fixture
def tmpPath(request):
    return request.getfixturevalue("tmp_path")


@pytest.fixture
def lintSpec():
    return _loadTool("lint-spec.py")


@pytest.fixture
def detector():
    return _loadTool("breaking-change-detector.py")


@pytest.fixture
def assertGenerated():
    return _loadTool("assert-generated.py")