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


# o tipo Java de um campo de javaExtraFields viaja na propriedade sintetica: a spec nao o descreve
EXTRA_FIELD_TYPE_KEY = "x-sdk-java-type"


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
    logShape: dict | None = None,
    pageShape: dict | None = None,
    logEntity: str | None = None,
    eventShape: dict | None = None,
    logResources: frozenset = frozenset(),
    otherInnerClasses: dict | None = None,
) -> dict:
    """`x-sdk-java-code` do recurso: `fields` (declaracao) e o corpo do Map-ctor, os dois sobre o mesmo tipo por
    campo, para o cast nunca divergir da declaracao. `ctorFields`: os mesmos campos na ordem e com o tipo do
    construtor all-args, que no real e dado do Java. `open`: a classe externa nao e `final`. `innerClasses`: as classes
    internas simples do real, ja com os construtores e o corpo do Map-ctor prontos. `resourceNames`: os recursos
    geradores, para o override `List<Recurso>`; esse tipo e o `List<ClasseInterna>` saem do Map-ctor por um auxiliar
    `parseX` (`ctorMapParsers`), como no real. `log`: so para recurso com Log, os nomes e o tipo de `errors` do Log
    interno, ja resolvidos a partir da forma (`javaLogs`) e do nome da entidade do Log (`logEntity`). `page`: so para recurso com Page externa, o nome da lista e o
    tipo de retorno dos `page()`, resolvidos a partir da forma (`javaPages`). `events`: so para recurso com `javaEvents`,
    o Deserializer e as subclasses, derivados das subscriptions e dos parametros do construtor all-args. `logResources`:
    os recursos que tem Log (flag `x-sdk-log` ou `Log` em javaInnerClasses), que o recurso de cada subscription tem de ser.
    `otherInnerClasses`: as classes internas dos demais recursos por nome qualificado (`Transfer.Rule`), so para o tipo
    `List<Transfer.Rule>` de um campo; a classe nao e emitida aqui. Os campos extras (javaExtraFields) ja chegam no schema
    (`addExtraFields`)."""
    knownInnerClasses = {**(innerClasses or {}), **(otherInnerClasses or {})}
    fields = renderFields(schema, resource, overrides, profile, knownInnerClasses, resourceNames)
    ctorFields = renderConstructorFields(fields, resource, ctorOrder, ctorTypes or {}, profile)
    innerClassData = renderInnerClasses(resource, innerClasses or {}, profile, hasLog=logShape is not None)
    code = {
        "fields": fields,
        "ctorFields": ctorFields,
        "open": isOpen,
        "innerClasses": innerClassData,
        "innerClassLines": [line for item in innerClassData for line in innerClassLines(item)],
        **renderMapConstructor(entity, fields, profile, knownInnerClasses),
    }
    if logShape is not None:
        code["log"] = renderLog(resource, logShape, logEntity)
    if pageShape is not None:
        code["page"] = {
            "listName": pageShape["listName"],
            "typeName": "Page" if pageShape["names"] == "short" else f"{resource}.Page",
        }
    if eventShape is not None:
        code["events"] = renderEvents(resource, eventShape, ctorFields, resourceNames, logResources)
    return code


def omitFields(schema: dict, resource: str, omitted: list) -> dict:
    """O schema sem os campos que o Java nao declara (`javaOmittedFields`); o resto, `x-sdk-docs` incluso, fica igual. O
    campo tem de existir e nao ser o id, senao um typo viraria no-op calado."""
    if not omitted:
        return schema
    properties = schema.get("properties") or {}
    for name in omitted:
        if name not in properties:
            raise ValueError(f"{resource}.{name}: javaOmittedFields cita campo que o schema nao tem")
        if properties[name].get("x-sdk-id"):
            raise ValueError(f"{resource}.{name}: javaOmittedFields cita o campo id, que o template sempre emite")
    return {**schema, "properties": {name: prop for name, prop in properties.items() if name not in omitted}}


def addExtraFields(
    schema: dict,
    createSchema: dict | None,
    resource: str,
    extras: dict,
    profile: dict = JAVA_CODE_PROFILE,
) -> tuple:
    """O schema e o XCreate com os campos que so o Java declara (`javaExtraFields`), depois dos da spec. O campo entra
    nos dois, entao e opcional para o Javadoc e o Map-ctor o le, como o real le os quatro. O tipo tem de ser um que o
    Map-ctor sabe ler e o nome nao pode ser de um campo da spec, senao o extra o sobrescreveria calado."""
    if not extras:
        return schema, createSchema
    properties = dict(schema.get("properties") or {})
    creatable = dict((createSchema or {}).get("properties") or {})
    for name, spec in extras.items():
        if name in properties or name in creatable:
            raise ValueError(f"{resource}.{name}: javaExtraFields cita campo que o schema ja tem")
        checkOverrideType(resource, name, spec["type"], profile)
        prop = {**schemaTypeOf(spec["type"], profile), "description": spec.get("description", ""),
                EXTRA_FIELD_TYPE_KEY: spec["type"]}
        properties[name] = prop
        creatable[name] = prop
    return {**schema, "properties": properties}, {**(createSchema or {}), "properties": creatable}


def schemaTypeOf(javaType: str, profile: dict) -> dict:
    """O `type` do schema que da o tipo Java dado, so para o vocabulario do Javadoc (`[string]`, `[integer]`)."""
    for kind, formats in profile["types"].items():
        if javaType in formats.values():
            return {"type": kind}
    for itemKind, arrayType in profile["arrayTypes"].items():
        if javaType == arrayType:
            return {"type": "array", "items": {"type": itemKind}}
    if javaType in profile["primitives"]:
        return {"type": "integer"}
    return {"type": "object"}


def renderEvents(resource: str, shape: dict, ctorFields: list, resourceNames: frozenset, logResources: frozenset) -> dict:
    """Deserializer e subclasses prontos para o Mustache. Da subscription (`boleto-holmes`) saem o recurso do log
    (BoletoHolmes), a subclasse (`<Recurso><Evento>`, BoletoHolmesEvent) e o tipo do `log` (BoletoHolmes.Log); a subclasse
    de `subscription` desconhecida tem `log` JsonObject. Os parametros do construtor das subclasses sao os da base."""
    gson = set(shape["gson"])
    cases = []
    subclasses = []
    for subscription in shape["subscriptions"]:
        logResource = eventLogResource(resource, subscription, resourceNames, logResources)
        className = f"{logResource}{resource}"
        cases.append({"subscription": subscription, "className": className, "isGson": subscription in gson})
        subclasses.append({"className": className, "logType": f"{logResource}.Log"})
    unknownClassName = f"Unknown{resource}"
    subclasses.append({"className": unknownClassName, "logType": "JsonObject"})
    return {
        "cases": cases,
        "subclasses": subclasses,
        "unknownClassName": unknownClassName,
        "baseParameters": ", ".join(
            f"{'String' if field['isId'] else field['javaType']} {field['name']}" for field in ctorFields
        ),
        "superArguments": ", ".join(field["name"] for field in ctorFields),
        "usesGsonEvent": bool(gson),
    }


def eventLogResource(resource: str, subscription: str, resourceNames: frozenset, logResources: frozenset) -> str:
    logResource = "".join(word.capitalize() for word in subscription.split("-"))
    if logResource == resource:
        raise ValueError(f"{resource}: javaEvents.subscriptions {subscription!r} cita o proprio recurso")
    if logResource not in resourceNames:
        raise ValueError(
            f"{resource}: javaEvents.subscriptions {subscription!r} cita o recurso {logResource}, que nao existe em apis/schemas"
        )
    if logResource not in logResources:
        raise ValueError(
            f"{resource}: javaEvents.subscriptions {subscription!r} cita o recurso {logResource}, que nao tem Log "
            "(sem x-sdk-log nem Log em javaInnerClasses)"
        )
    return logResource


def renderLog(resource: str, shape: dict, entity: str) -> dict:
    """Nomes do Log interno prontos para o Mustache. `short` e como o real escreve dentro do proprio Log; o `new Page`
    qualificado nao se escreve (`Page` resolve para a Page do Log, a mesma classe). `classData` e um eixo proprio: o
    real escreve `new ClassData(Log.class, ...)` ou `new ClassData(Recurso.Log.class, ...)` independente dos nomes.
    `extraFields` sao campos String alem dos padroes, declarados, atribuidos e documentados; o ctor all-args e `ctor` ou,
    sem ele, created, type, [errors], entidade, id e os extras, que e o que o template emitia."""
    isShort = shape["names"] == "short"
    typeName = "Log" if isShort else f"{resource}.Log"
    hasErrors = shape["errors"] != "none"
    return {
        "typeName": typeName,
        "pageName": f"{typeName}.Page",
        "newPageName": "Log.Page" if isShort else "Page",
        "classDataClass": "Log" if shape["classData"] == "short" else f"{resource}.Log",
        "errorsType": shape["errors"] if hasErrors else "",
        "hasErrors": hasErrors,
        "usesErrorElement": shape["errors"] == "List<ErrorElement>",
        "extraFields": [{"name": name, "description": text} for name, text in (shape.get("extraFields") or {}).items()],
        "ctorSignature": renderLogCtorSignature(resource, shape, entity),
    }


def renderLogCtorSignature(resource: str, shape: dict, entity: str) -> str:
    extraNames = list(shape.get("extraFields") or {})
    if entity in extraNames:
        raise ValueError(f"{resource}: javaLogs.extraFields.{entity} e o nome da entidade do Log")
    types = {"created": "String", "type": "String", "id": "String", entity: resource, **dict.fromkeys(extraNames, "String")}
    standard = ["created", "type", entity, "id", *extraNames]
    if shape["errors"] != "none":
        types["errors"] = shape["errors"]
        standard.insert(2, "errors")
    order = shape.get("ctor") or standard
    for name in order:
        if name not in types:
            raise ValueError(f"{resource}: javaLogs.ctor cita {name!r}, que nao e parametro do Log")
    for name in standard:
        if name not in order:
            raise ValueError(f"{resource}: javaLogs.ctor omite {name!r}")
    return ", ".join(f"{types[name]} {name}" for name in order)


INNER_CLASS_KEYS = {"fields", "constructors", "map", "final", "classData", "classDataType", "extends", "inner"}
# o real escreve `ClassData` (short) em Invoice.Payment e `SubResource.ClassData` (qualified) em MerchantSession.AllowedInstallment
INNER_CLASS_DATA_TYPES = ("short", "qualified")
# `Resource` carrega o `id`: o ultimo parametro de cada construtor com campos, repassado a `super(id)` (PaymentRequest.Log)
INNER_CLASS_SUPERCLASSES = ("SubResource", "Resource")
# `Page` o template ja emite dentro da classe externa, e `Log` quando o recurso tem `x-sdk-log`; repetir o nome nao compilaria
TEMPLATE_INNER_CLASSES = ("Page",)
MAP_CONSTRUCTOR_SIGNATURE = "Map<String, Object>"


def renderInnerClasses(resource: str, innerClasses: dict, profile: dict = JAVA_CODE_PROFILE, hasLog: bool = False) -> list:
    """Classes internas simples ja prontas para o Mustache: o real as escreve a mao e a spec nao as descreve, entao a
    definicao e dado do Java. Cada construtor atribui os campos recebidos e `null` nos demais; o Map-ctor le so os
    campos de `map`. Campo primitivo nao aceita null, entao tem de ser recebido e lido. `inner` aninha classes com a
    mesma forma, e um campo pode ter como tipo o recurso ou o caminho qualificado de qualquer classe interna dele."""
    scope = {
        "resource": resource,
        "profile": profile,
        "reserved": (*TEMPLATE_INNER_CLASSES, "Log") if hasLog else TEMPLATE_INNER_CLASSES,
        "references": {resource, *innerClassPaths(resource, innerClasses)},
        "seen": set(),
    }
    return [renderInnerClass(scope, resource, name, definition) for name, definition in innerClasses.items()]


def innerClassPaths(outerPath: str, innerClasses: dict):
    for name, definition in innerClasses.items():
        yield f"{outerPath}.{name}"
        nested = definition.get("inner") if isinstance(definition, dict) else None
        if isinstance(nested, dict):
            yield from innerClassPaths(f"{outerPath}.{name}", nested)


def renderInnerClass(scope: dict, outerPath: str, name: str, definition: dict) -> dict:
    where = f"{outerPath}.{name}"
    checkInnerClass(scope, where, name, definition)
    fields = definition["fields"]
    extendsResource = definition.get("extends") == "Resource"
    rendered = {
        "className": name,
        "superclass": definition.get("extends", "SubResource"),
        "isFinal": definition.get("final", True),
        "fields": [{"name": field, "javaType": javaType} for field, javaType in fields.items()],
    }
    if "classData" in definition:
        rendered["classDataLine"] = innerClassDataLine(name, definition["classData"], definition["classDataType"])
    rendered["constructors"] = [
        renderInnerConstructor(names, fields, extendsResource) for names in definition.get("constructors") or []
    ]
    if "map" in definition:
        rendered["map"] = renderInnerMapConstructor(definition["map"], fields, scope["profile"])
    rendered["inner"] = [renderInnerClass(scope, where, nestedName, nestedDefinition)
                         for nestedName, nestedDefinition in (definition.get("inner") or {}).items()]
    return rendered


def innerClassLines(rendered: dict, depth: int = 0) -> list:
    """O texto Java da classe interna, uma linha por item e com a indentacao do nivel, para o Mustache so repeti-lo: o
    Mustache resolve a chave ausente na classe de fora, entao um `classData` ou `map` do pai vazaria para a aninhada."""
    pad = " " * (4 * depth)
    name = rendered["className"]
    final = "final " if rendered["isFinal"] else ""
    lines = ["", f"{pad}    public {final}static class {name} extends {rendered['superclass']} {{"]
    lines += [f"{pad}        public {field['javaType']} {field['name']};" for field in rendered["fields"]]
    if "classDataLine" in rendered:
        lines += ["", f"{pad}        {rendered['classDataLine']}"]
    for constructor in rendered["constructors"]:
        lines += ["", f"{pad}        public {name}({constructor['signature']}) {{"]
        lines += [f"{pad}            {line}" for line in constructor["body"]]
        lines.append(f"{pad}        }}")
    if "map" in rendered:
        lines += innerMapConstructorLines(name, rendered["map"], pad)
    for nested in rendered["inner"]:
        lines += innerClassLines(nested, depth + 1)
    lines.append(f"{pad}    }}")
    return lines


def innerMapConstructorLines(name: str, constructor: dict, pad: str) -> list:
    lines = [""]
    if constructor["suppress"]:
        lines.append(f'{pad}        @SuppressWarnings("unchecked")')
    lines += [
        f"{pad}        public {name}(Map<String, Object> data) throws Exception {{",
        f"{pad}            HashMap<String, Object> dataCopy = new HashMap<>(data);",
        "",
        *[f"{pad}            {line}" for line in constructor["body"]],
        "",
        f"{pad}            if (!dataCopy.isEmpty()) {{",
        f'{pad}                throw new Exception("Unknown parameters used in constructor: [" + String.join(", ", dataCopy.keySet()) + "]");',
        f"{pad}            }}",
        f"{pad}        }}",
    ]
    return lines


def checkInnerClass(scope: dict, where: str, name, definition) -> None:
    checkInnerClassName(scope, where, name)
    if not isinstance(definition, dict):
        raise ValueError(f"{where}: definicao deve ser um mapa")
    unknown = sorted(str(key) for key in set(definition) - INNER_CLASS_KEYS)
    if unknown:
        raise ValueError(f"{where}: chave desconhecida: {unknown[0]}")
    fields = definition.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError(f"{where}: fields ausente ou vazio")
    for field, javaType in fields.items():
        checkInnerClassField(where, field, javaType, scope)
    constructors = definition.get("constructors") or []
    for names in constructors:
        checkInnerFieldNames(where, "construtor", names, fields, scope["profile"])
    if "map" in definition:
        checkInnerFieldNames(where, "map", definition["map"], fields, scope["profile"])
        checkInnerMapTypes(where, definition["map"], fields, scope)
    checkInnerClassSignatures(where, constructors, fields, "map" in definition)
    if not isinstance(definition.get("final", True), bool):
        raise ValueError(f"{where}: final deve ser booleano")
    if "classData" in definition and not isIdentifier(definition["classData"], "[A-Z]"):
        raise ValueError(f"{where}: classData invalido: {definition['classData']!r}")
    checkInnerClassDataType(where, definition)
    checkInnerClassExtends(where, definition)
    if "inner" in definition and not isinstance(definition["inner"], dict):
        raise ValueError(f"{where}: inner deve ser um mapa de classes")


def checkInnerClassExtends(where: str, definition: dict) -> None:
    superclass = definition.get("extends", "SubResource")
    if superclass not in INNER_CLASS_SUPERCLASSES:
        raise ValueError(
            f"{where}: extends invalido: {superclass!r}, esperado um de {list(INNER_CLASS_SUPERCLASSES)}"
        )
    if superclass != "Resource":
        return

    if "id" in definition["fields"]:
        raise ValueError(f"{where}: classe que estende Resource herda o campo id e nao pode declara-lo")
    if "map" in definition:
        raise ValueError(f"{where}: map nao e suportado em classe que estende Resource")
    if not definition.get("constructors"):
        raise ValueError(f"{where}: extends Resource exige ao menos um construtor em constructors")


def checkInnerClassDataType(where: str, definition: dict) -> None:
    if "classData" not in definition:
        if "classDataType" in definition:
            raise ValueError(f"{where}: classDataType sem classData")
        return

    if "classDataType" not in definition:
        raise ValueError(f"{where}: classDataType ausente")

    if definition["classDataType"] not in INNER_CLASS_DATA_TYPES:
        raise ValueError(
            f"{where}: classDataType invalido: {definition['classDataType']!r}, esperado um de {list(INNER_CLASS_DATA_TYPES)}"
        )


def isIdentifier(value, firstCharacter: str) -> bool:
    return isinstance(value, str) and fullmatch(rf"{firstCharacter}[A-Za-z0-9]*", value) is not None


def checkInnerClassName(scope: dict, where: str, name) -> None:
    """O nome e unico na arvore inteira do recurso: uma classe aninhada que repete o nome de uma classe de fora (ou de
    uma irma) sombreia o tipo dentro dela e o `javac` o resolve para a errada."""
    if not isIdentifier(name, "[A-Z]"):
        raise ValueError(f"{where}: nome de classe interna invalido")
    if name in scope["reserved"]:
        raise ValueError(f"{where}: nome de classe interna que o template ja emite")
    if name == scope["resource"]:
        raise ValueError(f"{where}: nome de classe interna igual ao da classe externa")
    if name in scope["seen"]:
        raise ValueError(f"{where}: nome de classe interna repetido")
    scope["seen"].add(name)


def checkInnerClassField(where: str, field, javaType, scope: dict) -> None:
    if not isIdentifier(field, "[a-z]"):
        raise ValueError(f"{where}.{field}: nome de campo invalido")
    if isinstance(javaType, str) and javaType in scope["references"]:
        return

    checkOverrideType(where, field, javaType, scope["profile"])


def checkInnerMapTypes(where: str, names: list, fields: dict, scope: dict) -> None:
    """O Map-ctor le por cast do perfil; um tipo de referencia (o recurso ou uma classe interna) nao vem de um Map."""
    unread = [name for name in names if fields[name] in scope["references"]]
    if unread:
        raise ValueError(f"{where}: map cita campo de tipo {fields[unread[0]]!r}, que o Map-ctor nao le: {unread[0]}")


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


def innerClassDataLine(name: str, classData: str, classDataType: str) -> str:
    # a classe interna estende SubResource e herda o tipo membro ClassData: as duas formas compilam
    qualifier = "SubResource." if classDataType == "qualified" else ""
    return f'static {qualifier}ClassData data = new {qualifier}ClassData({name}.class, "{classData}");'


def renderInnerConstructor(names: list, fields: dict, extendsResource: bool = False) -> dict:
    if not names:
        return {"signature": "", "body": ["super(null);"] if extendsResource else []}

    parameters = [f"{fields[name]} {name}" for name in names]
    body = [f"this.{field} = {field};" if field in names else f"this.{field} = null;" for field in fields]
    if not extendsResource:
        return {"signature": ", ".join(parameters), "body": body}

    return {"signature": ", ".join([*parameters, "String id"]), "body": ["super(id);", *body]}


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
        if name in overrides and EXTRA_FIELD_TYPE_KEY in prop:
            raise ValueError(f"{resource}.{name}: javaTypes cita campo declarado em javaExtraFields")
        extraType = prop.get(EXTRA_FIELD_TYPE_KEY)
        javaType = overrides[name] if name in overrides else extraType or fieldTypeOf(resource, name, prop, profile)
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
    """E e uma classe interna do proprio recurso (qualificada ou nao), uma de outro recurso (`innerClasses` a traz pelo
    nome qualificado, `Transfer.Rule`) ou, sem qualificador, outro recurso gerado."""
    qualifier, _, bare = element.rpartition(".")
    if qualifier and element in innerClasses:
        checkInnerClassBuildable(resource, name, bare, innerClasses[element])
        return
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
    if definition.get("extends") == "Resource":
        raise ValueError(f"{resource}.{name}: classe interna {className!r} estende Resource, que o parseX nao constroi")
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
        innerDefinition = (innerClasses or {}).get(element) or (innerClasses or {}).get(element.rpartition(".")[2])
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
