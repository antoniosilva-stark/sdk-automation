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
    return javaCode.renderJavaCode(schema, entity, "Widget", {})


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
    body = javaCode.renderJavaCode(schema, entity, "Widget", {}, profile)["ctorMapBody"]
    assert body[1].endswith("delay.shortValue();")


def test_splitProfileFromTheRepositorySchema(javaCode, javaDocs):
    schemas = yaml.safe_load((SCHEMAS / "splitprofile.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    entity = javaDocs.describeEntity(schemas["SplitProfile"], schemas["SplitProfileCreate"])
    body = javaCode.renderJavaCode(schemas["SplitProfile"], entity, "SplitProfile", {})["ctorMapBody"]
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
        javaCode.renderJavaCode(schema, entity, "Widget", {})


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


def _fields(javaCode, properties: dict, overrides: dict | None = None) -> list:
    return javaCode.renderFields({"properties": properties}, "Widget", overrides or {}, javaCode.JAVA_CODE_PROFILE)


@pytest.mark.parametrize("prop, expected", [
    ({"type": "integer"}, "Integer"),
    ({"type": "integer", "format": "int64"}, "Long"),
    ({"type": "number"}, "Number"),
    ({"type": "number", "format": "double"}, "Double"),
    ({"type": "boolean"}, "Boolean"),
    ({"type": "string", "format": "date-time"}, "String"),
    ({"type": "object"}, "Object"),
    ({"type": "array", "items": {"type": "string"}}, "String[]"),
    ({"type": "array", "items": {"type": "object"}}, "List<Object>"),
])
def test_fieldTypeDefaultsToTheProfileRule(javaCode, prop, expected):
    assert _fields(javaCode, {"value": prop}) == [{"name": "value", "javaType": expected, "isId": False}]


def test_fieldsKeepSchemaOrderAndMarkTheId(javaCode):
    fields = _fields(javaCode, {
        "id": {"type": "string", "x-sdk-id": True},
        "amount": {"type": "integer", "format": "int64"},
        "status": {"type": "string", "readOnly": True},
    })

    assert [(field["name"], field["javaType"], field["isId"]) for field in fields] == [
        ("id", "String", True),
        ("amount", "Long", False),
        ("status", "String", False),
    ]


def test_resourceOverrideBeatsTheProfileDefault(javaCode):
    fields = _fields(javaCode, {"amount": {"type": "integer", "format": "int64"}}, {"amount": "Integer"})

    assert fields[0]["javaType"] == "Integer"


def test_overrideOfAFieldTheSchemaDoesNotHaveFailsInsteadOfBeingIgnored(javaCode):
    with pytest.raises(ValueError, match="Widget.amout"):
        _fields(javaCode, {"amount": {"type": "integer"}}, {"amout": "Long"})


def test_fieldWithoutProfileRuleFailsNamingResourceAndField(javaCode):
    with pytest.raises(ValueError, match="Widget.blob: tipo sem regra no perfil"):
        _fields(javaCode, {"blob": {"type": "string", "format": "binary"}})


def test_overrideReachesTheMapConstructorCastAndTheFieldsOfTheSameRender(javaCode):
    schema, entity = _entity({"amount": {"type": "integer", "format": "int64"}}, ("amount",))

    rendered = javaCode.renderJavaCode(schema, entity, "Widget", {"amount": "Integer"})

    assert rendered["ctorMapBody"][1] == "this.amount = amount == null ? null : amount.intValue();"
    assert [field["javaType"] for field in rendered["fields"]] == ["Integer"]


def _overridden(javaCode, prop: dict, javaType: str, creatable: bool = True) -> dict:
    schema, entity = _entity({"value": prop}, ("value",) if creatable else ())
    return javaCode.renderJavaCode(schema, entity, "Widget", {"value": javaType})


@pytest.mark.parametrize("javaType, getter", [("long", "longValue"), ("int", "intValue")])
def test_primitiveIsReadThroughNumberWithoutANullBranch(javaCode, javaType, getter):
    rendered = _overridden(javaCode, {"type": "integer"}, javaType)

    assert rendered["ctorMapBody"] == [f'this.value = ((Number) dataCopy.remove("value")).{getter}();']
    assert rendered["ctorMapSuppress"] is False


@pytest.mark.parametrize("javaType", ["long", "int"])
def test_returnOnlyPrimitiveGetsZeroBecausePrimitiveCannotBeNull(javaCode, javaType):
    rendered = _overridden(javaCode, {"type": "integer"}, javaType, creatable=False)

    assert rendered["ctorMapBody"] == ["this.value = 0;"]


def test_returnOnlyBoxedFieldStillGetsNull(javaCode):
    rendered = _overridden(javaCode, {"type": "integer"}, "Long", creatable=False)

    assert rendered["ctorMapBody"] == ["this.value = null;"]


def test_numberOverrideIsReadWithADirectCast(javaCode):
    rendered = _overridden(javaCode, {"type": "integer"}, "Number")

    assert rendered["ctorMapBody"] == ['this.value = (Number) dataCopy.remove("value");']


def test_stringOverrideOnANumericFieldIsReadWithADirectCast(javaCode):
    rendered = _overridden(javaCode, {"type": "integer"}, "String")

    assert rendered["ctorMapBody"] == ['this.value = (String) dataCopy.remove("value");']
    assert rendered["ctorMapSuppress"] is False


@pytest.mark.parametrize("javaType", ["Map<String, Object>", "HashMap<String, Object>", "List<String>"])
def test_genericOverrideIsCastDirectlyAndTurnsOnSuppress(javaCode, javaType):
    rendered = _overridden(javaCode, {"type": "object"}, javaType)

    assert rendered["ctorMapBody"] == [f'this.value = ({javaType}) dataCopy.remove("value");']
    assert rendered["ctorMapSuppress"] is True


@pytest.mark.parametrize("override", ["Lon", "BigDecimal", "float", "", 5, ["a"], None])
def test_overrideTypeOutsideWhatTheMapConstructorReadsFails(javaCode, override):
    with pytest.raises(ValueError, match="Widget.amount: tipo de override"):
        _fields(javaCode, {"amount": {"type": "integer"}}, {"amount": override})


def test_overrideOfTheIdFieldFailsInsteadOfBeingIgnored(javaCode):
    with pytest.raises(ValueError, match="Widget.id: override de campo id"):
        _fields(javaCode, {"id": {"type": "string", "x-sdk-id": True}}, {"id": "Long"})


@pytest.mark.parametrize("javaType", [
    "String", "Integer", "Long", "Number", "Float", "Double", "Boolean", "Object", "long", "int",
    "String[]", "List<Object>", "Map<String, Object>", "HashMap<String, Object>", "List<String>",
])
def test_overrideTypeTheMapConstructorReadsIsAccepted(javaCode, javaType):
    assert _fields(javaCode, {"amount": {"type": "integer"}}, {"amount": javaType})[0]["javaType"] == javaType


_RULE_FIELDS = {"key": "String", "value": "Number"}
_MAP_RULE = {"fields": _RULE_FIELDS, "constructors": [["key", "value"]], "map": ["key", "value"]}
_ARRAY = {"type": "array", "items": {"type": "object"}}


def _renderList(javaCode, javaType, innerClasses=None, resourceNames=frozenset(), properties=None):
    schema, entity = _entity(properties or {"rules": _ARRAY}, tuple(properties or ["rules"]))
    return javaCode.renderJavaCode(
        schema,
        entity,
        "Widget",
        {"rules": javaType},
        innerClasses=innerClasses,
        resourceNames=resourceNames,
    )


def test_listOfAnInnerClassIsReadThroughItsParseHelperAndTypedInTheFieldAndTheConstructor(javaCode):
    rendered = _renderList(javaCode, "List<Widget.Rule>", {"Rule": _MAP_RULE})

    assert rendered["fields"][0]["javaType"] == "List<Widget.Rule>"
    assert rendered["ctorFields"][0]["javaType"] == "List<Widget.Rule>"
    assert rendered["ctorMapBody"] == ['this.rules = parseRules((List<Object>) dataCopy.remove("rules"));']
    assert rendered["ctorMapSuppress"] is True


def test_theParseHelperOfAnInnerClassWithAMapConstructorBuildsItFromTheMap(javaCode):
    helper = _renderList(javaCode, "List<Widget.Rule>", {"Rule": _MAP_RULE})["ctorMapParsers"]

    assert [item["signature"] for item in helper] == [
        "private static List<Widget.Rule> parseRules(List<Object> items) throws Exception",
    ]
    assert helper[0]["body"] == [
        "if (items == null) {",
        "    return null;",
        "}",
        "List<Widget.Rule> parsed = new ArrayList<>();",
        "for (Object item : items) {",
        "    if (item instanceof Widget.Rule) {",
        "        parsed.add((Widget.Rule) item);",
        "        continue;",
        "    }",
        "    if (item instanceof Map) {",
        "        parsed.add(new Widget.Rule((Map<String, Object>) item));",
        "        continue;",
        "    }",
        '    throw new Exception("Unknown type \\"" + item.getClass() + "\\", use Widget.Rule or HashMap");',
        "}",
        "return parsed;",
    ]


def test_theParseHelperOfAnInnerClassWithoutAMapConstructorReadsEachArgumentByItsFieldType(javaCode):
    fields = {"text": "String", "amount": "Integer", "ratio": "Number", "size": "long", "count": "int", "tags": "String[]"}
    inner = {"Rule": {"fields": fields, "constructors": [list(fields)]}}

    body = _renderList(javaCode, "List<Rule>", inner)["ctorMapParsers"][0]["body"]

    assert "    if (item instanceof Rule) {" in body
    assert "        Map<String, Object> map = (Map<String, Object>) item;" in body
    assert [line for line in body if line.startswith("            ")] == [
        '            (String) map.get("text"),',
        '            map.get("amount") == null ? null : ((Number) map.get("amount")).intValue(),',
        '            (Number) map.get("ratio"),',
        '            ((Number) map.get("size")).longValue(),',
        '            ((Number) map.get("count")).intValue(),',
        '            (String[]) map.get("tags")',
    ]
    assert "        parsed.add(new Rule(" in body
    assert "        ));" in body


def test_theParseHelperOfAGeneratedResourceBuildsItFromTheMapConstructor(javaCode):
    helper = _renderList(javaCode, "List<Split>", {}, frozenset({"Split"}))["ctorMapParsers"][0]

    assert helper["signature"] == "private static List<Split> parseRules(List<Object> items) throws Exception"
    assert "        parsed.add(new Split((Map<String, Object>) item));" in helper["body"]


def test_aResourceTypeIsAPlainCastWithoutHelper(javaCode):
    rendered = _renderList(javaCode, "Resource")

    assert rendered["ctorMapBody"] == ['this.rules = (Resource) dataCopy.remove("rules");']
    assert rendered["ctorMapParsers"] == []
    assert rendered["ctorMapSuppress"] is False


@pytest.mark.parametrize("javaType", ["List<String>", "List<Object>"])
def test_theListTypesTheProfileAlreadyKnowsGetNoHelper(javaCode, javaType):
    rendered = _renderList(javaCode, javaType)

    assert rendered["ctorMapParsers"] == []
    assert rendered["ctorMapBody"] == [f'this.rules = ({javaType}) dataCopy.remove("rules");']


def test_aReturnOnlyListOfInnerClassesIsNullAndGetsNoHelper(javaCode):
    schema, entity = _entity({"rules": _ARRAY}, ())
    rendered = javaCode.renderJavaCode(schema, entity, "Widget", {"rules": "List<Widget.Rule>"},
                                       innerClasses={"Rule": _MAP_RULE})

    assert rendered["ctorMapBody"] == ["this.rules = null;"]
    assert rendered["ctorMapParsers"] == []


def test_withoutOverrideTheListOfObjectIsStillCastRawAndThereIsNoHelper(javaCode):
    rendered = _render(javaCode, {"rules": _ARRAY}, ("rules",))

    assert rendered["ctorMapBody"] == ['this.rules = (List<Object>) dataCopy.remove("rules");']
    assert rendered["ctorMapParsers"] == []


@pytest.mark.parametrize("javaType, inner, resourceNames, message", [
    ("List<Foo>", {"Rule": _MAP_RULE}, {"Split"}, "Widget.rules: elemento 'Foo' de List nao e classe interna nem recurso gerado"),
    ("List<Other.Rule>", {"Rule": _MAP_RULE}, {"Other"}, "Widget.rules: qualificador 'Other' de 'Other.Rule' nao e o proprio recurso"),
    ("List<widget.Rule>", {"Rule": _MAP_RULE}, set(), "Widget.rules: qualificador 'widget' de 'widget.Rule' nao e o proprio recurso"),
    ("List<Widget.Nope>", {"Rule": _MAP_RULE}, set(), "Widget.rules: classe interna 'Nope' nao esta em javaInnerClasses"),
    ("List<rule>", {"Rule": _MAP_RULE}, set(), "Widget.rules: elemento 'rule' de List nao e classe interna nem recurso gerado"),
    ("List<Integer>", {}, {"Split"}, "Widget.rules: elemento 'Integer' de List nao e classe interna nem recurso gerado"),
    ("list<Rule>", {"Rule": _MAP_RULE}, set(), "Widget.rules: tipo de override"),
    ("List<>", {}, set(), "Widget.rules: tipo de override"),
    ("List<Widget.Rule.Sub>", {"Rule": _MAP_RULE}, set(), "Widget.rules: tipo de override"),
])
def test_invalidListOfSubObjectsFailsNamingTheFieldAndTheProblem(javaCode, javaType, inner, resourceNames, message):
    with pytest.raises(ValueError, match=message):
        _renderList(javaCode, javaType, inner, frozenset(resourceNames))


def test_anInnerClassThatCannotBeBuiltFromAMapFailsNamingTheField(javaCode):
    inner = {"Rule": {"fields": _RULE_FIELDS, "constructors": [[]]}}

    with pytest.raises(ValueError, match="Widget.rules: classe interna 'Rule' nao tem construtor que receba campos"):
        _renderList(javaCode, "List<Widget.Rule>", inner)
