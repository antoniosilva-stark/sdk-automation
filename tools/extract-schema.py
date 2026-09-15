import re
import os
import ast
import sys
import yaml
import argparse
import subprocess
from pathlib import Path

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

REPO_ROOT = Path(__file__).resolve().parent.parent
CONVENTIONS_FILE = REPO_ROOT / "apis/type-conventions.yaml"

VENDORED_ROOT = Path("_references/sdk-python")

SECTION_REQUIRED = "required"
SECTION_OPTIONAL = "optional"
SECTION_READONLY = "readonly"

_SECTIONS = {
    "## Parameters (required):": SECTION_REQUIRED,
    "## Parameters (optional):": SECTION_OPTIONAL,
    "## Parameters (conditionally required):": SECTION_OPTIONAL,
    "## Attributes (return-only):": SECTION_READONLY,
    "## Attributes (return only):": SECTION_READONLY,
    "## Attributes:": SECTION_READONLY,
}

_FIELD = re.compile(r"^-\s+(\w+)\s+\[(.+)\]\s*:\s*(.*)$")
_OPTIONS = re.compile(r"Options:\s*(.+)$")

OPERATION_FLAGS = {
    "create": "x-sdk-create",
    "put": "x-sdk-put",
    "get": "x-sdk-get",
    "query": "x-sdk-query",
    "page": "x-sdk-page",
    "delete": "x-sdk-delete",
    "update": "x-sdk-update",
    "pdf": "x-sdk-pdf",
    "cancel": "x-sdk-cancel",
}

TYPES = {
    "integer": {"type": "integer"},
    "int": {"type": "integer"},
    "long": {"type": "integer"},
    "number": {"type": "number"},
    "float": {"type": "number", "format": "float"},
    "string": {"type": "string"},
    "str": {"type": "string"},
    "boolean": {"type": "boolean"},
    "bool": {"type": "boolean"},
    "datetime.datetime": {"type": "string", "format": "date-time"},
    "datetime.date": {"type": "string", "format": "date"},
    "binary": {"type": "string", "format": "binary"},
}


def loadFast(text: str):
    """libyaml quando disponivel: a spec tem 7.131 linhas e o parser puro custa 138 ms."""
    return yaml.load(text, Loader=SafeLoader)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def warn(text: str) -> None:
    sys.stderr.write(f"{text}\n")


def integerFormat(field: str, conventions: Path = CONVENTIONS_FILE) -> str | None:
    if not Path(conventions).is_file():
        return None
    table = loadFast(Path(conventions).read_text(encoding="utf-8")) or {}
    return (table.get("integerFormats") or {}).get(field)


def stringFormat(field: str, conventions: Path = CONVENTIONS_FILE) -> str | None:
    if not Path(conventions).is_file():
        return None
    table = loadFast(Path(conventions).read_text(encoding="utf-8")) or {}
    return (table.get("stringFormats") or {}).get(field)


def declaredOptions(description: str) -> list[str]:
    """`Options:` e dominio fechado; `ex:` e exemplo.

    Tratar exemplo como enum foi o que produziu `accountType: [checking, savings]` a mao,
    que rejeita `salary` e `payment` — valores que a API aceita.
    """
    match = _OPTIONS.search(description)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def camelCase(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def modulePath(root: Path, resource: str) -> Path:
    module = resource.lower()
    return root / "starkbank" / module / f"__{module}.py"


def exportedNames(initSource: str) -> list[str]:
    names = []
    for node in ast.parse(initSource).body:
        if not isinstance(node, ast.ImportFrom):
            continue
        names.extend(alias.asname or alias.name for alias in node.names)
    return names


def declaredOperations(root: Path, resource: str) -> dict:
    module = resource.lower()
    package = Path(root) / "starkbank" / module
    initFile = package / "__init__.py"
    if not initFile.is_file():
        return {"flags": [], "unsupported": [], "missingInit": True}

    exported = [name for name in exportedNames(initFile.read_text(encoding="utf-8")) if name[:1].islower()]
    flags = sorted({OPERATION_FLAGS[name] for name in exported if name in OPERATION_FLAGS})
    unsupported = sorted({name for name in exported if name not in OPERATION_FLAGS and name != "log"})

    if (package / "log").is_dir():
        flags = sorted(set(flags) | {"x-sdk-log"})

    if not flags:
        flags = ["x-sdk-data-only"]

    return {"flags": flags, "unsupported": unsupported, "missingInit": False}


def resourceClass(source: str, resource: str) -> ast.ClassDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == resource:
            return node
    raise LookupError(resource)


def initDefaults(node: ast.ClassDef) -> dict[str, bool]:
    """Maps each __init__ argument to whether it has a default (i.e. is optional)."""
    for item in node.body:
        if not isinstance(item, ast.FunctionDef) or item.name != "__init__":
            continue
        args = [a.arg for a in item.args.args if a.arg != "self"]
        withDefault = set(args[len(args) - len(item.args.defaults):]) if item.args.defaults else set()
        return {name: name in withDefault for name in args}
    return {}


CHECK_FORMATS = {
    "check_datetime": ("string", "date-time"),
    "check_datetime_or_date": ("string", "date-time"),
    "check_date": ("string", "date"),
}


def initAssignments(node: ast.ClassDef) -> dict[str, str | None]:
    """Campo atribuido no __init__ e o check_* que o envolve, quando houver."""
    assignments: dict[str, str | None] = {}
    for item in node.body:
        if not isinstance(item, ast.FunctionDef) or item.name != "__init__":
            continue
        for statement in item.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target = statement.targets[0]
            if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
                continue
            if target.value.id != "self":
                continue
            call = statement.value
            checker = call.func.id if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) else None
            assignments[target.attr] = checker
    return assignments


def fieldsFromInit(node: ast.ClassDef) -> list[dict]:
    defaults = initDefaults(node)
    assignments = initAssignments(node)

    fields = []
    if "id" in defaults and "id" not in assignments:
        fields.append(
            {
                "name": "id",
                "pythonName": "id",
                "schema": {"type": "string"},
                "description": "tipo inferido do __init__, nao declarado no docstring",
                "section": SECTION_READONLY,
            }
        )

    for pythonName, checker in assignments.items():
        kind, fmt = CHECK_FORMATS.get(checker or "", ("string", None))
        name = camelCase(pythonName)
        convention = integerFormat(name) if checker is None else None
        if convention:
            kind, fmt = "integer", convention
        schema = {"type": kind}
        if fmt:
            schema["format"] = fmt

        fields.append(
            {
                "name": name,
                "pythonName": pythonName,
                "schema": schema,
                "description": "tipo inferido do __init__, nao declarado no docstring",
                "section": SECTION_OPTIONAL if defaults.get(pythonName, True) else SECTION_REQUIRED,
            }
        )
    return fields


def resolveType(text: str) -> dict | None:
    listMatch = re.match(r"^list of (\w+?)s?$", text)
    if listMatch:
        inner = listMatch.group(1).lower()
        return {"type": "array", "items": TYPES.get(inner, {"type": "object"})}
    if text.startswith("list of "):
        return {"type": "array", "items": {"type": "object"}}
    if text.startswith(("dictionary", "dict")):
        return {"type": "object"}

    known = TYPES.get(text.lower())
    return dict(known) if known else None


def reconcileRequired(fields: list[dict], defaults: dict[str, bool]) -> list[str]:
    """Parametro sem default e obrigatorio, diga o docstring o que disser.

    `SplitProfile` documenta `delay` e `interval` em "Parameters (optional)" e os recebe
    posicionalmente: seguir a prosa afrouxaria o que o construtor exige.
    """
    corrigidos = []
    for field in fields:
        if field["section"] != SECTION_OPTIONAL:
            continue
        if defaults.get(field["pythonName"], True):
            continue
        field["section"] = SECTION_REQUIRED
        corrigidos.append(field["name"])
    return corrigidos


def typeAlternatives(raw: str) -> list[str]:
    """Alternativas separadas por virgula e por `or`, sem a clausula `default`.

    `scheduled [datetime.date, datetime.datetime or string, default now]` tem tres
    alternativas: cortar na primeira virgula perdia `datetime.datetime`.
    """
    parts = []
    for chunk in raw.strip().split(","):
        parts.extend(piece.strip() for piece in chunk.split(" or "))
    return [part for part in parts if part and not part.lower().startswith("default")]


def parseType(raw: str) -> dict:
    """Uniao vale a alternativa que o SDK sabe expressar, e a mais ampla entre elas.

    `delay [DateInterval or integer]` resolvia para `DateInterval`, desconhecido, e o campo
    virava string — regredindo um integer ja declarado na spec.
    """
    resolved = [schema for schema in map(resolveType, typeAlternatives(raw)) if schema]
    if not resolved:
        return {"type": "string"}

    for schema in resolved:
        if schema.get("format") == "date-time":
            return schema
    return resolved[0]


def parseDocstring(doc: str) -> tuple[str, list[dict]]:
    summary: list[str] = []
    fields: list[dict] = []
    section = None

    for raw in doc.splitlines():
        line = raw.strip()
        if line in _SECTIONS:
            section = _SECTIONS[line]
            continue
        if section is None:
            cleaned = line.lstrip("#").strip()
            if cleaned and not cleaned.endswith("object"):
                summary.append(cleaned)
            continue
        match = _FIELD.match(line)
        if match:
            name = camelCase(match.group(1))
            schema = parseType(match.group(2))
            if schema.get("type") == "integer":
                fmt = integerFormat(name)
                if fmt:
                    schema["format"] = fmt

            options = declaredOptions(match.group(3))
            if options and schema.get("type") == "string":
                schema["enum"] = options

            stringFmt = stringFormat(name)
            if stringFmt and schema.get("type") == "string" and "format" not in schema:
                schema["format"] = stringFmt

            fields.append(
                {
                    "name": name,
                    "pythonName": match.group(1),
                    "schema": schema,
                    "description": match.group(3).strip(),
                    "section": section,
                }
            )

    return (" ".join(summary), fields)


def orderFields(fields: list[dict]) -> list[dict]:
    """`id` first — the ID_ORDER rule that lint-spec enforces."""
    return sorted(fields, key=lambda field: field["name"] != "id")


def renderSchema(name: str, summary: str, fields: list[dict], indent: int) -> list[str]:
    pad = " " * indent
    lines = [f"{pad}{name}:", f"{pad}  type: object"]
    if summary:
        lines.append(f"{pad}  description: {yamlScalar(summary)}")
    if not fields:
        lines.append(f"{pad}  properties: {{}}")
        return lines

    lines.append(f"{pad}  properties:")

    for field in fields:
        lines.append(f"{pad}    {field['name']}:")
        for key, value in field["schema"].items():
            if key == "items":
                lines.append(f"{pad}      items:")
                for innerKey, innerValue in value.items():
                    lines.append(f"{pad}        {innerKey}: {innerValue}")
                continue
            lines.append(f"{pad}      {key}: {value}")
        if field["description"]:
            lines.append(f"{pad}      description: {yamlScalar(field['description'])}")

    required = [f["name"] for f in fields if f["section"] != SECTION_OPTIONAL]
    if required:
        lines.append(f"{pad}  required:")
        lines.extend(f"{pad}    - {name}" for name in required)
    return lines


def yamlScalar(text: str) -> str:
    collapsed = " ".join(text.split())
    escaped = collapsed.replace('"', '\\"')
    return f'"{escaped}"'


def provenance(root: Path, source: Path) -> str:
    """Fica em comentário de propósito: SHA dentro do YAML faria todo commit upstream
    virar defasagem falsa na comparação semântica."""
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True)
    sha = result.stdout.strip() if result.returncode == 0 else "desconhecido"
    return f"# fonte: starkbank/sdk-python@{sha} · {source.relative_to(root)}"


def buildDocument(resource: str, summary: str, fields: list[dict], header: str | None = None) -> str:
    readFields = orderFields(fields)
    createFields = [f for f in fields if f["section"] != SECTION_READONLY]

    lines = [header] if header else []
    lines += ["components:", "  schemas:"]
    lines.extend(renderSchema(resource, summary, readFields, 4))
    lines.append("")
    lines.extend(renderSchema(f"{resource}Create", f"{resource} creation request", createFields, 4))
    return "\n".join(lines) + "\n"


def isStarkBankSdk(root: Path) -> bool:
    return (root / "starkbank").is_dir()


def resolveRoot(explicit: str | None) -> Path:
    """Raiz do SDK Python: a explícita vence; depois SDK_PYTHON, o checkout local e o
    clone do CI — cada candidato só entra se realmente for o SDK do Stark Bank."""
    if explicit:
        return Path(explicit)

    fromEnv = os.environ.get("SDK_PYTHON")
    if fromEnv and isStarkBankSdk(Path(fromEnv)):
        return Path(fromEnv)
    return VENDORED_ROOT


def rootIssue(root: Path) -> str | None:
    """Distingue os 3 casos que antes saíam com a mesma mensagem."""
    if not root.exists():
        return f"{root} não existe — rode `make clone-sdk-ref` ou passe --from"
    if not root.is_dir():
        return f"{root} não é um diretório"
    if not isStarkBankSdk(root):
        return f"{root} não contém starkbank/ — pode ser o SDK do Stark Infra ou a raiz errada"
    return None


def reportOperations(root: Path, resource: str) -> int:
    operations = declaredOperations(root, resource)
    if operations["missingInit"]:
        emit(f"[ERROR] {resource} não tem __init__.py — operações não derivaveis")
        return 1

    if not operations["flags"]:
        emit(f"[ERROR] {resource} não exporta nenhuma operação conhecida")
        return 1

    for name in operations["unsupported"]:
        warn(f"[WARN] operação sem flag no template: {name}")

    for flag in operations["flags"]:
        emit(f"{flag}: true")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrai schema OpenAPI de um recurso do SDK Python")
    parser.add_argument("resource", help="nome da classe, ex: Transaction")
    parser.add_argument("--from", dest="root", default=None,
                        help=f"raiz do SDK Python (padrão: SDK_PYTHON ou {VENDORED_ROOT})")
    parser.add_argument("--out", help="arquivo de saída (padrão: stdout)")
    parser.add_argument("--operations", action="store_true",
                        help="emite as flags x-sdk-* derivadas dos exports, em vez do schema")
    args = parser.parse_args()

    root = resolveRoot(args.root)
    issue = rootIssue(root)
    if issue:
        emit(f"[ERROR] {issue}")
        return 2

    source = modulePath(root, args.resource)
    if not source.is_file():
        emit(f"[ERROR] módulo não encontrado: {source}")
        return 2

    if args.operations:
        return reportOperations(root, args.resource)

    try:
        node = resourceClass(source.read_text(encoding="utf-8"), args.resource)
    except LookupError:
        emit(f"[ERROR] classe {args.resource} não encontrada em {source}")
        return 1

    doc = ast.get_docstring(node)
    if not doc:
        emit(f"[ERROR] {args.resource} não tem docstring — nada a extrair")
        return 1

    summary, fields = parseDocstring(doc)
    if not fields:
        fields = fieldsFromInit(node)
        if not fields:
            emit(f"[ERROR] {args.resource} não declara campos no docstring nem no __init__")
            return 1
        warn(f"[WARN] {args.resource}: campos vieram do __init__, com tipo inferido")

    defaults = initDefaults(node)
    unknown = [f["pythonName"] for f in fields if f["pythonName"] not in defaults]
    if unknown:
        emit(f"[WARN] documentados mas ausentes no __init__: {', '.join(unknown)}")

    for field in reconcileRequired(fields, defaults):
        warn(f"[WARN] {args.resource}.{field}: documentado como opcional, posicional no __init__")

    document = buildDocument(args.resource, summary, fields, provenance(root, source))
    if args.out:
        Path(args.out).write_text(document, encoding="utf-8")
        emit(f"[OK] {args.resource}: {len(fields)} campo(s) → {args.out}")
        return 0

    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    sys.exit(main())
