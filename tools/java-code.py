from collections import Counter
from re import fullmatch


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
    "genericTypes": ["Map<String, Object>", "HashMap<String, Object>", "List<String>", "String[]", "Resource"],
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
    innerClasses: dict | None = None,
    resourceNames: frozenset = frozenset(),
) -> dict:
    """`x-sdk-java-code` do recurso: `fields` (declaracao) e o corpo do Map-ctor, os dois sobre o mesmo tipo por
    campo, para o cast nunca divergir da declaracao. `ctorFields`: os mesmos campos na ordem e com o tipo do
    construtor all-args, que no real e dado do Java. `open`: a classe externa nao e `final`. `innerClasses`: as classes
    internas simples do real, ja com os construtores e o corpo do Map-ctor prontos. `resourceNames`: os recursos
    geradores, para o override `List<Recurso>`; esse tipo e o `List<ClasseInterna>` saem do Map-ctor por um auxiliar
    `parseX` (`ctorMapParsers`), como no real."""
    fields = renderFields(schema, resource, overrides, profile, innerClasses or {}, resourceNames)
    ctorFields = renderConstructorFields(fields, resource, ctorOrder, ctorTypes or {}, profile)
    return {
        "fields": fields,
        "ctorFields": ctorFields,
        "open": isOpen,
        "innerClasses": renderInnerClasses(resource, innerClasses or {}, profile),
        **renderMapConstructor(entity, fields, profile, innerClasses or {}),
    }


INNER_CLASS_KEYS = {"fields", "constructors", "map", "final", "classData"}
# `Log` e `Page` o template ja emite dentro da classe externa; repetir o nome nao compilaria
TEMPLATE_INNER_CLASSES = ("Log", "Page")
MAP_CONSTRUCTOR_SIGNATURE = "Map<String, Object>"


def renderInnerClasses(resource: str, innerClasses: dict, profile: dict = JAVA_CODE_PROFILE) -> list:
    """Classes internas simples ja prontas para o Mustache: o real as escreve a mao e a spec nao as descreve, entao a
    definicao e dado do Java. Cada construtor atribui os campos recebidos e `null` nos demais; o Map-ctor le so os
    campos de `map`. Campo primitivo nao aceita null, entao tem de ser recebido e lido."""
    return [renderInnerClass(resource, name, definition, profile) for name, definition in innerClasses.items()]


def renderInnerClass(resource: str, name: str, definition: dict, profile: dict) -> dict:
    where = f"{resource}.{name}"
    checkInnerClass(where, name, definition, resource, profile)
    fields = definition["fields"]
    rendered = {
        "className": name,
        "isFinal": definition.get("final", True),
        "fields": [{"name": field, "javaType": javaType} for field, javaType in fields.items()],
    }
    if "classData" in definition:
        rendered["classDataLine"] = innerClassDataLine(name, definition["classData"])
    rendered["constructors"] = [renderInnerConstructor(names, fields) for names in definition.get("constructors") or []]
    if "map" in definition:
        rendered["map"] = renderInnerMapConstructor(definition["map"], fields, profile)
    return rendered


def checkInnerClass(where: str, name, definition, resource: str, profile: dict) -> None:
    checkInnerClassName(where, name, resource)
    if not isinstance(definition, dict):
        raise ValueError(f"{where}: definicao deve ser um mapa")
    unknown = sorted(str(key) for key in set(definition) - INNER_CLASS_KEYS)
    if unknown:
        raise ValueError(f"{where}: chave desconhecida: {unknown[0]}")
    fields = definition.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError(f"{where}: fields ausente ou vazio")
    for field, javaType in fields.items():
        checkInnerClassField(where, field, javaType, profile)
    constructors = definition.get("constructors") or []
    for names in constructors:
        checkInnerFieldNames(where, "construtor", names, fields, profile)
    if "map" in definition:
        checkInnerFieldNames(where, "map", definition["map"], fields, profile)
    checkInnerClassSignatures(where, constructors, fields, "map" in definition)
    if not isinstance(definition.get("final", True), bool):
        raise ValueError(f"{where}: final deve ser booleano")
    if "classData" in definition and not isIdentifier(definition["classData"], "[A-Z]"):
        raise ValueError(f"{where}: classData invalido: {definition['classData']!r}")


def isIdentifier(value, firstCharacter: str) -> bool:
    return isinstance(value, str) and fullmatch(rf"{firstCharacter}[A-Za-z0-9]*", value) is not None


def checkInnerClassName(where: str, name, resource: str) -> None:
    if not isIdentifier(name, "[A-Z]"):
        raise ValueError(f"{where}: nome de classe interna invalido")
    if name in TEMPLATE_INNER_CLASSES:
        raise ValueError(f"{where}: nome de classe interna que o template ja emite")
    if name == resource:
        raise ValueError(f"{where}: nome de classe interna igual ao da classe externa")


def checkInnerClassField(where: str, field, javaType, profile: dict) -> None:
    if not isIdentifier(field, "[a-z]"):
        raise ValueError(f"{where}.{field}: nome de campo invalido")
    checkOverrideType(where, field, javaType, profile)


def checkInnerFieldNames(where: str, label: str, names, fields: dict, profile: dict) -> None:
    """`names` so pode citar campo da classe, sem repeticao, e tem de cobrir os primitivos."""
    if not isinstance(names, list):
        raise ValueError(f"{where}: {label} deve ser uma lista de campos")
    unknown = [name for name in names if name not in fields]
    if unknown:
        raise ValueError(f"{where}: {label} cita campo que a classe nao tem: {unknown[0]}")
    repeated = sorted(name for name, count in Counter(names).items() if count > 1)
    if repeated:
        raise ValueError(f"{where}: {label} repete campo: {repeated[0]}")
    omitted = [name for name, javaType in fields.items() if javaType in profile["primitives"] and name not in names]
    if omitted:
        raise ValueError(f"{where}: {label} omite o primitivo {omitted[0]}")


def checkInnerClassSignatures(where: str, constructors: list, fields: dict, hasMap: bool) -> None:
    """Dois construtores com os mesmos tipos de parametro nao compilam, mesmo com nomes de campo diferentes."""
    signatures = [tuple(fields[name] for name in names) for names in constructors]
    if hasMap:
        signatures.append((MAP_CONSTRUCTOR_SIGNATURE,))
    if len(set(signatures)) != len(signatures):
        raise ValueError(f"{where}: dois construtores com a mesma assinatura")


def innerClassDataLine(name: str, classData: str) -> str:
    return f'static SubResource.ClassData data = new SubResource.ClassData({name}.class, "{classData}");'


def renderInnerConstructor(names: list, fields: dict) -> dict:
    signature = ", ".join(f"{fields[name]} {name}" for name in names)
    if not names:
        return {"signature": signature, "body": []}
    body = [f"this.{field} = {field};" if field in names else f"this.{field} = null;" for field in fields]
    return {"signature": signature, "body": body}


def renderInnerMapConstructor(names: list, fields: dict, profile: dict) -> dict:
    body = []
    for field, javaType in fields.items():
        body += readLines(field, javaType, profile) if field in names else [f"this.{field} = null;"]
    return {"suppress": any("<" in fields[name] for name in names), "body": body}


def renderFields(
    schema: dict,
    resource: str,
    overrides: dict,
    profile: dict = JAVA_CODE_PROFILE,
    innerClasses: dict | None = None,
    resourceNames: frozenset = frozenset(),
) -> list:
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
            checkOverrideType(resource, name, javaType, profile, innerClasses or {}, resourceNames)
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


def checkOverrideType(
    resource: str,
    name: str,
    javaType,
    profile: dict,
    innerClasses: dict | None = None,
    resourceNames: frozenset = frozenset(),
) -> None:
    accepted = supportedTypes(profile)
    if isinstance(javaType, str) and javaType in accepted:
        return
    element = subObjectElement(javaType, profile)
    if element is not None:
        checkSubObjectElement(resource, name, element, innerClasses or {}, resourceNames)
        return
    raise ValueError(
        f"{resource}.{name}: tipo de override {javaType!r} que o Map-ctor nao le; aceitos: {', '.join(accepted)}"
    )


def subObjectElement(javaType, profile: dict) -> str | None:
    """`E` de `List<E>` quando E e `Nome` ou `Recurso.Nome`; `List<String>` e `List<Object>` o perfil ja le por cast."""
    if not isinstance(javaType, str) or javaType in supportedTypes(profile):
        return None
    match = fullmatch(r"List<(\w+(?:\.\w+)?)>", javaType)
    return match.group(1) if match else None


def checkSubObjectElement(resource: str, name: str, element: str, innerClasses: dict, resourceNames) -> None:
    """E e uma classe interna do proprio recurso (qualificada ou nao) ou, sem qualificador, outro recurso gerado."""
    qualifier, _, bare = element.rpartition(".")
    if qualifier and qualifier != resource:
        raise ValueError(f"{resource}.{name}: qualificador {qualifier!r} de {element!r} nao e o proprio recurso")
    if qualifier and bare not in innerClasses:
        raise ValueError(f"{resource}.{name}: classe interna {bare!r} nao esta em javaInnerClasses")
    if bare in innerClasses:
        checkInnerClassBuildable(resource, name, bare, innerClasses[bare])
        return
    if bare not in resourceNames:
        raise ValueError(f"{resource}.{name}: elemento {element!r} de List nao e classe interna nem recurso gerado")


def checkInnerClassBuildable(resource: str, name: str, className: str, definition: dict) -> None:
    if "map" in definition or any(definition.get("constructors") or []):
        return
    raise ValueError(f"{resource}.{name}: classe interna {className!r} nao tem construtor que receba campos")


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


def renderMapConstructor(
    entity: dict,
    fields: list,
    profile: dict = JAVA_CODE_PROFILE,
    innerClasses: dict | None = None,
) -> dict:
    """Linhas do corpo de `X(Map)` ja prontas; o Mustache so as emoldura.

    Return-only recebe `null` e nao sai do Map, entao a chave dele cai em "Unknown parameters" (168 campos
    `null` contra 24 lidos, em 27 arquivos). `needsSuppress` so quando algum cast do corpo e generico."""
    javaTypeByName = {field["name"]: field["javaType"] for field in fields}
    lines = []
    castTypes = []
    parsers = []
    for field in entity["fields"]:
        if field["isId"]:
            continue
        if field["returnOnly"]:
            zero = profile["primitives"].get(javaTypeByName[field["name"]], {}).get("zero", "null")
            lines.append(f"this.{field['name']} = {zero};")
            continue
        javaType = javaTypeByName[field["name"]]
        castTypes.append(javaType)
        element = subObjectElement(javaType, profile)
        if element is None:
            lines += readLines(field["name"], javaType, profile)
            continue
        parser = f"parse{field['name'][0].upper()}{field['name'][1:]}"
        lines.append(f'this.{field["name"]} = {parser}((List<Object>) dataCopy.remove("{field["name"]}"));')
        innerDefinition = (innerClasses or {}).get(element.rpartition(".")[2])
        parsers.append(renderParseHelper(parser, element, innerDefinition, profile))
    return {
        "ctorMapBody": lines,
        "ctorMapSuppress": any("<" in javaType for javaType in castTypes),
        "ctorMapParsers": parsers,
    }


def renderParseHelper(parser: str, element: str, innerDefinition: dict | None, profile: dict) -> dict:
    """`parseX(List<Object>)` privado, como o real: null -> null, item ja do tipo E e mantido, item Map vira E (pelo
    construtor Map, ou pelo posicional mais longo quando a classe interna nao o tem), outro tipo falha."""
    body = [
        "if (items == null) {",
        "    return null;",
        "}",
        f"List<{element}> parsed = new ArrayList<>();",
        "for (Object item : items) {",
        f"    if (item instanceof {element}) {{",
        f"        parsed.add(({element}) item);",
        "        continue;",
        "    }",
        "    if (item instanceof Map) {",
        *renderParsedItem(element, innerDefinition, profile),
        "        continue;",
        "    }",
        f'    throw new Exception("Unknown type \\"" + item.getClass() + "\\", use {element} or HashMap");',
        "}",
        "return parsed;",
    ]
    return {"signature": f"private static List<{element}> {parser}(List<Object> items) throws Exception", "body": body}


def renderParsedItem(element: str, innerDefinition: dict | None, profile: dict) -> list:
    if innerDefinition is None or "map" in innerDefinition:
        return [f"        parsed.add(new {element}((Map<String, Object>) item));"]
    fields = innerDefinition["fields"]
    names = max(innerDefinition["constructors"], key=len)
    arguments = [f"            {readItemValue(name, fields[name], profile)}," for name in names]
    arguments[-1] = arguments[-1].rstrip(",")
    return [
        "        Map<String, Object> map = (Map<String, Object>) item;",
        f"        parsed.add(new {element}(",
        *arguments,
        "        ));",
    ]


def readItemValue(name: str, javaType: str, profile: dict) -> str:
    """Valor de `map.get(name)` no tipo do parametro: numerico por `Number` (o Map de um JSON traz Integer, Long ou
    Double), os demais por cast."""
    value = f'map.get("{name}")'
    primitive = profile["primitives"].get(javaType)
    if primitive is not None:
        return f"((Number) {value}).{primitive['getter']}()"
    getter = profile["numericGetters"].get(javaType)
    if getter is None:
        return f"({javaType}) {value}"
    return f"{value} == null ? null : ((Number) {value}).{getter}()"


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
