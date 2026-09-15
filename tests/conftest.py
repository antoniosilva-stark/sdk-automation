import os
import sys
import shutil
import pytest
import subprocess
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"


def commandWorks(*command: str) -> bool:
    if not shutil.which(command[0]):
        return False
    try:
        return subprocess.run(command, capture_output=True).returncode == 0
    except OSError:
        return False


def missingGeneratorDependency() -> str | None:
    if not shutil.which("npx"):
        return "npx ausente"
    if not commandWorks("java", "-version"):
        return "JRE ausente ou inoperante — o gerador roda em Java"
    return None


def missingJavacDependency() -> str | None:
    if not commandWorks("javac", "-version"):
        return "javac ausente ou inoperante — sem verificação de sintaxe Java"
    return None


GENERATOR_SKIP = missingGeneratorDependency()
JAVAC_SKIP = missingJavacDependency()

requiresGenerator = pytest.mark.skipif(GENERATOR_SKIP is not None, reason=GENERATOR_SKIP or "")
requiresJavac = pytest.mark.skipif(JAVAC_SKIP is not None, reason=JAVAC_SKIP or "")


def _loadTool(fileName: str):
    modulePath = TOOLS_DIR / fileName
    moduleName = modulePath.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(moduleName, modulePath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolvedPythonSdk() -> Path | None:
    extractSchema = _loadTool("extract-schema.py")
    root = extractSchema.resolveRoot(None)
    return None if extractSchema.rootIssue(root) else root


def resolvedJavaSdk() -> Path | None:
    return _loadTool("list-gaps.py").resolveJavaRoot()


def resolvedNodeSdk() -> Path | None:
    candidates = [Path(os.environ["SDK_NODE"])] if os.environ.get("SDK_NODE") else []
    candidates += [REPO_ROOT / "_references/sdk-node"]

    for candidate in candidates:
        if (candidate / "sdk").is_dir():
            return candidate / "sdk"
    return None


PYTHON_SDK = resolvedPythonSdk()
JAVA_SDK = resolvedJavaSdk()
NODE_SDK = resolvedNodeSdk()

requiresPythonSdk = pytest.mark.skipif(PYTHON_SDK is None, reason="sdk-python do Stark Bank não resolvido")
requiresJavaSdk = pytest.mark.skipif(JAVA_SDK is None, reason="sdk-java não resolvido")
requiresNodeSdk = pytest.mark.skipif(NODE_SDK is None, reason="sdk-node não resolvido")


@pytest.fixture
def conftest():
    import conftest as module

    return module


def runTool(fileName: str, *args: str, env: dict | None = None) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / fileName), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, **env} if env else None,
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


@pytest.fixture
def placeGenerated():
    return _loadTool("place-generated.py")


@pytest.fixture
def buildResource():
    return _loadTool("build-resource.py")


@pytest.fixture
def extractSchema():
    return _loadTool("extract-schema.py")


@pytest.fixture
def coverageReport():
    return _loadTool("coverage-report.py")