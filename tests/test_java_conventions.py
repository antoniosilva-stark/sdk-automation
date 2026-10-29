import re
import shutil
import subprocess

import yaml
import pytest

from conftest import REFERENCES, REPO_ROOT, requiresGenerator

SCHEMAS = REPO_ROOT / "apis/schemas"
JAVA_DIR = "src/main/java/com/starkbank"

# (recurso, argumentos extras do build): os mesmos dos testes de build, para dividir o cache da sessao.
RESOURCES = (
    ("SplitProfile", ()),
    ("Transfer", ("--advisory",)),
    ("Transaction", ("--advisory",)),
    ("Webhook", ("--advisory",)),
    ("Balance", ("--advisory",)),
    ("VerifiedTransfer", ("--advisory",)),
    ("Invoice", ("--advisory",)),
    ("CorporateCard", ("--advisory",)),
)
RESOURCE_IDS = [name for name, _ in RESOURCES]

# Ordem total derivada dos pares do eixo F2 da ME 4 (get<query 27 a 4, query<page 33 a 0, get<pdf 8 a 0,
# page<delete 7 a 2, get<create 13 a 9, create<pdf 7 a 1, pdf<delete 5 a 1...); `put` so tem o SplitProfile (1 a 0).
METHOD_ORDER = ("put", "get", "query", "page", "create", "update", "pdf", "delete", "cancel")
LOG_METHOD_ORDER = ("get", "query", "page")
# Maiorias do eixo F2b: get 21 a 6, query 19 a 12, page 24 a 4; o Log e unanime (23 a 0) em `(params) (user) () (params,user)`.
OVERLOADS = {
    "get": ["String id", "String id, User user"],
    "query": ["Map<String, Object> params, User user", "Map<String, Object> params", "User user", ""],
    "page": ["Map<String, Object> params", "User user", "", "Map<String, Object> params, User user"],
}
LOG_OVERLOADS = {
    "get": OVERLOADS["get"],
    "query": OVERLOADS["page"],
    "page": OVERLOADS["page"],
}
COM_IMPORT_ORDER = ("com.starkbank.utils.Generator", "com.starkbank.utils.Resource", "com.starkbank.utils.Rest",
                    "com.starkcore.utils.SubResource", "com.starkbank.error.ErrorElement")
NUMERIC_GETTERS = {"Integer": "intValue", "Long": "longValue", "Float": "floatValue", "Double": "doubleValue"}
METHOD_LINE = re.compile(r"^( {4}| {8})public static [\w.<>\[\], ?]+? (\w+)\((.*?)\) throws Exception \{$")


@pytest.fixture
def generated(builtOnce):
    def read(resource: str) -> str:
        extras = dict(RESOURCES)[resource]
        code, out, target = builtOnce(resource, "java", *extras)
        assert code == 0, out
        return (target / JAVA_DIR / f"{resource}.java").read_text(encoding="utf-8")

    return read


def _schemas(resource: str) -> tuple:
    fileName = {"SplitProfile": "splitprofile", "VerifiedTransfer": "verifiedtransfer", "CorporateCard": "corporatecard"}
    schemas = yaml.safe_load((SCHEMAS / f"{fileName.get(resource, resource.lower())}.yaml").read_text(encoding="utf-8"))
    schemas = schemas["components"]["schemas"]
    return schemas[resource], schemas.get(f"{resource}Create") or {}


def _declaredTypes(source: str) -> dict:
    """Campos `public T nome;` da classe de topo (o gerador e a fonte do tipo; o teste nao o recalcula)."""
    return {name: javaType for javaType, name in re.findall(r"^    public ([\w<>\[\]]+) (\w+);$", source, re.M)}


def _mapConstructorBody(source: str) -> str:
    start = re.search(r"^    public \w+\(Map<String, Object> data\) throws Exception \{$", source, re.M)
    return source[start.end():source.index("\n    }\n", start.end())]


def _methods(source: str, indent: str) -> list:
    """(nome, parametros, linha) dos metodos `public static` do nivel pedido, na ordem do arquivo."""
    methods = []
    for number, line in enumerate(source.splitlines()):
        match = METHOD_LINE.match(line)
        if match and match.group(1) == indent:
            methods.append((match.group(2), match.group(3), number))
    return methods


def _docBefore(lines: list, number: int) -> str:
    end = number - 1
    while lines[end].strip().startswith("@"):
        end -= 1
    start = end
    while "/**" not in lines[start]:
        start -= 1
    return "\n".join(lines[start:end + 1])


@requiresGenerator
@pytest.mark.parametrize("resource", RESOURCE_IDS)
def test_returnOnlyFieldsAreNullAndNeverReadFromTheMap(generated, resource):
    source = generated(resource)
    schema, createSchema = _schemas(resource)
    creatable = set(createSchema.get("properties") or {})
    returnOnly = [name for name, prop in schema["properties"].items()
                  if name not in creatable and not prop.get("x-sdk-id")]

    body = _mapConstructorBody(source)

    for name in returnOnly:
        assert f"this.{name} = null;" in body
        assert f'dataCopy.remove("{name}")' not in source


@requiresGenerator
@pytest.mark.parametrize("resource", RESOURCE_IDS)
def test_everyCreatableFieldIsReadWithTheGeneratedDeclaredType(generated, resource):
    source = generated(resource)
    schema, createSchema = _schemas(resource)
    declared = _declaredTypes(source)
    body = _mapConstructorBody(source)

    for name in (createSchema.get("properties") or {}):
        if schema["properties"][name].get("x-sdk-id"):
            continue
        javaType = declared[name]
        if javaType in NUMERIC_GETTERS:
            assert f'Number {name} = (Number) dataCopy.remove("{name}");' in body
            assert f"this.{name} = {name} == null ? null : {name}.{NUMERIC_GETTERS[javaType]}();" in body
            continue
        assert f'this.{name} = ({javaType}) dataCopy.remove("{name}");' in body


@requiresGenerator
@pytest.mark.parametrize("resource", RESOURCE_IDS)
def test_noNumericIsReadWithADirectCast(generated, resource):
    source = generated(resource)

    assert not re.search(r"\((Integer|Long|Float|Double)\) dataCopy\.remove\(", source)


@requiresGenerator
def test_aLongFieldIsConvertedWithLongValue(generated):
    source = generated("Transfer")

    assert 'Number amount = (Number) dataCopy.remove("amount");' in source
    assert "this.amount = amount == null ? null : amount.longValue();" in source


@requiresGenerator
@pytest.mark.parametrize("resource", RESOURCE_IDS)
def test_suppressWarningsOnlyWhereThereIsAGenericCast(generated, resource):
    lines = generated(resource).splitlines()
    starts = [number for number, line in enumerate(lines) if re.match(r"^    public .*\) (throws Exception )?\{$", line)]
    annotated = {number for number in starts if lines[number - 1].strip() == '@SuppressWarnings("unchecked")'}
    needing = set()
    for number in starts:
        end = next(index for index in range(number, len(lines)) if lines[index] == "    }")
        if any(re.search(r"\((?:List|Map)<[^()]*>\) ", line) for line in lines[number:end]):
            needing.add(number)

    assert annotated == needing
    assert sum("@SuppressWarnings" in line for line in lines) == len(annotated)


@requiresGenerator
def test_theMapConstructorIsAnnotatedOnlyWhenItCastsAGenericList(generated):
    assert '    @SuppressWarnings("unchecked")\n    public Transfer(Map<String, Object> data)' in generated("Transfer")
    assert "@SuppressWarnings" not in generated("SplitProfile").split("public SplitProfile(Map")[0].splitlines()[-1]
    assert "@SuppressWarnings" not in generated("Balance")


@requiresGenerator
@pytest.mark.parametrize("resource", ("Transfer", "Webhook", "VerifiedTransfer"))
def test_theCreateLoopHasBracesContinueAndNoElseIf(generated, resource):
    source = generated(resource)

    assert re.search(r"if \(\w+ instanceof Map\) \{\n\s+\w+\.add\(new %s\(\(Map<String, Object>\) \w+\)\);\n\s+continue;\n\s+\}"
                     % resource, source)
    assert re.search(r"if \(\w+ instanceof %s\) \{\n\s+\w+\.add\(\(%s\) \w+\);\n\s+continue;\n\s+\}" % (resource, resource),
                     source)
    assert re.search(r'\n\s+throw new Exception\("Unknown type \\"" \+ \w+\.getClass\(\)', source)
    assert "else if" not in source


@requiresGenerator
@pytest.mark.parametrize("resource", RESOURCE_IDS)
def test_methodsFollowTheMeasuredOrder(generated, resource):
    source = generated(resource)
    names = []
    for name, _, _ in _methods(source, "    "):
        if name not in names:
            names.append(name)

    assert names == [name for name in METHOD_ORDER if name in names]
    assert names


@requiresGenerator
def test_aResourceWithEveryOperationKeepsTheWholeOrder(generated):
    source = generated("Invoice")
    names = []
    for name, _, _ in _methods(source, "    "):
        if name not in names:
            names.append(name)

    assert names == ["get", "query", "page", "create", "update", "pdf"]
    logNames = []
    for name, _, _ in _methods(source, "        "):
        if name not in logNames:
            logNames.append(name)
    assert logNames == list(LOG_METHOD_ORDER)


@requiresGenerator
def test_aCreateOnlyResourceHasNoReadMethods(generated):
    source = generated("VerifiedTransfer")

    assert [name for name, _, _ in _methods(source, "    ")] == ["create", "create"]
    assert "Generator" not in source
    assert "Rest.getStream" not in source


@requiresGenerator
@pytest.mark.parametrize("indent, expected", (("    ", OVERLOADS), ("        ", LOG_OVERLOADS)))
def test_overloadsFollowTheMeasuredOrder(generated, indent, expected):
    source = generated("Invoice")
    for operation, signatures in expected.items():
        found = [params for name, params, _ in _methods(source, indent) if name == operation]
        assert found == signatures, operation


@requiresGenerator
def test_aSplitProfilePutKeepsItsListOverloadsBeforeTheirUserForm(generated):
    source = generated("SplitProfile")

    found = [params for name, params, _ in _methods(source, "    ") if name == "put"]
    assert found == ["List<?> splitProfiles", "List<?> splitProfiles, User user"]


@requiresGenerator
@pytest.mark.parametrize("indent", ("    ", "        "))
def test_theDocFollowsItsOverloadAfterTheReorder(generated, indent):
    source = generated("Invoice")
    lines = source.splitlines()

    for name, params, number in _methods(source, indent):
        doc = _docBefore(lines, number)
        assert ("@param params" in doc) == ("Map<String, Object> params" in params), (name, params)
        assert ("@param user" in doc) == ("User user" in params), (name, params)


@requiresGenerator
@pytest.mark.parametrize("resource", RESOURCE_IDS)
def test_importsAreOrderedAndGrouped(generated, resource):
    source = generated(resource)
    header = source.split("public final class")[0]
    lines = header.splitlines()
    assert lines[0] == "package com.starkbank;"
    assert lines[1] == ""
    assert header.endswith(";\n\n\n")

    groups = [group.splitlines() for group in "\n".join(lines[2:]).strip("\n").split("\n\n")]
    assert len(groups) == 2
    comGroup = [line[len("import "):-1] for line in groups[0]]
    javaGroup = [line[len("import "):-1] for line in groups[1]]

    assert all(line.startswith("import com.") for line in groups[0])
    assert all(line.startswith("import java.") for line in groups[1])
    assert comGroup == [name for name in COM_IMPORT_ORDER if name in comGroup]
    assert javaGroup == sorted(javaGroup)


@requiresGenerator
def test_theImportBlockOfAFullResourceListsEveryGroupInTheMeasuredOrder(generated):
    header = generated("Invoice").split("public final class")[0]

    assert header == (
        "package com.starkbank;\n"
        "\n"
        "import com.starkbank.utils.Generator;\n"
        "import com.starkbank.utils.Resource;\n"
        "import com.starkbank.utils.Rest;\n"
        "import com.starkcore.utils.SubResource;\n"
        "import com.starkbank.error.ErrorElement;\n"
        "\n"
        "import java.io.InputStream;\n"
        "import java.util.ArrayList;\n"
        "import java.util.HashMap;\n"
        "import java.util.List;\n"
        "import java.util.Map;\n"
        "\n"
        "\n"
    )


@requiresGenerator
@pytest.mark.parametrize("resource", RESOURCE_IDS)
def test_noIfForOrElseWithoutBraces(generated, resource):
    for line in generated(resource).splitlines():
        if re.match(r"^\s*(\} )?(if|for|while|else)\b", line.strip()):
            assert line.endswith("{"), line


@requiresGenerator
@pytest.mark.parametrize("resource", RESOURCE_IDS)
def test_hygieneNoTrailingSpaceAndTheBraceSitsOnTheStatementLine(generated, resource):
    source = generated(resource)

    assert not [line for line in source.splitlines() if line != line.rstrip()]
    assert "){" not in source
    assert not [line for line in source.splitlines() if line.strip() == "{"]


# ---------------------------------------------------------------- comportamento real do construtor
CONSTRUCTOR_MAIN = """
import com.starkbank.SplitProfile;
import com.starkbank.Transaction;
import java.util.HashMap;
import java.util.Map;

public class Main {
    public static void main(String[] args) throws Exception {
        for (Object delay : new Object[]{604800, 604800L, 604800.0, 604800f}) {
            Map<String, Object> data = new HashMap<>();
            data.put("interval", "week");
            data.put("delay", delay);
            SplitProfile profile = new SplitProfile(data);
            System.out.println("delay " + delay.getClass().getSimpleName() + " -> " + profile.delay + " " + profile.status);
        }
        Map<String, Object> withoutDelay = new HashMap<>();
        withoutDelay.put("interval", "week");
        System.out.println("delay absent -> " + new SplitProfile(withoutDelay).delay);
        for (Object amount : new Object[]{100, 100L, 100.0}) {
            Map<String, Object> data = new HashMap<>();
            data.put("amount", amount);
            data.put("description", "Tony Stark");
            System.out.println("amount " + amount.getClass().getSimpleName() + " -> " + new Transaction(data).amount);
        }
        for (String key : new String[]{"status", "created", "updated"}) {
            Map<String, Object> data = new HashMap<>();
            data.put("interval", "week");
            data.put(key, "x");
            try {
                new SplitProfile(data);
                System.out.println("accepted " + key);
            } catch (Exception exception) {
                System.out.println("rejected " + key + " -> " + exception.getMessage());
            }
        }
        Map<String, Object> transactionData = new HashMap<>();
        transactionData.put("balance", 1L);
        try {
            new Transaction(transactionData);
            System.out.println("accepted balance");
        } catch (Exception exception) {
            System.out.println("rejected balance -> " + exception.getMessage());
        }
    }
}
"""


@pytest.fixture(scope="module")
def constructorRun(tmp_path_factory, builtOnce, javaRuntimeClasspath):
    classpath = javaRuntimeClasspath
    work = tmp_path_factory.mktemp("constructor-runtime")
    sources = []
    for resource in ("SplitProfile", "Transaction"):
        code, out, target = builtOnce(resource, "java", *dict(RESOURCES)[resource])
        assert code == 0, out
        copy = work / "src/com/starkbank" / f"{resource}.java"
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(target / JAVA_DIR / f"{resource}.java", copy)
        sources.append(str(copy))
    (work / "src/Main.java").write_text(CONSTRUCTOR_MAIN, encoding="utf-8")

    # os arquivos gerados vencem o do real no -sourcepath; o resto do real so entra se alguem o referencia
    compiled = subprocess.run(
        ["javac", "-d", str(work / "out"), "-cp", classpath, "-sourcepath", str(REFERENCES["java"] / "src/main/java"),
         *sources, str(work / "src/Main.java")],
        capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run(["java", "-cp", f"{work / 'out'}:{classpath}", "Main"], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    return ran.stdout.splitlines()


@requiresGenerator
def test_theGeneratedConstructorAcceptsAnyNumericTypeInTheMap(constructorRun):
    assert "delay Integer -> 604800 null" in constructorRun
    assert "delay Long -> 604800 null" in constructorRun
    assert "delay Double -> 604800 null" in constructorRun
    assert "delay Float -> 604800 null" in constructorRun
    assert "delay absent -> null" in constructorRun
    assert "amount Integer -> 100" in constructorRun
    assert "amount Long -> 100" in constructorRun
    assert "amount Double -> 100" in constructorRun


@requiresGenerator
def test_theGeneratedConstructorRejectsAKeyThatOnlyTheReturnCarries(constructorRun):
    rejected = [line for line in constructorRun if line.startswith("rejected")]

    assert not [line for line in constructorRun if line.startswith("accepted")]
    assert rejected == [
        "rejected status -> Unknown parameters used in constructor: [status]",
        "rejected created -> Unknown parameters used in constructor: [created]",
        "rejected updated -> Unknown parameters used in constructor: [updated]",
        "rejected balance -> Unknown parameters used in constructor: [balance]",
    ]
