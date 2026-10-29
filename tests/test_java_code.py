import yaml
import pytest

from conftest import REPO_ROOT

SCHEMAS = REPO_ROOT / "apis/schemas"


def _entity(properties: dict, creatable: tuple) -> tuple:
    """Schema e entidade como o `describeEntity` os entrega: id marcado, return-only = fora do XCreate."""
    schema = {"properties": properties}
    fields = [{"name": name, "isId": bool(prop.get("x-sdk-id")),
               "returnOnly": name not in creatable and not prop.get("x-sdk-id")}
              for name, prop in properties.items()]
    return schema, {"fields": fields}


def _render(javaCode, properties: dict, creatable: tuple) -> dict:
    schema, entity = _entity(properties, creatable)
    return javaCode.renderMapConstructor(schema, entity)


def test_integerIsReadAsNumberAndConvertedWithoutCast(javaCode):
    rendered = _render(javaCode, {"delay": {"type": "integer"}}, ("delay",))
    assert rendered["ctorMapBody"] == [
        'Number delay = (Number) dataCopy.remove("delay");',
        "this.delay = delay == null ? null : delay.intValue();",
    ]


def test_int64IsConvertedWithLongValue(javaCode):
    rendered = _render(javaCode, {"amount": {"type": "integer", "format": "int64"}}, ("amount",))
    assert rendered["ctorMapBody"] == [
        'Number amount = (Number) dataCopy.remove("amount");',
        "this.amount = amount == null ? null : amount.longValue();",
    ]


@pytest.mark.parametrize("numberFormat, getter", [("float", "floatValue"), ("double", "doubleValue")])
def test_floatingPointIsConvertedWithItsOwnValueMethod(javaCode, numberFormat, getter):
    rendered = _render(javaCode, {"fine": {"type": "number", "format": numberFormat}}, ("fine",))
    assert rendered["ctorMapBody"][1] == f"this.fine = fine == null ? null : fine.{getter}();"


def test_numberWithoutFormatIsTheNumberTypeItselfSoItNeedsNoConversion(javaCode):
    rendered = _render(javaCode, {"ratio": {"type": "number"}}, ("ratio",))
    assert rendered["ctorMapBody"] == ['this.ratio = (Number) dataCopy.remove("ratio");']


def test_stringKeepsTheDirectCast(javaCode):
    rendered = _render(javaCode, {"interval": {"type": "string"}}, ("interval",))
    assert rendered["ctorMapBody"] == ['this.interval = (String) dataCopy.remove("interval");']


def test_dateTimeStringKeepsTheDirectCast(javaCode):
    rendered = _render(javaCode, {"due": {"type": "string", "format": "date"}}, ("due",))
    assert rendered["ctorMapBody"] == ['this.due = (String) dataCopy.remove("due");']


def test_arrayOfStringIsCastToStringArray(javaCode):
    rendered = _render(javaCode, {"tags": {"type": "array", "items": {"type": "string"}}}, ("tags",))
    assert rendered["ctorMapBody"] == ['this.tags = (String[]) dataCopy.remove("tags");']


def test_arrayOfObjectIsCastToListOfObject(javaCode):
    rendered = _render(javaCode, {"rules": {"type": "array", "items": {"type": "object"}}}, ("rules",))
    assert rendered["ctorMapBody"] == ['this.rules = (List<Object>) dataCopy.remove("rules");']


def test_booleanKeepsTheDirectCast(javaCode):
    rendered = _render(javaCode, {"isDelivered": {"type": "boolean"}}, ("isDelivered",))
    assert rendered["ctorMapBody"] == ['this.isDelivered = (Boolean) dataCopy.remove("isDelivered");']


def test_returnOnlyFieldIsNullAndNeverReadFromTheMap(javaCode):
    properties = {"status": {"type": "string"}, "fee": {"type": "integer"}, "created": {"type": "string"}}
    rendered = _render(javaCode, properties, ())
    assert rendered["ctorMapBody"] == ["this.status = null;", "this.fee = null;", "this.created = null;"]
    assert "dataCopy.remove" not in "".join(rendered["ctorMapBody"])


def test_idIsNotPartOfTheBody(javaCode):
    properties = {"id": {"type": "string", "x-sdk-id": True}, "name": {"type": "string"}}
    rendered = _render(javaCode, properties, ("name",))
    assert rendered["ctorMapBody"] == ['this.name = (String) dataCopy.remove("name");']


def test_fieldsKeepTheSchemaOrder(javaCode):
    properties = {"b": {"type": "string"}, "a": {"type": "integer"}, "c": {"type": "string"}}
    rendered = _render(javaCode, properties, ("a", "b"))
    assert [line.split(" ")[0] for line in rendered["ctorMapBody"]] == ["this.b", "Number", "this.a", "this.c"]


@pytest.mark.parametrize("properties, expected", [
    ({"rules": {"type": "array", "items": {"type": "object"}}}, True),
    ({"name": {"type": "string"}}, False),
    ({"tags": {"type": "array", "items": {"type": "string"}}}, False),
    ({"delay": {"type": "integer"}}, False),
])
def test_suppressIsNeededOnlyForAGenericCastInTheBody(javaCode, properties, expected):
    rendered = _render(javaCode, properties, tuple(properties))
    assert rendered["ctorMapSuppress"] is expected


def test_aGenericFieldThatIsReturnOnlyIsNotCastSoItNeedsNoSuppress(javaCode):
    properties = {"rules": {"type": "array", "items": {"type": "object"}}}
    rendered = _render(javaCode, properties, ())
    assert rendered["ctorMapSuppress"] is False


def test_theProfileIsData(javaCode):
    profile = {**javaCode.JAVA_CODE_PROFILE, "numericGetters": {"Integer": "shortValue"}}
    schema, entity = _entity({"delay": {"type": "integer"}}, ("delay",))
    body = javaCode.renderMapConstructor(schema, entity, profile)["ctorMapBody"]
    assert body[1].endswith("delay.shortValue();")


def test_splitProfileFromTheRepositorySchema(javaCode, javaDocs):
    schemas = yaml.safe_load((SCHEMAS / "splitprofile.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    entity = javaDocs.describeEntity(schemas["SplitProfile"], schemas["SplitProfileCreate"])
    body = javaCode.renderMapConstructor(schemas["SplitProfile"], entity)["ctorMapBody"]
    assert body == [
        'this.interval = (String) dataCopy.remove("interval");',
        'Number delay = (Number) dataCopy.remove("delay");',
        "this.delay = delay == null ? null : delay.intValue();",
        'this.tags = (String[]) dataCopy.remove("tags");',
        "this.status = null;",
        "this.created = null;",
        "this.updated = null;",
    ]


@pytest.mark.parametrize("prop", [
    {"type": "string", "format": "byte"},
    {"type": "string", "format": "binary"},
    {"type": "integer", "format": "int128"},
    {"type": "number", "format": "decimal"},
    {"type": "boolean", "format": "tristate"},
    {"type": "array", "items": {"type": "integer"}},
    {"type": "array", "items": {"type": "array"}},
    {"type": "array"},
    {"type": "null"},
    {},
])
def test_aTypeTheProfileDoesNotCoverFailsClosedNamingTheField(javaCode, prop):
    schema, entity = _entity({"widget": prop}, ("widget",))

    with pytest.raises(ValueError, match="widget"):
        javaCode.renderMapConstructor(schema, entity)


def _everySchemaProperty() -> list:
    found = []
    for path in sorted(SCHEMAS.glob("*.yaml")):
        schemas = yaml.safe_load(path.read_text(encoding="utf-8"))["components"]["schemas"]
        for schemaName, schema in schemas.items():
            for name, prop in (schema.get("properties") or {}).items():
                found.append((f"{schemaName}.{name}", prop))
    return found


def test_everyPropertyOfEverySchemaFallsInACaseTheProfileCovers(javaCode):
    properties = _everySchemaProperty()

    assert len(properties) > 500
    for label, prop in properties:
        try:
            javaCode.javaTypeOf(prop, javaCode.JAVA_CODE_PROFILE)
        except ValueError as error:
            pytest.fail(f"{label}: {error}")


def test_theSweepNoticesACaseRemovedFromTheProfile(javaCode):
    profile = {**javaCode.JAVA_CODE_PROFILE, "types": {
        **javaCode.JAVA_CODE_PROFILE["types"],
        "integer": {"default": "Integer"},
    }}

    with pytest.raises(ValueError, match="int64"):
        for _, prop in _everySchemaProperty():
            javaCode.javaTypeOf(prop, profile)
