import re
import yaml
import pytest

from conftest import REPO_ROOT, requiresGenerator, requiresJavac

SUBRESOURCES = ("CardMethod", "MerchantCountry", "MerchantCategory", "Institution")
CONVENTIONS = yaml.safe_load((REPO_ROOT / "apis/type-conventions.yaml").read_text(encoding="utf-8"))
OPEN_CLASSES = set(CONVENTIONS["javaOpenClasses"])
LIST_QUERY_RESOURCES = set(CONVENTIONS["javaQueryShapes"])


def _artifact(builtOnce, resource: str) -> str:
    code, out, target = builtOnce(resource, "java")
    assert code == 0, out
    return (target / f"src/main/java/com/starkbank/{resource}.java").read_text(encoding="utf-8")


@requiresGenerator
@requiresJavac
@pytest.mark.parametrize("resource", SUBRESOURCES)
def test_subResourceGeneratesAsSubResource(builtOnce, resource):
    source = _artifact(builtOnce, resource)
    modifier = "public" if resource in OPEN_CLASSES else "public final"

    assert f"{modifier} class {resource} extends SubResource {{" in source
    classData = "ClassData" if resource in LIST_QUERY_RESOURCES else "SubResource.ClassData"
    assert f"static {classData} data = new {classData}({resource}.class, \"{resource}\");" in source
    assert "import com.starkcore.utils.SubResource;" in source
    assert "import com.starkbank.utils.Resource;" not in source
    assert "super(" not in source
    assert "String id" not in source
    returned = "List" if resource in LIST_QUERY_RESOURCES else "Generator"
    assert f"public static {returned}<{resource}> query(" in source


@requiresGenerator
@requiresJavac
def test_subResourceGatePassesStrictAndStyle(builtOnce):
    code, out, _ = builtOnce("CardMethod", "java")

    assert code == 0, out
    assert "sintaxe (javac)" in out


@requiresGenerator
def test_resourceKeepsItsResourceShape(builtOnce):
    source = _artifact(builtOnce, "SplitProfile")

    assert "public final class SplitProfile extends Resource {" in source
    assert "import com.starkbank.utils.Resource;" in source
    assert "static ClassData data = new ClassData(SplitProfile.class" in source
    assert "super(id);" in source
    assert "super(null);" in source


@requiresGenerator
@requiresJavac
def test_propertyNamedDataRenamesTheStaticClassData(builtOnce):
    source = _artifact(builtOnce, "InvoicePullSubscription")
    outer = source[:source.index("public final static class Log")]

    assert 'static ClassData classData = new ClassData(InvoicePullSubscription.class, ' in outer
    assert '"InvoicePullSubscription");' in outer
    assert re.search(r"^    public [\w<>, ]+ data;$", outer, re.M)
    assert "public InvoicePullSubscription(Map<String, Object> classData) throws Exception {" in outer
    assert "new HashMap<>(classData)" in outer
    assert "@param classData map of properties" in outer
    assert "Rest.getId(classData," in outer
    assert re.search(r"Rest\.\w+\(data,", outer) is None
    assert 'dataCopy.remove("data")' in outer
    assert 'static ClassData data = new ClassData(Log.class, "InvoicePullSubscriptionLog")' in source


@requiresGenerator
def test_resourceWithoutDataPropertyKeepsStaticData(builtOnce):
    source = _artifact(builtOnce, "SplitProfile")

    assert "static ClassData data = new ClassData(SplitProfile.class" in source
    assert "classData" not in source


ALL_OPERATIONS = ("create", "put", "get", "query", "page", "update", "delete", "cancel", "pdf", "log")


def _generatedWithEveryOperation(buildResource, tmpPath, monkeypatch) -> str:
    entry = {f"x-sdk-{operation}": True for operation in ALL_OPERATIONS}
    log = buildResource.loadTool("java-code").renderLog(
        "Widget", buildResource.loadTool("extract-schema").JAVA_LOG_DEFAULT, "widget"
    )
    entry |= {"x-sdk-java-create": {"isList": True}, "x-sdk-java-code": {"log": log}, "x-sdk-log-entity": "widget", "type": "object", "properties": {
        "id": {"type": "string", "x-sdk-id": True},
        "data": {"type": "object"},
        "amount": {"type": "integer"},
    }}
    spec = tmpPath / "spec.yaml"
    create = {"type": "object", "properties": {"amount": {"type": "integer"}}}
    spec.write_text(yaml.safe_dump({
        "openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {},
        "components": {"schemas": {"Widget": entry, "WidgetCreate": create}},
    }), encoding="utf-8")
    monkeypatch.chdir(REPO_ROOT)

    code = buildResource.generate("Widget", buildResource.GENERATORS["java"][0], tmpPath / "out", specFile=str(spec))

    assert code == 0
    return (tmpPath / "out/src/main/java/com/starkbank/Widget.java").read_text(encoding="utf-8")


@requiresGenerator
def test_classDataRenameReachesEveryRestCallOfTheOuterClass(buildResource, tmpPath, monkeypatch):
    source = _generatedWithEveryOperation(buildResource, tmpPath, monkeypatch)
    outer = source[:source.index("public final static class Log")]

    assert "import com.starkbank.utils.Rest;" in outer
    assert "import com.starkbank.utils.Generator;" in outer
    assert re.search(r"Rest\.\w+\(data,", outer) is None
    for call in ("put", "post", "patch", "getContent", "delete", "getId", "getStream", "getPage"):
        assert f"Rest.{call}(classData," in outer, call
    assert "Rest.getId(data," in source[source.index("public final static class Log"):]
