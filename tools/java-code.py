from collections import Counter


# Perfil do alvo em dados: a proxima linguagem troca este dicionario, nao o codigo abaixo.
# Contagens da ME 4 (46 recursos do sdk-java real) em cada regra.
JAVA_CODE_PROFILE = {
    # tipo Java que o gerador emite por (type, format) do schema (generator-java-config.yaml: BigDecimal -> Number,
    # datas e uri -> String); "default" e o schema sem format. O que nao esta aqui falha em vez de ser adivinhado:
    # o gerador emitiria outro tipo (byte[], File, UUID...) e o cast do corpo divergiria da declaracao do campo.
    # O template nunca emite primitivo, entao a regra de `(long)`/`(int)` do real (7 e 1 arquivos) nao se aplica
    "types": {
        "string": {"default": "String", "date-time": "String", "date": "String", "uri": "String"},
        "boolean": {"default": "Boolean"},
        "object": {"default": "Object"},
        "integer": {"default": "Integer", "int32": "Integer", "int64": "Long"},
        "number": {"default": "Number", "float": "Float", "double": "Double"},
    },
    # array de string e String[] e de objeto e List<Object> (o `items.isString` do template); outro item nao e coberto
    "arrayTypes": {"string": "String[]", "object": "List<Object>"},
    # cast direto de numerico lanca ClassCastException para Long no lugar de Integer (A2); o real le como Number
    # e converte null-safe (SplitProfile). `Number` em si e lido direto: 10 de 10 no real, e nao ha o que converter
    "numberType": "Number",
    # primitivo (so por override) nao aceita null: le-se como o real le (`((Number) x).longValue()`, sem ramo null) e
    # o return-only recebe o zero em vez de `null`, que nao compila
    "primitives": {"long": {"getter": "longValue", "zero": "0"}, "int": {"getter": "intValue", "zero": "0"}},
    # genericos que o Map-ctor le por cast direto: so os que o override pode pedir; tipo fora deste conjunto e dos
    # valores do perfil falha, em vez de gerar `Lon` ou um cast que nao compila
    "genericTypes": ["Map<String, Object>", "HashMap<String, Object>", "List<String>", "String[]"],
    "numericGetters": {"Integer": "intValue", "Long": "longValue", "Float": "floatValue", "Double": "doubleValue"},
}


def renderJavaCode(
    schema: dict,
    entity: dict,
    resource: str,
    overrides: dict,
    profile: dict = JAVA_CODE_PROFILE,
    isOpen: bool = False,
    ctorOrder: list | None = None,
    ctorTypes: dict | None = None,
) -> dict:
    """`x-sdk-java-code` do recurso: `fields` (declaracao) e o corpo do Map-ctor, os dois sobre o mesmo tipo por
    campo, para o cast nunca divergir da declaracao. `ctorFields`: os mesmos campos na ordem e com o tipo do
    construtor all-args, que no real e dado do Java. `open`: a classe externa nao e `final`."""
    fields = renderFields(schema, resource, overrides, profile)
    ctorFields = renderConstructorFields(fields, resource, ctorOrder, ctorTypes or {}, profile)
    return {"fields": fields, "ctorFields": ctorFields, "open": isOpen, **renderMapConstructor(entity, fields, profile)}


def renderFields(schema: dict, resource: str, overrides: dict, profile: dict = JAVA_CODE_PROFILE) -> list:
    """Todos os campos na ordem do schema (inclusive id e return-only) com o tipo Java ja decidido: o override do
    recurso vence o default do perfil. Override de campo inexistente falha, senao um typo viraria no-op calado."""
    properties = schema.get("properties") or {}
    unknown = sorted(set(overrides) - set(properties))
    if unknown:
        raise ValueError(f"{resource}.{unknown[0]}: override de campo que o schema nao tem")
    fields = []
    for name, prop in properties.items():
        isId = bool(prop.get("x-sdk-id"))
        if name in overrides and isId:
            raise ValueError(f"{resource}.{name}: override de campo id, que o template sempre emite como String")
        javaType = overrides[name] if name in overrides else fieldTypeOf(resource, name, prop, profile)
        if name in overrides:
            checkOverrideType(resource, name, javaType, profile)
        fields.append({"name": name, "javaType": javaType, "isId": isId})
    return fields


def renderConstructorFields(fields: list, resource: str, order: list | None, types: dict, profile: dict) -> list:
    """Sem entrada o construtor segue a ordem e os tipos do campo; a ordem so aceita permutacao exata dos campos, senao
    uma posicao faltando geraria um construtor que nao atribui um campo."""
    checkConstructorTypes(fields, resource, types, profile)
    typed = [{**field, "javaType": types.get(field["name"], field["javaType"])} for field in fields]
    if order is None:
        return typed
    checkConstructorOrder(fields, resource, order)
    typedByName = {field["name"]: field for field in typed}
    return [typedByName[name] for name in order]


def checkConstructorOrder(fields: list, resource: str, order: list) -> None:
    names = {field["name"] for field in fields}
    missing = sorted(names - set(order))
    extra = sorted(set(order) - names)
    repeated = sorted(name for name, count in Counter(order).items() if count > 1)
    if missing or extra or repeated:
        raise ValueError(
            f"{resource}: ordem do construtor nao e permutacao dos campos "
            f"(faltam: {', '.join(missing)}; sobram: {', '.join(extra)}; repetidos: {', '.join(repeated)})"
        )


def checkConstructorTypes(fields: list, resource: str, types: dict, profile: dict) -> None:
    fieldByName = {field["name"]: field for field in fields}
    for name, javaType in types.items():
        if name not in fieldByName:
            raise ValueError(f"{resource}.{name}: tipo de construtor de campo que o schema nao tem")
        if fieldByName[name]["isId"]:
            raise ValueError(f"{resource}.{name}: tipo de construtor de campo id, que o template emite como String")
        checkOverrideType(resource, name, javaType, profile)


def checkOverrideType(resource: str, name: str, javaType, profile: dict) -> None:
    accepted = supportedTypes(profile)
    if isinstance(javaType, str) and javaType in accepted:
        return
    raise ValueError(
        f"{resource}.{name}: tipo de override {javaType!r} que o Map-ctor nao le; aceitos: {', '.join(accepted)}"
    )


def supportedTypes(profile: dict) -> list:
    declared = [javaType for formats in profile["types"].values() for javaType in formats.values()]
    declared += profile["arrayTypes"].values()
    declared += [*profile["primitives"], *profile["numericGetters"], profile["numberType"], *profile["genericTypes"]]
    return sorted(set(declared))


def fieldTypeOf(resource: str, name: str, prop: dict, profile: dict) -> str:
    try:
        return javaTypeOf(prop, profile)
    except ValueError as error:
        raise ValueError(f"{resource}.{name}: {error}") from error


def renderMapConstructor(entity: dict, fields: list, profile: dict = JAVA_CODE_PROFILE) -> dict:
    """Linhas do corpo de `X(Map)` ja prontas; o Mustache so as emoldura.

    Return-only recebe `null` e nao sai do Map, entao a chave dele cai em "Unknown parameters" (168 campos
    `null` contra 24 lidos, em 27 arquivos). `needsSuppress` so quando algum cast do corpo e generico."""
    javaTypeByName = {field["name"]: field["javaType"] for field in fields}
    lines = []
    castTypes = []
    for field in entity["fields"]:
        if field["isId"]:
            continue
        if field["returnOnly"]:
            zero = profile["primitives"].get(javaTypeByName[field["name"]], {}).get("zero", "null")
            lines.append(f"this.{field['name']} = {zero};")
            continue
        javaType = javaTypeByName[field["name"]]
        castTypes.append(javaType)
        lines += readLines(field["name"], javaType, profile)
    return {
        "ctorMapBody": lines,
        "ctorMapSuppress": any("<" in javaType for javaType in castTypes),
    }


def javaTypeOf(prop: dict, profile: dict) -> str:
    kind = prop.get("type")
    if kind == "array":
        return arrayTypeOf(prop.get("items") or {}, profile)
    formats = profile["types"].get(kind) or {}
    javaType = formats.get(prop.get("format") or "default")
    if javaType is None:
        raise ValueError(f"tipo sem regra no perfil: type={kind} format={prop.get('format')}")
    return javaType


def arrayTypeOf(items: dict, profile: dict) -> str:
    javaType = profile["arrayTypes"].get(items.get("type"))
    if javaType is None:
        raise ValueError(f"array sem regra no perfil: items type={items.get('type')}")
    return javaType


def readLines(name: str, javaType: str, profile: dict) -> list:
    primitive = profile["primitives"].get(javaType)
    if primitive is not None:
        return [f'this.{name} = ((Number) dataCopy.remove("{name}")).{primitive["getter"]}();']
    getter = profile["numericGetters"].get(javaType)
    if getter is None:
        return [f'this.{name} = ({javaType}) dataCopy.remove("{name}");']
    numberType = profile["numberType"]
    return [
        f'{numberType} {name} = ({numberType}) dataCopy.remove("{name}");',
        f"this.{name} = {name} == null ? null : {name}.{getter}();",
    ]
