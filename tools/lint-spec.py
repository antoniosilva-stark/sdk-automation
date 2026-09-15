import re
import sys
import yaml
import argparse
from pathlib import Path
from dataclasses import dataclass

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

SPEC_FILE = "apis/spec-v2.openapi.yaml"
CREATE_SUFFIX = "Create"

CODE_SCAFFOLDING = "SCAFFOLDING"
CODE_UNDECLARED = "UNDECLARED"
CODE_ID_ORDER = "ID_ORDER"
CODE_NO_OPERATION = "NO_OPERATION"
CODE_NO_PROVENANCE = "NO_PROVENANCE"
CODE_NO_ID = "NO_ID"
PROVENANCE_PREFIX = "# fonte: starkbank/sdk-python@"

MIN_READ_PROPS = 3
MIN_CREATE_PROPS = 1

OPERATION_FLAGS = (
    "x-sdk-create", "x-sdk-put", "x-sdk-get", "x-sdk-query", "x-sdk-page",
    "x-sdk-update", "x-sdk-delete", "x-sdk-cancel", "x-sdk-pdf", "x-sdk-data-only",
)
WRITE_FLAGS = ("x-sdk-create", "x-sdk-put")

_SCHEMA_LINE = re.compile(r"^    ([A-Za-z][A-Za-z0-9]*):", re.M)


@dataclass
class Issue:
    file: str
    line: int
    col: int
    code: str
    message: str


@dataclass
class ResourceReport:
    name: str
    line: int
    readProps: int
    createProps: int
    subObjects: int
    operations: list[str]
    reasons: list[tuple[str, str]]

    @property
    def generatable(self) -> bool:
        return not self.reasons


def loadFast(text: str):
    """libyaml quando disponivel: a spec tem 7.131 linhas e o parser puro custa 138 ms."""
    return yaml.load(text, Loader=SafeLoader)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def loadYaml(path: Path) -> dict:
    return loadFast(path.read_text(encoding="utf-8"))


def resolveRef(ref: str, specPath: Path) -> dict:
    if "#" not in ref:
        raise ValueError(f"$ref sem ponteiro não é suportado: {ref}")

    filePart, pointer = ref.split("#", 1)
    target = (specPath.parent / filePart).resolve()
    if not target.exists():
        raise FileNotFoundError(f"$ref aponta para arquivo inexistente: {target}")

    node = loadYaml(target)
    for segment in [s for s in pointer.split("/") if s]:
        if segment not in node:
            raise KeyError(f"ponteiro {pointer} quebrado em {segment!r} dentro de {target}")
        node = node[segment]
    return node


def refFile(schema: dict | None, specPath: Path) -> Path | None:
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    if not ref or "#" not in ref:
        return None
    filePart, _ = ref.split("#", 1)
    target = (specPath.parent / filePart).resolve()
    return target if target.is_file() else None


def declaresProvenance(path: Path) -> bool:
    first = path.read_text(encoding="utf-8").splitlines()[:1]
    return bool(first) and first[0].startswith(PROVENANCE_PREFIX) and "@desconhecido" not in first[0]


def resolveSchema(schema: dict | None, specPath: Path) -> dict:
    if not isinstance(schema, dict):
        return {}

    ref = schema.get("$ref")
    if not ref:
        return schema

    merged = dict(resolveSchema(resolveRef(ref, specPath), specPath))
    merged.update({key: value for key, value in schema.items() if key != "$ref"})
    return merged


def schemaProps(schema: dict | None, specPath: Path) -> dict:
    return resolveSchema(schema, specPath).get("properties") or {}


def declaredOperations(schema: dict) -> list[str]:
    return [flag for flag in OPERATION_FLAGS if schema.get(flag)]


def countSubObjects(props: dict) -> int:
    total = 0
    for definition in props.values():
        if not isinstance(definition, dict):
            continue
        if definition.get("type") == "object":
            total += 1
            continue
        items = definition.get("items")
        if definition.get("type") == "array" and isinstance(items, dict) and items.get("type") == "object":
            total += 1
    return total


def schemaLines(specPath: Path) -> dict[str, int]:
    source = specPath.read_text(encoding="utf-8")
    lines: dict[str, int] = {}
    for match in _SCHEMA_LINE.finditer(source):
        lines.setdefault(match.group(1), source.count("\n", 0, match.start()) + 1)
    return lines


def inspectResource(name: str, schemas: dict, specPath: Path, lines: dict[str, int]) -> ResourceReport:
    readSchema = resolveSchema(schemas.get(name), specPath)
    readProps = readSchema.get("properties") or {}
    createProps = schemaProps(schemas.get(name + CREATE_SUFFIX), specPath)
    operations = declaredOperations(readSchema)
    writes = [flag for flag in WRITE_FLAGS if readSchema.get(flag)]

    reasons: list[tuple[str, str]] = []
    names = list(readProps)
    schemaFile = refFile(schemas.get(name), specPath)
    if schemaFile and not declaresProvenance(schemaFile):
        reasons.append((CODE_NO_PROVENANCE,
                        f"{schemaFile.name} não declara de qual commit do sdk-python veio"))
    if not operations:
        reasons.append((CODE_NO_OPERATION, f"nenhuma operação declarada — use uma de {', '.join(OPERATION_FLAGS)}"))
    if names and "id" not in names:
        reasons.append((CODE_NO_ID,
                        "schema sem 'id': o template emite 'extends Resource' com 'super(null)'"
                        " e nao declara a primeira propriedade — no SDK real esses recursos sao"
                        " SubResource, que ainda nao tem template"))
    if "id" in names and names[0] != "id":
        reasons.append((CODE_ID_ORDER, f"'id' deve ser a primeira propriedade, mas veio depois de '{names[0]}'"))
    if len(readProps) < MIN_READ_PROPS:
        reasons.append((CODE_SCAFFOLDING, f"schema de leitura tem {len(readProps)} propriedades (mínimo {MIN_READ_PROPS})"))
    if writes and len(createProps) < MIN_CREATE_PROPS:
        reasons.append((
            CODE_SCAFFOLDING,
            f"{name}{CREATE_SUFFIX} tem {len(createProps)} propriedades (mínimo {MIN_CREATE_PROPS}) "
            f"porque {writes[0]} está declarado",
        ))

    return ResourceReport(
        name=name,
        line=lines.get(name, 0),
        readProps=len(readProps),
        createProps=len(createProps),
        subObjects=countSubObjects(readProps),
        operations=operations,
        reasons=reasons,
    )


def resourceNames(schemas: dict) -> list[str]:
    return sorted(n for n in schemas if not n.endswith(CREATE_SUFFIX))


def reportInventory(reports: list[ResourceReport]) -> None:
    generatable = [r for r in reports if r.generatable]
    scaffolding = [r for r in reports if not r.generatable]

    emit(f"[INFO] {len(reports)} recurso(s) declarado(s) na spec")
    emit("")
    emit(f"[OK] gerável ({len(generatable)}):")
    for report in generatable:
        subObjects = f", {report.subObjects} sub-objeto(s)" if report.subObjects else ""
        operations = ", ".join(flag.removeprefix("x-sdk-") for flag in report.operations)
        emit(f"  {report.name} — {report.readProps} props / {report.createProps} create{subObjects} [{operations}]")

    emit("")
    emit(f"[WARN] scaffolding ({len(scaffolding)}):")
    for report in scaffolding[:5]:
        emit(f"  {report.name} — {report.reasons[0][1]}")
    if len(scaffolding) > 5:
        emit(f"  ... e mais {len(scaffolding) - 5}")


def requiredIssues(reports: list[ResourceReport], required: set[str], specPath: Path) -> list[Issue]:
    declared = {r.name for r in reports}
    issues = [
        Issue(str(specPath), 0, 0, CODE_UNDECLARED, f"recurso exigido não está na spec: {name}")
        for name in sorted(required - declared)
    ]
    for report in reports:
        if report.name not in required or report.generatable:
            continue
        for code, message in report.reasons:
            issues.append(
                Issue(str(specPath), report.line, 0, code, f"{report.name}: {message}")
            )
    return issues


def reportIssues(issues: list[Issue]) -> None:
    emit("")
    for issue in sorted(issues, key=lambda i: (i.line, i.code)):
        emit(f"[ERROR] {issue.file}:{issue.line} {issue.code} {issue.message}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Classifica recursos da spec como geráveis ou scaffolding")
    parser.add_argument("--spec", default=SPEC_FILE, help=f"caminho da spec (padrão: {SPEC_FILE})")
    parser.add_argument("--require", default="", help="recursos que devem ser geráveis, separados por vírgula")
    parser.add_argument("--quiet", action="store_true", help="omite o inventário")
    args = parser.parse_args()

    specPath = Path(args.spec)
    if not specPath.exists():
        emit(f"[ERROR] spec não encontrada: {specPath}")
        return 2

    spec = loadYaml(specPath)
    schemas = (spec.get("components") or {}).get("schemas") or {}
    if not schemas:
        emit(f"[ERROR] nenhum components.schemas em {specPath}")
        return 2

    lines = schemaLines(specPath)
    reports = [inspectResource(name, schemas, specPath, lines) for name in resourceNames(schemas)]

    if not args.quiet:
        reportInventory(reports)

    required = {name.strip() for name in args.require.split(",") if name.strip()}
    if not required:
        emit("")
        emit("[INFO] modo inventário (sem --require) — não reprova")
        return 0

    issues = requiredIssues(reports, required, specPath)
    if issues:
        reportIssues(issues)
        emit("")
        emit(f"[ERROR] {len(issues)} problema(s) bloqueando a geração")
        return 1

    emit("")
    emit(f"[OK] os {len(required)} recurso(s) exigido(s) são geráveis")
    emit("[INFO] gerável significa schema não-vazio, não paridade com o SDK real")
    return 0


if __name__ == "__main__":
    sys.exit(main())