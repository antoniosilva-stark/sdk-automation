import os
import sys
import shutil
import pytest
import subprocess
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"


WIDGET_OPERATIONS = '''

def get(id, user=None):
    """# Retrieve a specific Widget
    Receive a single Widget object previously created in the Stark Bank API by its id
    ## Parameters (required):
    - id [string]: object unique id. ex: "5656565656565656"
    ## Parameters (optional):
    - user [Organization/Project object, default None]: Organization or Project object. Not necessary if starkbank.user
        was set before function call
    ## Return:
    - Widget object with updated attributes
    """
    return rest.get_id(resource=_resource, id=id, user=user)


def query(limit=None, after=None, tags=None, external_ids=None, user=None):
    """# Retrieve Widgets
    Receive a generator of Widget objects previously created in the Stark Bank API.
    Use this function instead of page if you want to stream Widgets without worrying about cursors and pagination.
    ## Parameters (optional):
    - limit [integer, default None]: maximum number of objects to be retrieved. Unlimited if None. ex: 35
    - after [datetime.date or string, default None]: date filter for objects created only after specified date. ex: datetime.date(2020, 3, 10)
    - tags [list of strings, default None]: tags to filter retrieved objects. ex: ["tony", "stark"]
    - external_ids [list of strings, default None]: list of external ids to filter retrieved objects. ex: ["5656565656565656"]
    - user [Organization/Project object, default None]: Organization or Project object. Not necessary if starkbank.user was set before function call
    ## Return:
    - generator of Widget objects with updated attributes
    """
    return rest.get_stream(resource=_resource, limit=limit)


def page(cursor=None, limit=None, after=None, tags=None, external_ids=None, user=None):
    """# Retrieve paged Widgets
    Receive a list of up to 100 Widget objects previously created in the Stark Bank API and the cursor to the next page.
    Use this function instead of query if you want to manually page your requests.
    ## Parameters (optional):
    - cursor [string, default None]: cursor returned on the previous page function call
    - limit [integer, default 100]: maximum number of objects to be retrieved. It must be an integer between 1 and 100. ex: 50
    - after [datetime.date or string, default None]: date filter for objects created only after specified date. ex: datetime.date(2020, 3, 10)
    - tags [list of strings, default None]: tags to filter retrieved objects. ex: ["tony", "stark"]
    - external_ids [list of strings, default None]: list of external ids to filter retrieved objects. ex: ["5656565656565656"]
    - user [Organization/Project object, default None]: Organization or Project object. Not necessary if starkbank.user was set before function call
    ## Return:
    - list of Widget objects with updated attributes
    - cursor to retrieve the next page of Widget objects
    """
    return rest.get_page(resource=_resource, cursor=cursor)
'''


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


def missingDependencies() -> list[str]:
    reasons = [GENERATOR_SKIP, JAVAC_SKIP]
    reasons += [reason for reason, resolved in (
        ("sdk-python não resolvido", PYTHON_SDK),
        ("sdk-java não resolvido", JAVA_SDK),
        ("sdk-node não resolvido", NODE_SDK),
    ) if resolved is None]
    return [reason for reason in reasons if reason]


# No CI um pre-requisito ausente pulava ~60 testes e a suite seguia verde: la ele reprova.
if os.environ.get("REQUIRE_TEST_DEPENDENCIES") == "1" and missingDependencies():
    raise pytest.UsageError("dependência de teste ausente: " + "; ".join(missingDependencies()))


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
def applySchema():
    return _loadTool("apply-schema.py")


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
def javaDocs():
    return _loadTool("java-docs.py")


@pytest.fixture
def coverageReport():
    return _loadTool("coverage-report.py")


REFERENCES = {"java": REPO_ROOT / "_references/sdk-java", "node": REPO_ROOT / "_references/sdk-node"}


@pytest.fixture(scope="session")
def referenceWithout(tmp_path_factory):
    """Referencia sem os arquivos do recurso, para exercitar o caminho de criacao.

    A existencia e medida contra o SDK de referencia, nao contra `--into`; e os recursos
    de teste ja existem nos pins. Arvore de symlinks: nenhum arquivo e copiado."""
    placeGenerated = _loadTool("place-generated.py")
    cache: dict[tuple, dict] = {}

    def build(language: str, resource: str, keepRoles: tuple[str, ...] = ()) -> dict:
        key = (language, resource, keepRoles)
        if key in cache:
            return cache[key]
        root = tmp_path_factory.mktemp(f"ref-{language}-{resource}") / "sdk"
        source = REFERENCES[language]
        shutil.copytree(source, root, symlinks=True, copy_function=os.symlink,
                        ignore=shutil.ignore_patterns(".git"))
        (root / ".git").symlink_to(source / ".git")
        for planned, target in placeGenerated.resolvePairs(language, resource):
            if planned.split("/", 1)[0] not in keepRoles:
                (root / target).unlink(missing_ok=True)
        cache[key] = {f"SDK_{language.upper()}": str(root)}
        return cache[key]

    return build


@pytest.fixture(scope="session")
def builtOnce(tmp_path_factory, referenceWithout):
    cache: dict[tuple, tuple] = {}

    def build(resource: str, language: str, *extra: str):
        key = (resource, language, extra)
        if key not in cache:
            target = tmp_path_factory.mktemp(f"{resource}-{language}")
            code, out = runTool("build-resource.py", resource, "--lang", language,
                                "--into", str(target), *extra,
                                env=referenceWithout(language, resource))
            cache[key] = (code, out, target)
        return cache[key]

    return build
