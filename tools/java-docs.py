import re

# Perfil do alvo em dados: a proxima linguagem troca este dicionario, nao o codigo abaixo.
# Cada regra cita a contagem da ME 4 (46 recursos do sdk-java real) que a sustenta.
JAVA_PROFILE = {
    "parametersTitle": "Parameters:",
    "returnTitle": "Return:",
    "attributesTitle": "Attributes (return-only):",
    "throwsLine": "@throws Exception error in the request",
    "settingsReference": "starkbank.Settings.user",
    # decisao do Antonio (D-doc2): a forma mais frequente (193) cita starkbank.User.defaultUser, que nao existe no SDK
    "user": {
        "name": "user",
        "type": "Organization/Project object, default null",
        "description": "Organization or Project object. Not necessary if starkbank.Settings.user was set before function call",
    },
    # after/before sem ":" apos o colchete: 244 contra 124 (eixo E2)
    "noColonParams": ("after", "before"),
    "mapParamText": {
        "query": "map of parameters for the query",
        "page": "parameters of the query",
        "log.query": "map of parameters for the query",
        "log.page": "parameters of the query",
    },
    "patchText": "map of parameters to patch",
    "ctorMapText": "map of properties for the creation of the {X}",
    "titles": {
        "get": "Retrieve a specific {X}",
        "query": "Retrieve {Xs}",
        "page": "Retrieve paged {Xs}",
        "create": "Create {Xs}",
        "delete": "Delete {a} {X} entity",
        "put": "Create or update {a} {X}",
        "log.get": "Retrieve a specific {X} Log",
        "log.query": "Retrieve {X} Logs",
        "log.page": "Retrieve paged {X}.Logs",
    },
    "dateFormat": "{year:04d}-{month:02d}-{day:02d}",
    "datetimeFormat": "{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}.{micro:06d}+00:00",
    "schemaTypes": {"string": "string", "integer": "integer", "number": "number", "boolean": "bool",
                    "object": "Map of String: Object"},
    "arrayTypes": {"string": "list of strings", "integer": "list of integers", "number": "list of numbers",
                   "object": "list of maps", "boolean": "list of bools"},
}

OPERATION_KINDS = {
    "create": "list", "put": "list",
    "get": "id", "delete": "id", "cancel": "id", "pdf": "id",
    "update": "patch",
    "query": "filters", "page": "filters",
    "log.get": "id", "log.query": "filters", "log.page": "filters",
}
LOG_OPERATIONS = ("log.get", "log.query", "log.page")
PAGE_OPERATIONS = ("page", "log.page")
VARIANTS_PLAIN = (("Plain", False, False), ("User", False, True))
VARIANTS_FILTERS = (("Plain", False, False), ("User", False, True),
                    ("Params", True, False), ("ParamsUser", True, True))

DATETIME_CALL = re.compile(r"datetime\.datetime\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?(?:,\s*(\d+)\s*)?(?:,\s*(\d+)\s*)?"
                           r"(?:,\s*(\d+)\s*)?(?:,\s*(\d+)\s*)?(?:,\s*(\d+)\s*)?\)")
DATE_CALL = re.compile(r"datetime\.date\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
DATE_OR_STRING = re.compile(r"datetime\.date(?:time)? or string")
BARE_DATETIME = re.compile(r"datetime\.date(?:time)?\b")
SETTINGS_REFERENCE = re.compile(r"starkbank\.(?:User\.defaultUser|user)\b")
TYPE_DEFAULT = re.compile(r"^(?P<base>.*?)(?:,\s*default\s+(?P<default>.*))?$")


def renderDocs(className: str, docs: dict, flags: set, entity: dict, profile: dict = JAVA_PROFILE) -> dict:
    """Linhas de Javadoc ja prontas, por chave plana; o Mustache so as emoldura.

    Chaves planas (`getPlain`, `queryParamsUser`, `logPageParams`...) porque o Mustache resolve nome ausente
    no contexto pai: um `get` ou `user` aninhado poderia cair em outro campo do modelo."""
    context = {"className": className, "profile": profile, "warnings": [], "fallbacks": []}
    lines = {
        "dataDoc": entityHeader(context, entity) + fieldSection(context, entity["fields"]),
        "ctorAllDoc": entityHeader(context, entity) + fieldSection(context, entity["fields"], atParam=True),
        "ctorMapDoc": ctorMapLines(context, entity),
    }
    for operation in flaggedOperations(flags):
        doc = operationDoc(docs, operation)
        if doc is None:
            context["fallbacks"].append(operation)
            doc = genericDoc(operation, className)
        lines.update(renderOperation(context, operation, doc))
    return {"lines": lines, "warnings": context["warnings"], "fallbacks": context["fallbacks"]}


def describeEntity(schema: dict, createSchema: dict | None, profile: dict = JAVA_PROFILE) -> dict:
    """Campos do schema ja no vocabulario do doc; return-only = propriedade que o XCreate nao tem, fora o id."""
    creatable = set(((createSchema or {}).get("properties")) or {})
    fields = []
    for name, prop in (schema.get("properties") or {}).items():
        fields.append({
            "name": name,
            "type": fieldType(prop, profile),
            "description": prop.get("description") or "",
            "isId": bool(prop.get("x-sdk-id")),
            "returnOnly": name not in creatable and not prop.get("x-sdk-id"),
        })
    return {"description": schema.get("description") or "", "fields": fields}


def fieldType(prop: dict, profile: dict) -> str:
    kind = prop.get("type") or "object"
    if kind != "array":
        return profile["schemaTypes"].get(kind, kind)
    itemKind = (prop.get("items") or {}).get("type") or "object"
    return profile["arrayTypes"].get(itemKind, f"list of {itemKind}")


def pluralize(name: str) -> str | None:
    """+s e consoante+y -> ies; o resto e irregular e quem chama avisa em vez de adivinhar."""
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return None
    if name.endswith("y") and name[-2:-1].lower() not in "aeiou":
        return f"{name[:-1]}ies"
    return f"{name}s"


def mapText(text: str, className: str, profile: dict = JAVA_PROFILE) -> str:
    text = text.replace('\\"', '"')
    text = DATETIME_CALL.sub(lambda match: quoted(datetimeLiteral(match, profile)), text)
    text = DATE_CALL.sub(lambda match: quoted(dateLiteral(match, profile)), text)
    text = text.replace("Unlimited if None", "Unlimited if null")
    text = SETTINGS_REFERENCE.sub(profile["settingsReference"], text)
    text = classNameText(text, className)
    return neutralizeComment(text)


def mapType(pythonType: str) -> str:
    """`datetime.date or string, default None` -> `string, default null`."""
    match = TYPE_DEFAULT.match(pythonType.strip())
    base = DATE_OR_STRING.sub("string", match.group("base").strip())
    base = BARE_DATETIME.sub("string", base)
    default = match.group("default")
    if default is None:
        return base
    return f"{base}, default {'null' if default.strip() == 'None' else default.strip()}"


def quoted(value: str) -> str:
    return f'"{value}"'


def datetimeLiteral(match, profile: dict) -> str:
    year, month, day, hour, minute, second, micro = (int(part or 0) for part in match.groups())
    return profile["datetimeFormat"].format(year=year, month=month, day=day, hour=hour, minute=minute,
                                            second=second, micro=micro)


def dateLiteral(match, profile: dict) -> str:
    year, month, day = (int(part) for part in match.groups())
    return profile["dateFormat"].format(year=year, month=month, day=day)


def classNameText(text: str, className: str) -> str:
    """splitProfile -> SplitProfile; splitprofile.Log e SplitProfile.Log -> SplitProfile Log (48 a 4 no real).

    So nome com mais de uma palavra: `transfer` sozinho e a palavra do ingles."""
    text = re.sub(rf"\b(?:{className}|{className.lower()})\.Log\b", f"{className} Log", text)
    lowerCamel = f"{className[:1].lower()}{className[1:]}"
    if lowerCamel == lowerCamel.lower():
        return text
    return re.sub(rf"\b{lowerCamel}(s?)\b", rf"{className}\1", text)


def neutralizeComment(text: str) -> str:
    """`*/` fecharia o Javadoc e `\\u` e escape de unicode ate dentro de comentario."""
    return text.replace("*/", "*&#47;").replace("\\u", "&#92;u")


def article(name: str) -> str:
    return "an" if name[:1].lower() in "aeiou" else "a"


def flaggedOperations(flags: set) -> list:
    operations = [flag[len("x-sdk-"):] for flag in sorted(flags) if flag[len("x-sdk-"):] in OPERATION_KINDS]
    return operations + (list(LOG_OPERATIONS) if "x-sdk-log" in flags else [])


def operationDoc(docs: dict, operation: str) -> dict | None:
    if not operation.startswith("log."):
        return (docs or {}).get(operation)
    return (((docs or {}).get("log")) or {}).get(operation[len("log."):])


def operationKey(operation: str, variant: str) -> str:
    head, _, tail = operation.partition(".")
    return f"{head}{tail.capitalize()}{variant}"


def renderOperation(context: dict, operation: str, doc: dict) -> dict:
    kind = OPERATION_KINDS[operation]
    variants = VARIANTS_FILTERS if kind == "filters" else VARIANTS_PLAIN
    description = [mapText(line, context["className"], context["profile"]) for line in doc.get("description") or []]
    title = operationTitle(context, operation, doc)
    returns = returnLines(context, operation, doc)
    rendered = {}
    for variant, withParams, withUser in variants:
        parameters = parameterLines(context, operation, kind, doc, withParams)
        parameters += [userLine(context["profile"])] if withUser else []
        rendered[operationKey(operation, variant)] = blockLines(context["profile"], title, description,
                                                                parameters, returns)
    return rendered


def blockLines(profile: dict, title: str, description: list, parameters: list, returns: list) -> list:
    lines = [title]
    lines += ["<p>", *description] if description else []
    lines += ["<p>", profile["parametersTitle"], *parameters] if parameters else []
    return lines + ["<p>", profile["returnTitle"], *returns, profile["throwsLine"]]


def operationTitle(context: dict, operation: str, doc: dict) -> str:
    className, profile = context["className"], context["profile"]
    template = profile["titles"].get(operation)
    plural = pluralize(className)
    if template is None or ("{Xs}" in template and plural is None):
        if template is not None:
            context["warnings"].append(f"plural irregular nao suportado: {className}; titulo de {operation}"
                                       " vem do resumo do Python")
        summary = doc.get("summary") or genericDoc(operation, className)["summary"]
        return mapText(summary, className, profile)
    return template.replace("{Xs}", plural or "").replace("{X}", className).replace("{a}", article(className))


def parameterLines(context: dict, operation: str, kind: str, doc: dict, withParams: bool) -> list:
    profile = context["profile"]
    pythonParams = [param for param in doc.get("params") or [] if param.get("name") != "user"]
    if kind == "list":
        return [entityListLine(context, operation, pythonParams)]
    if kind == "id":
        return [idLine(context, pythonParams)]
    if kind == "patch":
        fields = [param for param in pythonParams if param.get("name") != "id"]
        return [idLine(context, pythonParams), f"@param patchData {profile['patchText']}",
                *[paramText(context, param) for param in fields]]
    if not withParams:
        return []
    return [f"@param params {profile['mapParamText'][operation]}",
            *[paramText(context, param) for param in pythonParams]]


def entityListLine(context: dict, operation: str, pythonParams: list) -> str:
    """O Java recebe a lista (`{{classVarName}}s`); o Python as vezes recebe os campos soltos (Webhook)."""
    className = context["className"]
    name = f"{className[:1].lower()}{className[1:]}s"
    first = pythonParams[0] if pythonParams else {}
    isList = first.get("type", "").startswith("list of")
    verb = "created/updated" if operation == "put" else "created"
    generic = f"list of {className} objects to be {verb} in the API"
    description = mapText(first["description"], className, context["profile"]) if isList and first.get("description") else generic
    return f"@param {name} [list of {className} objects or HashMaps]: {description}"


def idLine(context: dict, pythonParams: list) -> str:
    generic = {"name": "id", "type": "string", "description": "object unique id"}
    param = next((item for item in pythonParams if item.get("name") == "id"), generic)
    return f"@param {paramText(context, param)}"


def paramText(context: dict, param: dict) -> str:
    profile = context["profile"]
    head = f"{param['name']} [{mapType(param.get('type') or 'string')}]"
    description = mapText(param.get("description") or "", context["className"], profile)
    if not description:
        return head
    separator = " " if param["name"] in profile["noColonParams"] else ": "
    return f"{head}{separator}{description}"


def userLine(profile: dict) -> str:
    user = profile["user"]
    return f"@param {user['name']} [{user['type']}]: {user['description']}"


def returnLines(context: dict, operation: str, doc: dict) -> list:
    className, profile = context["className"], context["profile"]
    returns = [mapText(item, className, profile) for item in doc.get("returns") or []]
    if operation not in PAGE_OPERATIONS:
        return [f"@return {returns[0]}", *returns[1:]] if returns else [f"@return {genericReturns(operation, className)[0]}"]
    returns = returns if len(returns) == 2 else genericReturns(operation, className)
    isLog = operation == "log.page"
    prefix = f"{className}.Log.Page" if isLog else f"{className}.Page"
    field = "logs" if isLog else f"{className[:1].lower()}{className[1:]}s"
    return [f"@return {prefix} object:", f"{prefix}.{field}: {returns[0]}", f"{prefix}.cursor: {returns[1]}"]


def entityHeader(context: dict, entity: dict) -> list:
    className, profile = context["className"], context["profile"]
    description = mapText(entity.get("description") or "", className, profile)
    return [f"{className} object", *(["<p>", description] if description else [])]


def fieldSection(context: dict, fields: list, atParam: bool = False) -> list:
    prefix = "@param " if atParam else ""
    lines = [fieldLine(context, field, prefix) for field in fields]
    return ["<p>", context["profile"]["parametersTitle"], *lines] if lines else []


def fieldLine(context: dict, field: dict, prefix: str = "") -> str:
    description = mapText(field["description"], context["className"], context["profile"])
    head = f"{prefix}{field['name']} [{field['type']}]"
    return f"{head}: {description}" if description else head


def ctorMapLines(context: dict, entity: dict) -> list:
    """`X(Map)`: parametros criaveis, depois os somente de retorno (que o construtor deixa null)."""
    className, profile = context["className"], context["profile"]
    fields = [field for field in entity["fields"] if not field["isId"]]
    creatable = [fieldLine(context, field) for field in fields if not field["returnOnly"]]
    returnOnly = [fieldLine(context, field) for field in fields if field["returnOnly"]]
    dataText = profile["ctorMapText"].replace("{X}", className)
    lines = entityHeader(context, entity) + ["<p>", profile["parametersTitle"], f"@param data {dataText}", *creatable]
    lines += ["<p>", profile["attributesTitle"], *returnOnly] if returnOnly else []
    return lines + [profile["throwsLine"]]


def genericReturns(operation: str, className: str) -> list:
    generic = {
        "create": [f"list of {className} objects with updated attributes"],
        "put": [f"list of {className} objects with updated attributes"],
        "get": [f"{className} object with updated attributes"],
        "query": [f"generator of {className} objects with updated attributes"],
        "page": [f"list of {className} objects with updated attributes",
                 f"cursor to retrieve the next page of {className} objects"],
        "update": [f"updated {className} object"],
        "delete": [f"deleted {className} object"],
        "cancel": [f"canceled {className} object"],
        "pdf": [f"{className} pdf file content"],
        "log.get": [f"{className} Log object with updated attributes"],
        "log.query": [f"generator of {className} Log objects with updated attributes"],
        "log.page": [f"list of {className} Log objects with updated attributes",
                     f"cursor to retrieve the next page of {className} Log objects"],
    }
    return generic[operation]


def genericDoc(operation: str, className: str) -> dict:
    """Bloco generico para a operacao que o schema nao documenta; o mesmo texto que o template trazia."""
    summaries = {
        "update": f"Update {className} entity",
        "cancel": f"Cancel {article(className)} {className} entity",
        "pdf": f"Retrieve a specific {className} pdf file",
    }
    descriptions = {
        "create": [f"Send a list of {className} objects for creation in the Stark Bank API"],
        "put": [f"Send a list of {className} objects for creation/update in the Stark Bank API"],
        "get": [f"Receive a single {className} object previously created in the Stark Bank API by passing its id"],
        "query": [f"Receive a generator of {className} objects previously created in the Stark Bank API.",
                  "Use this function instead of page if you want to stream the objects without worrying about"
                  " cursors and pagination."],
        "page": [f"Receive a list of up to 100 {className} objects previously created in the Stark Bank API and"
                 " the cursor to the next page.",
                 "Use this function instead of query if you want to manually page your requests."],
        "log.get": [f"Receive a single {className} Log object previously created by the Stark Bank API by passing"
                    " its id"],
        "log.query": [f"Receive a generator of {className} Log objects previously created in the Stark Bank API"],
        "log.page": [f"Receive a list of up to 100 {className} Log objects previously created in the Stark Bank API"
                     " and the cursor to the next page."],
    }
    return {
        "summary": summaries.get(operation, ""),
        "description": descriptions.get(operation, []),
        "params": [],
        "returns": [],
    }
