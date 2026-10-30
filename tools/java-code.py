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
    "numericGetters": {"Integer": "intValue", "Long": "longValue", "Float": "floatValue", "Double": "doubleValue"},
}


def renderMapConstructor(schema: dict, entity: dict, profile: dict = JAVA_CODE_PROFILE) -> dict:
    """Linhas do corpo de `X(Map)` ja prontas; o Mustache so as emoldura.

    Return-only recebe `null` e nao sai do Map, entao a chave dele cai em "Unknown parameters" (168 campos
    `null` contra 24 lidos, em 27 arquivos). `needsSuppress` so quando algum cast do corpo e generico."""
    properties = schema.get("properties") or {}
    lines = []
    castTypes = []
    for field in entity["fields"]:
        if field["isId"]:
            continue
        if field["returnOnly"]:
            lines.append(f"this.{field['name']} = null;")
            continue
        try:
            javaType = javaTypeOf(properties[field["name"]], profile)
        except ValueError as error:
            raise ValueError(f"{field['name']}: {error}") from error
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
    getter = profile["numericGetters"].get(javaType)
    if getter is None:
        return [f'this.{name} = ({javaType}) dataCopy.remove("{name}");']
    numberType = profile["numberType"]
    return [
        f'{numberType} {name} = ({numberType}) dataCopy.remove("{name}");',
        f"this.{name} = {name} == null ? null : {name}.{getter}();",
    ]
