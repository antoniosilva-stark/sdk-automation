import re
import subprocess
from hashlib import sha1

import yaml
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavac, requiresJavaSdk, _loadTool
from test_java_field_types import REAL_SOURCES, _balancedBlock, _generated, _realConventionsText
from test_java_payment_request_log import SOURCEPATH, _members

QUERY_OVERLOADS = ("", "Map<String, Object> params", "User user", "Map<String, Object> params, User user")
SHAPE = {"returns": "list", "classData": "short"}


def _entries() -> dict:
    return yaml.safe_load(_realConventionsText())["javaQueryShapes"]


def _realSource(resource: str) -> str:
    return (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")


def _queryParameters(source: str, returned: str) -> list:
    found = re.findall(rf"^    public static {re.escape(returned)} query\((.*?)\) throws", source, re.M | re.S)
    return [re.sub(r"\s*,\s*", ", ", " ".join(parameters.split())) for parameters in found]


def _pageBlock(source: str) -> str:
    match = re.search(r"^    private final static class Page\b[^{]*\{", source, re.M)
    return _balancedBlock(source, match)


def _queryBody(source: str, resource: str) -> str:
    match = re.search(
        rf"^    public static List<{resource}> query\(Map<String, Object> params, User user\) throws Exception \{{",
        source,
        re.M,
    )
    return _balancedBlock(source, match)


@pytest.fixture(scope="module")
def generatedInstitution(tmp_path_factory):
    return _generated(_loadTool("build-resource.py"), tmp_path_factory.mktemp("institution"), "Institution",
                      _realConventionsText())


# 1. a entrada contra o real, nos dois sentidos

@requiresJavaSdk
def test_theInstitutionEntryIsTheShapeOfTheRealQueryAndIsTheOnlyEntry():
    assert _entries() == {"Institution": SHAPE}


@requiresJavaSdk
def test_theRealInstitutionHasTheListQueriesThePrivatePageAndTheShortClassData():
    source = _realSource("Institution")

    assert _queryParameters(source, "List<Institution>") == list(QUERY_OVERLOADS)
    assert _queryParameters(source, "Generator<Institution>") == []
    assert "    static ClassData data = new ClassData(Institution.class, \"Institution\");\n" in source
    assert "SubResource.ClassData" not in source
    assert "private final static class Page {" in source


@requiresJavaSdk
def test_noOtherRealResourceHasTheListQueryShape():
    others = [path.stem for path in REAL_SOURCES.glob("*.java")
              if path.stem != "Institution" and "private final static class Page" in path.read_text(encoding="utf-8")]

    assert others == []


# 2. o Institution gerado contra o real

@requiresGenerator
@requiresJavaSdk
def test_generatedQueryOverloadsReturnTheListInTheOrderOfTheReal(generatedInstitution):
    assert _queryParameters(generatedInstitution, "List<Institution>") == list(QUERY_OVERLOADS)
    assert "Generator" not in generatedInstitution


@requiresGenerator
@requiresJavaSdk
def test_generatedQueryBodyMatchesTheRealOne(generatedInstitution):
    assert _members(_queryBody(generatedInstitution, "Institution")) == _members(_queryBody(_realSource("Institution"),
                                                                                           "Institution"))


@requiresGenerator
@requiresJavaSdk
def test_generatedPrivatePageMatchesTheRealOneMemberByMember(generatedInstitution):
    assert _members(_pageBlock(generatedInstitution)) == _members(_pageBlock(_realSource("Institution")))


@requiresGenerator
def test_generatedClassDataIsTheShortOne(generatedInstitution):
    assert "    static ClassData data = new ClassData(Institution.class, \"Institution\");\n" in generatedInstitution
    assert "SubResource.ClassData" not in generatedInstitution


@requiresGenerator
def test_generatedQueryReturnsTheListOfThePageNotThePage(generatedInstitution):
    assert "        return new Page(institutions, page.cursor).institutions;\n" in generatedInstitution
    assert "            institutions.add((Institution) institution);\n" in generatedInstitution


# 3. imports, gate estrito e javac contra os irmaos reais

_MAIN = """import com.starkbank.*;
import java.util.List;

public class Main {
    public static void main(String[] args) throws Exception {
        List<Institution> institutions = Institution.query();
        List<Institution> byUser = Institution.query((User) null);
        System.out.println("institutions -> " + institutions.size() + " " + byUser.size());
    }
}
"""


@requiresGenerator
def test_generatedImportsDropGeneratorAndKeepArrayListAndList(generatedInstitution):
    assert "import com.starkbank.utils.Generator;" not in generatedInstitution
    assert "import java.util.ArrayList;\n" in generatedInstitution
    assert "import java.util.List;\n" in generatedInstitution


@requiresGenerator
@requiresJavac
@requiresJavaSdk
def test_generatedInstitutionPassesTheStrictGateAndCompilesWithTheRealSiblings(
    builtOnce, tmp_path_factory, javaRuntimeClasspath
):
    code, out, target = builtOnce("Institution", "java")
    assert code == 0, out
    assert "DEAD_IMPORT" not in out
    work = tmp_path_factory.mktemp("institution-runtime")
    (work / "src").mkdir()
    (work / "src/Main.java").write_text(_MAIN, encoding="utf-8")

    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", javaRuntimeClasspath, "-sourcepath",
         f"{SOURCEPATH}:{target / 'src/main/java'}", str(work / "src/Main.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr


# 4. a borda

def _load(extractSchema, tmpPath, text: str, resource: str = "Institution"):
    conventions = tmpPath / f"{sha1(text.encode()).hexdigest()}.yaml"  # loadConventions memoiza por caminho
    conventions.write_text(text, encoding="utf-8")
    return extractSchema.javaQueryShape(resource, conventions)


def test_javaQueryShapeReturnsTheEntryAndNoneWithoutEntryOrSection(extractSchema, tmpPath):
    entry = "javaQueryShapes:\n  Institution: {returns: list, classData: short}\n"

    assert _load(extractSchema, tmpPath, entry) == SHAPE
    assert _load(extractSchema, tmpPath, entry, "MerchantCategory") is None
    assert _load(extractSchema, tmpPath, "javaTypes:\n") is None


@pytest.mark.parametrize("text, message", [
    ("javaQueryShapes:\n  Institutionn: {returns: list, classData: short}\n", "Institutionn: javaQueryShapes cita recurso"),
    ("javaQueryShapes:\n  Institution: {returns: list, classData: short, extra: 1}\n",
     "Institution: javaQueryShapes chave desconhecida: extra"),
    ("javaQueryShapes:\n  Institution: {returns: generator, classData: short}\n",
     "Institution: javaQueryShapes.returns invalido: 'generator'"),
    ("javaQueryShapes:\n  Institution: {returns: list, classData: qualified}\n",
     "Institution: javaQueryShapes.classData invalido: 'qualified'"),
    ("javaQueryShapes:\n  Institution: {returns: [list], classData: short}\n",
     "Institution: javaQueryShapes.returns invalido: \\['list'\\]"),
    ("javaQueryShapes:\n  Institution: {classData: short}\n", "Institution: javaQueryShapes.returns ausente"),
    ("javaQueryShapes:\n  Institution: {returns: list}\n", "Institution: javaQueryShapes.classData ausente"),
    ("javaQueryShapes:\n  Institution: list\n", "Institution: javaQueryShapes deve ser um mapa"),
    ("javaQueryShapes: [Institution]\n", "javaQueryShapes deve ser um mapa"),
])
def test_invalidJavaQueryShapesDataFailsNamingTheResourceTheKeyAndTheValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=message):
        _load(extractSchema, tmpPath, text)


@pytest.mark.parametrize("resource, flags, message", [
    ("Transfer", {"x-sdk-query"}, "Transfer: javaQueryShapes.returns 'list' exige x-sdk-subresource"),
    ("Institution", {"x-sdk-subresource"}, "Institution: javaQueryShapes.returns 'list' exige x-sdk-query"),
    ("Institution", {"x-sdk-subresource", "x-sdk-query", "x-sdk-page"},
     "Institution: javaQueryShapes.returns 'list' nao vale com x-sdk-page"),
])
def test_listReturnOutsideASubresourceQueryWithoutPageFailsNamingResourceKeyAndValue(resource, flags, message):
    with pytest.raises(ValueError, match=message):
        _loadTool("java-code.py").renderQuery(resource, SHAPE, flags)


def test_renderQueryFlagsTheListReturnAndTheShortClassData():
    rendered = _loadTool("java-code.py").renderQuery("Institution", SHAPE, {"x-sdk-subresource", "x-sdk-query"})

    assert rendered == {"returnsList": True, "shortClassData": True}


# 5. recurso sem entrada segue como hoje

@requiresGenerator
@pytest.mark.parametrize("resource", ["MerchantCategory", "Transfer"])
def test_resourceWithoutEntryKeepsTheGeneratorQueryAndTheQualifiedClassData(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())

    assert "import com.starkbank.utils.Generator;\n" in source
    assert f"    public static Generator<{resource}> query() throws Exception {{\n" in source
    assert "private final static class Page" not in source
    assert "List<" + resource + "> query(" not in source


@requiresGenerator
@pytest.mark.parametrize("resource", ["MerchantCategory", "Transfer"])
def test_entryOfAnotherResourceLeavesTheGeneratedClassIdentical(buildResource, tmpPath, resource):
    plain = _generated(buildResource, tmpPath / "plain", resource, "javaTypes:\n")
    other = _generated(buildResource, tmpPath / "other", resource,
                       "javaQueryShapes:\n  Institution: {returns: list, classData: short}\n")

    assert other == plain


@requiresGenerator
def test_merchantCategoryKeepsTheSubResourceClassData(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "MerchantCategory", _realConventionsText())

    assert "static SubResource.ClassData data = new SubResource.ClassData(MerchantCategory.class," in source
