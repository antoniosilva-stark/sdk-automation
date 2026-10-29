import re
import sys
import pytest

from conftest import REPO_ROOT, requiresGenerator

REAL_SOURCES = REPO_ROOT / "_references/sdk-java/src/main/java/com/starkbank"
GOLDEN_RESOURCES = ("SplitProfile", "Transaction", "Transfer", "Balance", "Webhook", "VerifiedTransfer")

SECTION = re.compile(r"^(Parameters(?: \((?:optional|required)\))?|Return|Attributes(?: \(return-only\))?):$")
FILTER_LINE = re.compile(r"^(\w+) \[")
PARAM_TAG = re.compile(r"^@param (\w+)")
NUMERIC_TYPES = {"Integer", "Long", "Float", "Double", "Short", "Byte", "int", "long", "float", "double", "short", "byte"}
MODIFIERS = {"public", "static", "final", "private", "protected"}
ANNOTATIONS = re.compile(r"^((?:@\w+(?:\([^)]*\))?\s*)+)")
CLASS_HEADER = re.compile(r"\b(?:class|interface|enum)\s+(\w+)")

# o `user` do real tem duas formas fora da decidida (D-doc2): o golden as leva para a decidida, so no real,
# para a linha gerada continuar comparada exatamente
REAL_USER_FORMS = (("[Organization/Project object]:", "[Organization/Project object, default null]:"),
                   ("starkbank.User.defaultUser", "starkbank.Settings.user"))


def _doc(lines: list[str]) -> list[str]:
    return [re.sub(r"^\s*\*\s?", "", line).rstrip() for line in lines]


def _javadocAbove(rawLines: list[str], line: int) -> list[str] | None:
    """Javadoc que fecha logo acima da linha `line` (0-based), pulando linhas em branco."""
    index = line - 1
    while index >= 0 and not rawLines[index].strip():
        index -= 1
    if index < 0 or not rawLines[index].strip().endswith("*/"):
        return None
    end = index
    while index >= 0 and not rawLines[index].strip().startswith("/**"):
        index -= 1
    return _doc(rawLines[index + 1:end])


def _membersOf(source: str, className: str, tool, assertGenerated) -> list[dict]:
    """Membros declarados em corpo de classe, em ordem: o texto entre `;`, `{` e `}` quando o bloco que contem e
    uma classe. Corpo de metodo e inicializador ficam de fora."""
    cleaned = "\n".join(tool.withoutNoise(source))
    rawLines = source.split("\n")
    stack, names, bodies, members = [], [], [], []
    header, start, line = "", None, 0
    for index, char in enumerate(cleaned):
        if char == "\n":
            line += 1
        if char not in "{;}":
            header += char
            start = line if start is None and not char.isspace() else start
            continue
        text = " ".join(header.split())
        isMember = bool(text) and (stack[-1] == "class" if stack else not text.startswith(("package ", "import ")))
        if isMember:
            members.append(_member(text, start, ".".join(names[1:]), className, rawLines, assertGenerated))
        if char == "{":
            declared = CLASS_HEADER.search(text) if "(" not in text else None
            stack.append("class" if declared else "body")
            names.append(declared.group(1) if declared else "")
            bodies.append((members[-1], index) if isMember and not declared else None)
        if char == "}":
            stack.pop()
            names.pop()
            opened = bodies.pop()
            if opened:
                opened[0]["body"] = cleaned[opened[1] + 1:index]
        header, start = "", None
    return members


def _member(text: str, start: int, owner: str, className: str, rawLines: list[str], assertGenerated) -> dict:
    annotations = ANNOTATIONS.match(text)
    declaration = text[annotations.end():] if annotations else text
    annotationList = re.findall(r"@(\w+)", annotations.group(1)) if annotations else []
    base = {"doc": _javadocAbove(rawLines, start), "annotations": annotationList, "owner": owner}
    classHeader = CLASS_HEADER.search(declaration)
    if classHeader and "(" not in declaration:
        return {**base, "kind": "class", "label": f"class {owner + '.' if owner else ''}{classHeader.group(1)}"}
    opening = declaration.find("(")
    if opening < 0 or "=" in declaration[:opening]:
        words = [word for word in declaration.split("=")[0].split() if word not in MODIFIERS]
        return {**base, "kind": "field", "label": f"{owner}.{words[-1]}".lstrip("."), "type": " ".join(words[:-1])}
    words = declaration[:opening].split()
    parameters = [" ".join(part.split()) for part in assertGenerated.splitTopLevel(declaration[opening + 1:declaration.rfind(")")])]
    types = [parameter.rsplit(" ", 1)[0] for parameter in parameters]
    ownerClass = owner.rsplit(".", 1)[-1] or className
    isAllArgs = words[-1] == ownerClass and set(words[:-1]) <= MODIFIERS and bool(parameters) and types != ["Map<String, Object>"]
    label = f"{owner}.{words[-1]}({', '.join(types)})".lstrip(".")
    return {**base, "kind": "method", "label": f"{owner}.{ownerClass}(all-args)".lstrip(".") if isAllArgs else label,
            "isAllArgs": isAllArgs, "name": words[-1], "returns": " ".join(words[:-1]), "parameterTypes": types,
            "throws": declaration[declaration.rfind(")") + 1:].strip(), "body": "",
            "parameterNames": [parameter.rsplit(" ", 1)[-1] for parameter in parameters]}


def _assignmentClass(expression: str) -> str:
    expression = " ".join(expression.split())
    if expression == "null":
        return "null"
    converted = re.match(r"^(\w+) == null \? null : \1\.(\w+)\(\)$", expression)
    if converted:
        return f"number:{converted.group(2)}"
    chained = re.match(r"^\(\(Number\) [\w.]+\.(?:remove|get)\(.*\)\)\.(\w+)\(\)$", expression)
    if chained:
        return f"number:{chained.group(1)}"
    parsed = re.match(r"^\w+\.(parse\w+)\(", expression)
    if parsed:
        return f"parse:{parsed.group(1)}"
    cast = re.match(r"^\(([\w.<>?\[\], ]+)\) [\w.]+\.(?:remove|get)\(", expression)
    if cast:
        kind = re.sub(r"<.*>", "", cast.group(1)).replace(" ", "")
        return f"directcast:{kind}" if kind in NUMERIC_TYPES else f"cast:{kind}"
    return f"other:{expression}"


def _mapConstructorAssignments(source: str, tool, className: str) -> dict[str, str]:
    """Classe da atribuicao de cada campo no construtor X(Map): null, number:<conversao>, cast:<tipo>, parse:<fn>."""
    cleaned = "\n".join(tool.withoutNoise(source, keepStrings=True))
    opening = re.search(r"public " + className + r"\(Map<String, Object> data\)[^{]*\{", cleaned)
    if not opening:
        return {}
    body = cleaned[opening.end():]
    body = body[:_closingBrace(body)]
    assignments = {}
    for statement in (part.strip() for part in body.split(";")):
        match = re.match(r"^this\.(\w+) = (.*)$", statement, re.S)
        if match:
            assignments[match.group(1)] = _assignmentClass(match.group(2))
    return assignments


def _closingBrace(text: str) -> int:
    depth = 1
    for index, char in enumerate(text):
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth == 0:
            return index
    return len(text)


def _importGroups(source: str) -> list[list[str]]:
    groups, current = [], []
    for line in source.split("\n"):
        if line.startswith("import "):
            current.append(line)
            continue
        if current and not line.strip():
            groups.append(current)
            current = []
    return groups + ([current] if current else [])


def _docSignature(doc: list[str] | None, real: bool) -> dict | None:
    if doc is None:
        return None
    sections, tags, filters, user = [], [], [], None
    for line in doc:
        if SECTION.match(line):
            sections.append(line)
            continue
        if line.startswith("@param user "):
            user = " ".join(line.split())
            for old, new in REAL_USER_FORMS if real else ():
                user = user.replace(old, new)
        tag = PARAM_TAG.match(line)
        if tag:
            tags.append(f"param:{tag.group(1)}")
            continue
        if line.startswith(("@return", "@throws")):
            tags.append(line.split()[0][1:])
            continue
        if not line.startswith("@") and FILTER_LINE.match(line):
            filters.append(FILTER_LINE.match(line).group(1))
    return {"sections": sections, "tags": tags, "filters": filters, "user": user}


def _structure(source: str, className: str, real: bool, tool, assertGenerated) -> dict:
    members = _membersOf(source, className, tool, assertGenerated)
    methods = [member for member in members if member["kind"] in ("method", "class")]
    return {
        "methods": methods,
        "fields": [member for member in members if member["kind"] == "field"],
        "assignments": _mapConstructorAssignments(source, tool, className),
        "imports": _importGroups(source),
        "docs": {member["label"]: _docSignature(member["doc"], real) for member in methods},
    }


def _short(imports: list[list[str]]) -> str:
    return " | ".join(",".join(line.removeprefix("import ").rstrip(";").rsplit(".", 1)[-1] for line in group)
                      for group in imports)


def _listDiff(real: list[str], generated: list[str]) -> str:
    return f"-[{', '.join(item for item in real if item not in generated)}] +[{', '.join(item for item in generated if item not in real)}]"


def _moved(real: list[str], generated: list[str]) -> list[str]:
    """Itens comuns que mudaram de lugar: os que ficam fora da maior subsequencia comum das duas ordens."""
    common = [item for item in real if item in generated]
    other = [item for item in generated if item in common]
    table = [[0] * (len(other) + 1) for _ in range(len(common) + 1)]
    for row in range(len(common) - 1, -1, -1):
        for column in range(len(other) - 1, -1, -1):
            table[row][column] = (table[row + 1][column + 1] + 1 if common[row] == other[column]
                                  else max(table[row + 1][column], table[row][column + 1]))
    kept, row, column = set(), 0, 0
    while row < len(common) and column < len(other):
        if common[row] == other[column]:
            kept.add(common[row])
            row, column = row + 1, column + 1
        elif table[row + 1][column] >= table[row][column + 1]:
            row += 1
        else:
            column += 1
    return [item for item in other if item not in kept]


def _setDivergences(prefix: str, real: list[str], generated: list[str]) -> dict[str, str]:
    found = {}
    for item in real:
        if item not in generated:
            found[f"{prefix}|missing|{item}"] = "no real, ausente no gerado"
    for item in generated:
        if item not in real:
            found[f"{prefix}|extra|{item}"] = "no gerado, ausente no real"
    moved = _moved(real, generated)
    if moved:
        found[f"{prefix}|order|moved=[{', '.join(moved)}]"] = "ordem do real: " + " > ".join(item for item in real if item in generated)
    return found


def _methodDivergences(real: dict, generated: dict) -> dict[str, str]:
    return _setDivergences("methods", [member["label"] for member in real["methods"]],
                           [member["label"] for member in generated["methods"]])


def _seqDiff(real: list[str], generated: list[str]) -> str:
    missing = [item for item in real if item not in generated]
    extra = [item for item in generated if item not in real]
    moved = _moved(real, generated)
    return " ".join(([f"-[{', '.join(missing)}] +[{', '.join(extra)}]"] if missing or extra else [])
                    + ([f"moved=[{', '.join(moved)}]"] if moved else []))


def _blockDivergences(real: dict, generated: dict) -> dict[str, str]:
    """Divergencias de assinatura, anotacao e Javadoc. A mesma divergencia em varios overloads vira uma chave so,
    com a lista dos overloads: o registro fica legivel e continua exato."""
    found, grouped = {}, {}
    byLabel = {member["label"]: member for member in generated["methods"]}
    for member in real["methods"]:
        other = byLabel.get(member["label"])
        if other is None:
            continue
        label = member["label"]
        if member["kind"] == "method" and member["returns"] != other["returns"]:
            grouped.setdefault(f"signature|returns {member['returns']} -> {other['returns']}", []).append(label)
        if member["kind"] == "method" and member["throws"] != other["throws"]:
            grouped.setdefault(f"signature|{member['throws'] or '(sem throws)'} -> {other['throws'] or '(sem throws)'}", []).append(label)
        if member["annotations"] != other["annotations"]:
            grouped.setdefault(f"annotations|{_seqDiff(member['annotations'], other['annotations'])}", []).append(label)
        if member["kind"] == "method" and member["isAllArgs"]:
            found.update(_allArgsDivergences(label, member, other))
        for detail in _docDivergences(real["docs"][label], generated["docs"][label]):
            grouped.setdefault(f"doc|{detail}", []).append(label)
    found.update({f"{detail}|{', '.join(labels)}": "bloco" for detail, labels in grouped.items()})
    return found


def _restByOperation(structure: dict) -> dict[str, dict[str, int]]:
    """Primitivos de Rest que os corpos de cada operacao chamam (post, postSingle, getId, getStream, getPage, put...),
    contados por operacao e nao por overload: `create(Map)` do real e `create(List)` do gerador sao a mesma operacao."""
    calls = {}
    for member in structure["methods"]:
        if member["kind"] != "method" or member["isAllArgs"]:
            continue
        operation = f"{member['owner']}.{member['name']}".lstrip(".")
        for primitive in re.findall(r"\bRest\.(\w+)\(", member["body"]):
            counts = calls.setdefault(operation, {})
            counts[primitive] = counts.get(primitive, 0) + 1
    return calls


def _restDivergences(real: dict, generated: dict) -> dict[str, str]:
    before, after = _restByOperation(real), _restByOperation(generated)
    return {f"rest|{operation} real={before.get(operation, {})} gen={after.get(operation, {})}": "primitivo de Rest"
            for operation in dict.fromkeys([*before, *after]) if before.get(operation) != after.get(operation)}


def _allArgsDivergences(label: str, real: dict, generated: dict) -> dict[str, str]:
    found = _setDivergences(f"allArgs|{label}", real["parameterNames"], generated["parameterNames"])
    realTypes = dict(zip(real["parameterNames"], real["parameterTypes"]))
    generatedTypes = dict(zip(generated["parameterNames"], generated["parameterTypes"]))
    for name, kind in realTypes.items():
        if name in generatedTypes and generatedTypes[name] != kind:
            found[f"allArgs|{label}|type|{name} real={kind} gen={generatedTypes[name]}"] = "tipo do parametro"
    return found


def _docDivergences(real: dict | None, generated: dict | None) -> list[str]:
    if real is None and generated is None:
        return []
    if real is None or generated is None:
        return [f"presence real={real is not None} gen={generated is not None}"]
    found = []
    for name in ("sections", "tags", "filters"):
        if real[name] != generated[name]:
            found.append(f"{name} {_seqDiff(real[name], generated[name])}")
    if real["user"] != generated["user"]:
        found.append(f"user real={real['user']} gen={generated['user']}")
    return found


def _fieldDivergences(real: dict, generated: dict) -> dict[str, str]:
    realNames = [field["label"] for field in real["fields"]]
    generatedNames = [field["label"] for field in generated["fields"]]
    found = _setDivergences("fields", realNames, generatedNames)
    generatedTypes = {field["label"]: field["type"] for field in generated["fields"]}
    for field in real["fields"]:
        if field["label"] in generatedTypes and generatedTypes[field["label"]] != field["type"]:
            found[f"fields|type|{field['label']} real={field['type']} gen={generatedTypes[field['label']]}"] = "tipo do campo"
    return found


def _assignmentDivergences(real: dict, generated: dict) -> dict[str, str]:
    found = {}
    for name in dict.fromkeys([*real["assignments"], *generated["assignments"]]):
        before, after = real["assignments"].get(name), generated["assignments"].get(name)
        if before != after:
            found[f"mapctor|{name} real={before} gen={after}"] = "classe da atribuicao no ctor Map"
    return found


def _importDivergences(real: dict, generated: dict) -> dict[str, str]:
    if real["imports"] == generated["imports"]:
        return {}
    return {f"imports|real={_short(real['imports'])} gen={_short(generated['imports'])}": "ordem, agrupamento ou conjunto"}


def _divergences(real: dict, generated: dict) -> dict[str, str]:
    found = {}
    for check in (_methodDivergences, _restDivergences, _blockDivergences, _fieldDivergences, _assignmentDivergences, _importDivergences):
        found.update(check(real, generated))
    return found


def _compared(real: dict, generated: dict) -> tuple[int, int]:
    labels = {member["label"] for member in generated["methods"]}
    common = [member for member in real["methods"] if member["label"] in labels]
    blocks = [member for member in common if real["docs"][member["label"]] or generated["docs"][member["label"]]]
    return (len([member for member in common if member["kind"] == "method"]), len(blocks))


# Dois registros fechados, recurso -> chave -> motivo. A chave e a divergencia exata; uma entrada que parou de casar
# reprova o teste (como as dispensas versionadas de tests/waivers), nos dois registros.
#   EXPECTED: divergencias legitimas (docstring Python sem o conteudo, real na minoria do proprio real, primitivos do
#     real, excecoes da 6b). Nao ha o que corrigir no gerador.
#   KNOWN_GENERATOR_LIMITS: defeitos ou limites do gerador, com severidade e a pendencia "Entrega 9" que os fecha. O
#     golden nao fica verde escondendo isso: o relatorio imprime a contagem por severidade.
SEVERITIES = ("runtime-errado", "tipo-perdido", "cosmetico")

PRIMITIVE = "tipo primitivo do real (long/int, 15 de 55 arquivos); o gerador emite o boxed, porque o Python nao tem primitivo"
PRIMITIVE_CAST = "o real le `amount` com cast direto (int), que trunca; o gerador le Number.longValue (RF9)"
LIST_PARAM = "excecao da 6b: o nome do parametro de lista vem do plural do recurso; o real usa nome proprio"
DEPRECATED = ("excecao da 6b: @Deprecated do real nao vem do docstring Python (Transaction.create: v2.31.0 no Python,"
              " v2.24.0 no Java); o corpo do real so lanca, sem chamar Rest")
LOG_ERRORS = "excecao da 6b: Log.errors sai List<ErrorElement> (7 de 24 Log); o real mais frequente usa String[] (14 de 24)"
DOCSTRING_EXTRA = "o docstring Python tem conteudo que o Javadoc do real nao tem"
DOCSTRING_MISSING = "conteudo ausente do docstring Python que o real tem"
DOCSTRING_ORDER = "a ordem dos campos do real difere do docstring Python, que e a fonte do schema"
TRANSACTION_FILTERS = "docstring Python diverge do real: o real escreve `receivedId` (erro de digitacao) e lista `id` entre os parametros"
ID_RETURN_ONLY = "plano, premissa 6: campo somente de retorno = X menos XCreate, fora o id; o real lista o id em Attributes (return-only)"
ID_POSITION = "posicao do id no ctor all-args: real primeiro 20, no meio 18, no fim 4 de 42; o gerador segue a ordem do docstring"
OPTIONAL_HEADER = ("ctor Map do real sem forma dominante: required+optional 13 de 32, `Parameters:`+`Attributes` (a do gerador) 5,"
                   " `Parameters:`+`optional` 5, e so `optional` no SplitProfile")
REQUIRED_OPTIONAL = ("real: required+optional e a forma mais frequente do ctor Map (13 de 32);"
                     " o gerador usa `Parameters:` unico (regra do campo data, 39 de 45)")
NO_THROWS = "real na minoria: ctor Map sem `@throws` no Javadoc (7 de 32 documentados) ou sem `throws Exception` (3 de 35)"
PAGE_ORDER = "real na minoria do proprio real: ordem dos overloads de page; a dominante e (Map)(User)()(Map,User), 24 de 33"
QUERY_ORDER = "real na minoria do proprio real: query em (Map)(User)()(Map,User), 12 de 33; a dominante e (Map,User)(Map)(User)(), 19 de 33"
TRANSFER_ORDER = ("real na minoria do proprio real: query (Map)(User)()(Map,User), 12 de 33, e delete antes de query"
                  " (2 de 9 arquivos com os dois)")
REST_DIRECT = ("real sem forma dominante: query(Map) chama Rest.getStream direto (20 de 37) ou delega para query(Map, User)"
               " (17 de 37, a forma do gerador)")
LOG_ATTRIBUTES = "real [SP] na minoria: `Attributes:` no Log (6 cabecalhos contra 46 `Attributes (return-only):`)"
LOG_FIELD_ORDER = "real [SP] na minoria: campos do Log em outra ordem; a dominante e `created type errors X`, 16 de 24"
IMPORT_ORDER = ("real na minoria do proprio real: Rest>Resource>Generator (11 de 42; dominante Generator>Resource>Rest, 20)"
                " e java.util fora de ordem alfabetica (13 contra 21)")
BALANCE_IMPORTS = "o real importa o que nao usa (Generator, List, HashMap, ArrayList); o gate DEAD_IMPORT reprova import morto"

FREE_OBJECT = ("tipo-perdido",
               "Entrega 9: objeto livre `metadata`: o real usa HashMap<String, Object>; o gerador emite Object")
RULE = ("tipo-perdido",
        "Entrega 9: o gerador nao emite o sub-objeto Transfer.Rule (classe interna, Deserializer, parseRules);"
        " `rules` sai List<Object> e o ctor Map faz cast nao verificado")
LOG_QUALIFIED = ("cosmetico",
                 "Entrega 9: o gerador qualifica `Transfer.Log` onde o real usa `Log` (13 de 23 `List<Log>` contra 10"
                 " `List<X.Log>`)")
BALANCE_GET = ("runtime-errado",
               "Entrega 9: Balance e singleton: o real tem `get()` e `get(User)` com Rest.getStream; o gerador emite"
               " `get(String id)` com Rest.getId. A guarda de build-resource bloqueia a criacao")
BALANCE_MAP_CTOR = ("cosmetico",
                    "Entrega 9: Balance nao tem ctor Map no real; o gerador emite o ctor (superficie publica a mais)")
SINGLE_CREATE = ("runtime-errado",
                 "Entrega 9: create de objeto unico: o real tem `create(Map)` com Rest.postSingle; o gerador emite"
                 " `create(List)` com Rest.post. A guarda de build-resource bloqueia a criacao")

EXPECTED: dict[str, dict[str, str]] = {
    "SplitProfile": {
        "methods|order|moved=[page(Map<String, Object>, User)]":
            PAGE_ORDER,
        "rest|query real={'getStream': 4} gen={'getStream': 1}":
            REST_DIRECT,
        "doc|sections -[Parameters (optional):] +[Parameters:]|SplitProfile(Map<String, Object>)":
            OPTIONAL_HEADER,
        "doc|tags -[] +[throws]|SplitProfile(Map<String, Object>)":
            NO_THROWS,
        "doc|tags -[param:profiles] +[param:splitProfiles]|put(List<?>), put(List<?>, User)":
            LIST_PARAM,
        "doc|filters -[tags, ids] +[]|query(Map<String, Object>, User), query(Map<String, Object>)":
            DOCSTRING_MISSING,
        "doc|filters -[] +[status]|page(Map<String, Object>, User), page(Map<String, Object>)":
            DOCSTRING_EXTRA,
        "doc|sections -[Attributes:] +[Attributes (return-only):]|Log.Log(all-args)":
            LOG_ATTRIBUTES,
        "fields|order|moved=[Log.type, Log.errors, Log.profile]":
            LOG_ERRORS,
        "imports|real=Rest,Resource,Generator,SubResource,ErrorElement | Map,List,HashMap,ArrayList gen=Generator,Resource,Rest,SubResource,ErrorElement | ArrayList,HashMap,List,Map":
            IMPORT_ORDER,
    },
    "Transaction": {
        "methods|order|moved=[query(Map<String, Object>, User)]":
            QUERY_ORDER,
        "rest|create real={} gen={'post': 1}":
            DEPRECATED,
        "allArgs|Transaction(all-args)|order|moved=[senderId, fee, balance, created]":
            DOCSTRING_ORDER,
        "allArgs|Transaction(all-args)|type|amount real=long gen=Long":
            PRIMITIVE,
        "allArgs|Transaction(all-args)|type|fee real=int gen=Integer":
            PRIMITIVE,
        "doc|sections -[Parameters (required):, Parameters (optional):] +[]|Transaction(Map<String, Object>)":
            REQUIRED_OPTIONAL,
        "doc|filters -[receivedId, id] +[receiverId]|Transaction(Map<String, Object>)":
            TRANSACTION_FILTERS,
        "doc|filters -[] +[tags]|query(Map<String, Object>, User), page(Map<String, Object>, User)":
            DOCSTRING_EXTRA,
        "annotations|-[Deprecated] +[]|create(List<?>), create(List<?>, User)":
            DEPRECATED,
        "fields|order|moved=[senderId, fee]":
            DOCSTRING_ORDER,
        "fields|type|amount real=long gen=Long":
            PRIMITIVE,
        "mapctor|amount real=directcast:int gen=number:longValue":
            PRIMITIVE_CAST,
    },
    "Transfer": {
        "methods|order|moved=[query(Map<String, Object>, User), delete(String), delete(String, User)]":
            TRANSFER_ORDER,
        "allArgs|Transfer(all-args)|extra|displayDescription":
            DOCSTRING_EXTRA,
        "allArgs|Transfer(all-args)|order|moved=[id]":
            ID_POSITION,
        "allArgs|Transfer(all-args)|type|amount real=long gen=Long":
            PRIMITIVE,
        "allArgs|Log.Log(all-args)|type|errors real=String[] gen=List<ErrorElement>":
            LOG_ERRORS,
        "doc|tags -[] +[param:displayDescription]|Transfer(all-args)":
            DOCSTRING_EXTRA,
        "doc|sections -[Parameters (optional):] +[]|Transfer(Map<String, Object>)":
            OPTIONAL_HEADER,
        "doc|filters -[id] +[displayDescription]|Transfer(Map<String, Object>)":
            DOCSTRING_EXTRA,
        "doc|filters -[] +[taxId]|query(Map<String, Object>, User)":
            DOCSTRING_EXTRA,
        "doc|filters -[transferIds] +[transactionIds, taxId]|page(Map<String, Object>), page(Map<String, Object>, User)":
            DOCSTRING_EXTRA,
        "fields|extra|displayDescription":
            DOCSTRING_EXTRA,
        "fields|type|amount real=long gen=Long":
            PRIMITIVE,
        "fields|type|Log.errors real=String[] gen=List<ErrorElement>":
            LOG_ERRORS,
        "mapctor|displayDescription real=None gen=cast:String":
            DOCSTRING_EXTRA,
    },
    "Balance": {
        "allArgs|Balance(all-args)|order|moved=[id]":
            ID_POSITION,
        "allArgs|Balance(all-args)|type|amount real=long gen=Long":
            PRIMITIVE,
        "fields|type|amount real=long gen=Long":
            PRIMITIVE,
        "imports|real=Rest,Resource,Generator | List,HashMap,ArrayList gen=Resource,Rest | HashMap,Map":
            BALANCE_IMPORTS,
    },
    "Webhook": {
        "methods|order|moved=[query(Map<String, Object>, User)]":
            QUERY_ORDER,
        "signature|(sem throws) -> throws Exception|Webhook(Map<String, Object>)":
            NO_THROWS,
        "doc|sections -[Attributes (return-only):] +[]|Webhook(Map<String, Object>)":
            ID_RETURN_ONLY,
        "doc|tags -[] +[throws]|Webhook(Map<String, Object>)":
            NO_THROWS,
        "doc|filters -[id] +[]|Webhook(Map<String, Object>)":
            ID_RETURN_ONLY,
    },
    "VerifiedTransfer": {
        "allArgs|VerifiedTransfer(all-args)|missing|accountType":
            DOCSTRING_MISSING,
        "allArgs|VerifiedTransfer(all-args)|order|moved=[id]":
            ID_POSITION,
        "allArgs|VerifiedTransfer(all-args)|type|amount real=long gen=Long":
            PRIMITIVE,
        "doc|tags -[param:accountType] +[]|VerifiedTransfer(all-args)":
            DOCSTRING_MISSING,
        "doc|sections -[Parameters (optional):] +[]|VerifiedTransfer(Map<String, Object>)":
            OPTIONAL_HEADER,
        "doc|filters -[accountType, id] +[]|VerifiedTransfer(Map<String, Object>)":
            DOCSTRING_MISSING,
        "doc|tags -[param:transfers] +[param:verifiedTransfers]|create(List<?>), create(List<?>, User)":
            LIST_PARAM,
        "fields|missing|accountType":
            DOCSTRING_MISSING,
        "fields|type|amount real=long gen=Long":
            PRIMITIVE,
        "mapctor|accountType real=cast:String gen=None":
            DOCSTRING_MISSING,
        "imports|real=Rest,Resource | Map,List,HashMap,ArrayList gen=Resource,Rest | ArrayList,HashMap,List,Map":
            IMPORT_ORDER,
    },
}

KNOWN_GENERATOR_LIMITS: dict[str, dict[str, tuple[str, str]]] = {
    "SplitProfile": {

    },
    "Transaction": {

    },
    "Transfer": {
        "methods|missing|parseRules(List<Object>)":
            RULE,
        "methods|missing|class Rule":
            RULE,
        "methods|missing|Rule.Rule(all-args)":
            RULE,
        "methods|missing|Rule.Rule(Map<String, Object>)":
            RULE,
        "methods|missing|Rule.Rule()":
            RULE,
        "methods|missing|class Rule.Deserializer":
            RULE,
        "methods|missing|Rule.Deserializer.deserialize(JsonElement, Type, JsonDeserializationContext)":
            RULE,
        "allArgs|Transfer(all-args)|type|rules real=List<Rule> gen=List<Object>":
            RULE,
        "allArgs|Transfer(all-args)|type|metadata real=HashMap<String, Object> gen=Object":
            FREE_OBJECT,
        "allArgs|Log.Page.Page(all-args)|type|logs real=List<Log> gen=List<Transfer.Log>":
            LOG_QUALIFIED,
        "annotations|-[] +[SuppressWarnings]|Transfer(Map<String, Object>)":
            RULE,
        "signature|returns public static Log -> public static Transfer.Log|Log.get(String), Log.get(String, User)":
            LOG_QUALIFIED,
        "signature|returns public static Generator<Log> -> public static Generator<Transfer.Log>|Log.query(Map<String, Object>), Log.query(User), Log.query(), Log.query(Map<String, Object>, User)":
            LOG_QUALIFIED,
        "signature|returns public static Log.Page -> public static Transfer.Log.Page|Log.page(Map<String, Object>), Log.page(User), Log.page(), Log.page(Map<String, Object>, User)":
            LOG_QUALIFIED,
        "fields|missing|Rule.key":
            RULE,
        "fields|missing|Rule.value":
            RULE,
        "fields|type|rules real=List<Rule> gen=List<Object>":
            RULE,
        "fields|type|metadata real=HashMap<String, Object> gen=Object":
            FREE_OBJECT,
        "fields|type|Log.Page.logs real=List<Log> gen=List<Transfer.Log>":
            LOG_QUALIFIED,
        "mapctor|rules real=other:parseRules((List<Object>) dataCopy.remove(\"rules\")) gen=cast:List":
            RULE,
        "imports|real=Generator,Resource,Rest,SubResource,GsonEvent | JsonDeserializationContext,JsonDeserializer,JsonElement,JsonObject,JsonParseException | InputStream,Type,ArrayList,HashMap,List,Map gen=Generator,Resource,Rest,SubResource,ErrorElement | InputStream,ArrayList,HashMap,List,Map":
            RULE,
    },
    "Balance": {
        "methods|missing|get(User)":
            BALANCE_GET,
        "methods|missing|get()":
            BALANCE_GET,
        "methods|extra|Balance(Map<String, Object>)":
            BALANCE_MAP_CTOR,
        "methods|extra|get(String)":
            BALANCE_GET,
        "methods|extra|get(String, User)":
            BALANCE_GET,
        "rest|get real={'getStream': 1} gen={'getId': 1}":
            BALANCE_GET,
        "mapctor|amount real=None gen=null":
            BALANCE_MAP_CTOR,
        "mapctor|currency real=None gen=null":
            BALANCE_MAP_CTOR,
        "mapctor|updated real=None gen=null":
            BALANCE_MAP_CTOR,
    },
    "Webhook": {
        "methods|missing|create(Map<String, Object>)":
            SINGLE_CREATE,
        "methods|missing|create(Map<String, Object>, User)":
            SINGLE_CREATE,
        "methods|extra|create(List<?>)":
            SINGLE_CREATE,
        "methods|extra|create(List<?>, User)":
            SINGLE_CREATE,
        "rest|create real={'postSingle': 1} gen={'post': 1}":
            SINGLE_CREATE,
    },
    "VerifiedTransfer": {
        "methods|missing|parseRules(List<Object>)":
            RULE,
        "allArgs|VerifiedTransfer(all-args)|type|rules real=List<Transfer.Rule> gen=List<Object>":
            RULE,
        "allArgs|VerifiedTransfer(all-args)|type|metadata real=HashMap<String, Object> gen=Object":
            FREE_OBJECT,
        "fields|type|rules real=List<Transfer.Rule> gen=List<Object>":
            RULE,
        "fields|type|metadata real=HashMap<String, Object> gen=Object":
            FREE_OBJECT,
        "mapctor|rules real=other:parseRules((List<Object>) dataCopy.remove(\"rules\")) gen=cast:List":
            RULE,
    },
}


# A guarda de build-resource bloqueia a criacao de Webhook (create de objeto unico) e de Balance (singleton), entao o
# golden os gera com --advisory, que nao a roda: aqui se mede a estrutura contra o real, e a guarda prova o bloqueio
# (tests/test_build_resource.py). Os dois nao se sobrepoem de proposito.
GOLDEN_FLAGS = {"Webhook": ("--advisory",), "Balance": ("--advisory",)}


def _structures(builtOnce, assertGenerated, resource: str, generated: str | None = None) -> tuple[dict, dict, str]:
    code, out, target = builtOnce(resource, "java", *GOLDEN_FLAGS.get(resource, ()))
    assert code == 0, out
    tool = assertGenerated.loadTool("derive-contract")
    source = generated or (target / "src/main/java/com/starkbank" / f"{resource}.java").read_text(encoding="utf-8")
    real = (REAL_SOURCES / f"{resource}.java").read_text(encoding="utf-8")
    return (_structure(real, resource, True, tool, assertGenerated),
            _structure(source, resource, False, tool, assertGenerated), source)


def _audit(found: dict[str, str], expected: dict[str, str],
           limits: dict[str, tuple[str, str]] | None = None) -> tuple[list[str], list[str]]:
    """Divergencia sem entrada e entrada que parou de casar, em qualquer dos dois registros: as duas reprovam."""
    known = {**expected, **(limits or {})}
    return ([key for key in found if key not in known], [key for key in known if key not in found])


def _limitCounts(limits: dict[str, tuple[str, str]]) -> dict[str, int]:
    return {severity: sum(1 for entry in limits.values() if entry[0] == severity) for severity in SEVERITIES}


def _report(resource: str, real: dict, generated: dict, found: dict[str, str]) -> str:
    methods, blocks = _compared(real, generated)
    expected, limits = EXPECTED[resource], KNOWN_GENERATOR_LIMITS[resource]
    byMotive = {}
    for key in found:
        if key in expected:
            byMotive.setdefault(expected[key], []).append(key)
    lines = [f"=== {resource}: {methods} metodos e {blocks} blocos Javadoc comparados, {len(found)} divergencias:"
             f" {sum(len(keys) for keys in byMotive.values())} esperadas, {len(limits)} limites do gerador"]
    lines += [f"  {len(keys)} x {motive}" for motive, keys in byMotive.items()]
    lines += [f"  limites por severidade: {_limitCounts(limits)}"]
    lines += [f"  [{severity}] {key}" for key, (severity, _) in limits.items()]
    return "\n".join(lines)


@requiresGenerator
@pytest.mark.parametrize("resource", GOLDEN_RESOURCES)
def test_generatedJavaMatchesTheRealSdkOnStructure(builtOnce, assertGenerated, resource):
    real, generated, _ = _structures(builtOnce, assertGenerated, resource)

    found = _divergences(real, generated)
    sys.stdout.write(f"{_report(resource, real, generated, found)}\n")
    unexpected, stale = _audit(found, EXPECTED[resource], KNOWN_GENERATOR_LIMITS[resource])

    assert not unexpected, f"{resource}: divergencia sem entrada nos registros:\n" + "\n".join(f"  {key}" for key in unexpected)
    assert not stale, f"{resource}: entrada que parou de casar, remova do registro:\n" + "\n".join(f"  {key}" for key in stale)


def test_everyExpectedDivergenceCarriesAWrittenMotive():
    assert set(EXPECTED) == set(GOLDEN_RESOURCES)
    for resource, entries in EXPECTED.items():
        for key, motive in entries.items():
            assert len(motive.split()) >= 5, f"{resource}: motivo curto demais para {key}"
            assert "*" not in key, f"{resource}: curinga na chave {key}"
            assert "Entrega 9" not in motive, f"{resource}: {key} e limite do gerador, nao divergencia esperada"


def test_everyKnownGeneratorLimitCarriesASeverityAndTheEntrega9Reference():
    assert set(KNOWN_GENERATOR_LIMITS) == set(GOLDEN_RESOURCES)
    for resource, entries in KNOWN_GENERATOR_LIMITS.items():
        for key, (severity, motive) in entries.items():
            assert severity in SEVERITIES, f"{resource}: severidade invalida em {key}"
            assert "Entrega 9" in motive, f"{resource}: {key} sem referencia a Entrega 9"
            assert key not in EXPECTED[resource], f"{resource}: {key} esta nos dois registros"


def test_theGeneratorLimitsAreCountedBySeverityInTheFinalReport():
    totals = {severity: 0 for severity in SEVERITIES}
    for entries in KNOWN_GENERATOR_LIMITS.values():
        for severity, count in _limitCounts(entries).items():
            totals[severity] += count

    sys.stdout.write(f"=== limites do gerador no golden, por severidade (Entrega 9): {totals}\n")

    assert sum(totals.values()) == sum(len(entries) for entries in KNOWN_GENERATOR_LIMITS.values())
    assert totals["runtime-errado"] > 0, "o golden nao pode esconder o limite que muda o comportamento em runtime"


def _swapBlocks(source: str, first: str, second: str) -> str:
    """Troca de lugar dois metodos (com o Javadoc) pelas assinaturas, para quebrar a ordem dos overloads."""
    blocks = source.split("\n\n")
    one = next(index for index, block in enumerate(blocks) if first in block)
    two = next(index for index, block in enumerate(blocks) if second in block)
    blocks[one], blocks[two] = blocks[two], blocks[one]
    return "\n\n".join(blocks)


def _replaced(source: str, old: str, new: str) -> str:
    assert old in source, f"a mutacao nao encontrou {old!r}"
    return source.replace(old, new, 1)


MUTATIONS = {
    "overloadOrder": lambda source: _swapBlocks(source, "query(User user) throws", "query() throws"),
    "directCast": lambda source: _replaced(
        _replaced(source, "Number delay = (Number) dataCopy.remove(\"delay\");", ""),
        "this.delay = delay == null ? null : delay.intValue();", "this.delay = (Integer) dataCopy.remove(\"delay\");"),
    "userLine": lambda source: _replaced(source, "starkbank.Settings.user", "starkbank.User.defaultUser"),
    "filterRemoved": lambda source: _replaced(
        source, "     * limit [integer, default null]: maximum number of objects to be retrieved. Unlimited if null. ex: 35\n", ""),
    "returnOnlyReadFromTheMap": lambda source: _replaced(source, "this.status = null;", "this.status = (String) dataCopy.remove(\"status\");"),
    "throwsRemoved": lambda source: _replaced(
        source, "public static Page page(Map<String, Object> params, User user) throws Exception {",
        "public static Page page(Map<String, Object> params, User user) {"),
    "restPrimitive": lambda source: _replaced(source, "return Rest.getId(data, id, user);", "return Rest.getStream(data, id, user);"),
    "tagMissing": lambda source: _replaced(source, "     * @return list of SplitProfile objects with updated attributes\n", ""),
}
MUTATION_KEYS = {
    "overloadOrder": "methods|order|moved=",
    "directCast": "mapctor|delay real=number:intValue gen=directcast:Integer",
    "userLine": "doc|user real=",
    "filterRemoved": "doc|filters",
    "returnOnlyReadFromTheMap": "mapctor|status real=null gen=cast:String",
    "throwsRemoved": "signature|throws Exception -> (sem throws)",
    "restPrimitive": "rest|get real={'getId': 1} gen={'getStream': 1}",
    "tagMissing": "doc|tags -[param:profiles, return]",
}


@requiresGenerator
@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_theGoldenFailsWhenTheGeneratedSourceIsMutated(builtOnce, assertGenerated, mutation):
    _, _, source = _structures(builtOnce, assertGenerated, "SplitProfile")
    real, generated, _ = _structures(builtOnce, assertGenerated, "SplitProfile", MUTATIONS[mutation](source))

    unexpected, _ = _audit(_divergences(real, generated), EXPECTED["SplitProfile"], KNOWN_GENERATOR_LIMITS["SplitProfile"])

    assert any(MUTATION_KEYS[mutation] in key for key in unexpected), unexpected


def test_theMapConstructorAssignmentsAreClassifiedByTheirShape():
    assert _assignmentClass("null") == "null"
    assert _assignmentClass("delay == null ? null : delay.intValue()") == "number:intValue"
    assert _assignmentClass('((Number) data.get("amount")).longValue()') == "number:longValue"
    assert _assignmentClass('(String[]) dataCopy.remove("tags")') == "cast:String[]"
    assert _assignmentClass('(List<String>) data.get("tags")') == "cast:List"
    assert _assignmentClass('(Integer) dataCopy.remove("delay")') == "directcast:Integer"
    assert _assignmentClass('Long.parseLong((String) data.get("x"))') == "parse:parseLong"


def test_movedItemsAreTheOnesOutsideTheLongestCommonOrder():
    assert _moved(["a", "b", "c", "d"], ["a", "b", "c", "d"]) == []
    assert _moved(["a", "b", "c", "d"], ["b", "c", "d", "a"]) == ["a"]
    assert _moved(["a", "b", "c"], ["a", "z", "c", "b"]) == ["b"]


def test_aDivergenceWithoutAnEntryAndAnEntryThatStoppedMatchingAreBothReported():
    unexpected, stale = _audit({"doc|new": "x", "doc|kept": "x"}, {"doc|kept": "motivo escrito aqui agora", "doc|gone": "motivo escrito aqui agora"})

    assert unexpected == ["doc|new"]
    assert stale == ["doc|gone"]


def test_aKnownGeneratorLimitThatStoppedMatchingFailsLikeAnExpectedOne():
    limits = {"rest|create": ("runtime-errado", "Entrega 9: motivo escrito aqui"), "doc|gone": ("cosmetico", "Entrega 9: x")}

    unexpected, stale = _audit({"rest|create": "x", "doc|new": "x"}, {}, limits)

    assert unexpected == ["doc|new"]
    assert stale == ["doc|gone"]
