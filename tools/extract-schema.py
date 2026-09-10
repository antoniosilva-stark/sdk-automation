import re
import ast
import sys
import argparse
from pathlib import Path

SDK_PYTHON = "_references/sdk-python"

SECTION_REQUIRED = "required"
SECTION_OPTIONAL = "optional"
SECTION_READONLY = "readonly"

_SECTIONS = {
    "## Parameters (required):": SECTION_REQUIRED,
    "## Parameters (optional):": SECTION_OPTIONAL,
    "## Attributes (return-only):": SECTION_READONLY,
    "## Attributes:": SECTION_READONLY,
}

_FIELD = re.compile(r"^-\s+(\w+)\s+\[(.+)\]\s*:\s*(.*)$")

TYPES = {
    "integer": {"type": "integer"},
    "int": {"type": "integer"},
    "long": {"type": "integer"},
    "number": {"type": "number"},
    "float": {"type": "number"},
    "string": {"type": "string"},
    "str": {"type": "string"},
    "boolean": {"type": "boolean"},
    "bool": {"type": "boolean"},
    "datetime.datetime": {"type": "string", "format": "date-time"},
    "datetime.date": {"type": "string", "format": "date"},
    "binary": {"type": "string", "format": "binary"},
}


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def camelCase(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def modulePath(root: Path, resource: str) -> Path:
    module = resource.lower()
    return root / "starkbank" / module / f"__{module}.py"


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


def parseType(raw: str) -> dict:
    text = raw.strip().split(",")[0].split(" or ")[0].strip()

    listMatch = re.match(r"^list of (\w+?)s?$", text)
    if listMatch:
        inner = listMatch.group(1).lower()
        return {"type": "array", "items": TYPES.get(inner, {"type": "object"})}
    if text.startswith("list of "):
        return {"type": "array", "items": {"type": "object"}}
    if text.startswith(("dictionary", "dict")):
        return {"type": "object"}

    return dict(TYPES.get(text.lower(), {"type": "string"}))


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
            fields.append(
                {
                    "name": camelCase(match.group(1)),
                    "pythonName": match.group(1),
                    "schema": parseType(match.group(2)),
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


def buildDocument(resource: str, summary: str, fields: list[dict]) -> str:
    readFields = orderFields(fields)
    createFields = [f for f in fields if f["section"] != SECTION_READONLY]

    lines = ["components:", "  schemas:"]
    lines.extend(renderSchema(resource, summary, readFields, 4))
    lines.append("")
    lines.extend(renderSchema(f"{resource}Create", f"{resource} creation request", createFields, 4))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrai schema OpenAPI de um recurso do SDK Python")
    parser.add_argument("resource", help="nome da classe, ex: Transaction")
    parser.add_argument("--from", dest="root", default=SDK_PYTHON, help=f"raiz do SDK Python (padrão: {SDK_PYTHON})")
    parser.add_argument("--out", help="arquivo de saída (padrão: stdout)")
    args = parser.parse_args()

    root = Path(args.root)
    if not (root / "starkbank").is_dir():
        emit(f"[ERROR] {root} não contém starkbank/ — é o SDK do Stark Infra ou caminho errado")
        return 2

    source = modulePath(root, args.resource)
    if not source.is_file():
        emit(f"[ERROR] módulo não encontrado: {source}")
        return 2

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
        emit(f"[ERROR] docstring de {args.resource} não declara campos no formato esperado")
        return 1

    defaults = initDefaults(node)
    unknown = [f["pythonName"] for f in fields if f["pythonName"] not in defaults]
    if unknown:
        emit(f"[WARN] documentados mas ausentes no __init__: {', '.join(unknown)}")

    document = buildDocument(args.resource, summary, fields)
    if args.out:
        Path(args.out).write_text(document, encoding="utf-8")
        emit(f"[OK] {args.resource}: {len(fields)} campo(s) → {args.out}")
        return 0

    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    sys.exit(main())
