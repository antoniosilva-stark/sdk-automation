"""Breaking Change Detector — detects breaking changes between spec versions."""

import sys
import json
import yaml
import argparse
import subprocess
from pathlib import Path

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

SPEC_FILE = "apis/spec-v2.openapi.yaml"
APPROVALS_FILE = "apis/bc-approvals.yaml"
METHODS = ("get", "post", "put", "patch", "delete")

RULE_OPERATION = "removes operation"
RULE_SCHEMA = "removes schema"
RULE_PARAM = "removes required param"
RULE_TYPE = "type changes"
RULE_REQUIRED = "required field added"
RULE_REQUIRED_GONE = "removes required field"

SHAPE_KEYS = ("type", "format", "items", "enum")


def loadFast(text: str):
    """libyaml quando disponivel: a spec tem 7.131 linhas e o parser puro custa 138 ms."""
    return yaml.load(text, Loader=SafeLoader)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def gitShow(commit: str, path: str) -> str | None:
    result = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def refExists(ref: str) -> bool:
    probe = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                           capture_output=True, text=True)
    return probe.returncode == 0


def loadSpec(ref: str | None = None) -> dict | None:
    """Ref que nao resolve levanta; ref valida sem a spec e primeiro commit, caso legitimo."""
    if ref is None:
        return loadFast(Path(SPEC_FILE).read_text(encoding="utf-8"))

    if not refExists(ref):
        raise LookupError(ref)

    content = gitShow(ref, SPEC_FILE)
    if content is None:
        emit("[INFO] nenhuma spec anterior encontrada (primeiro commit)")
        return None
    return loadFast(content)


def refLoader(ref: str | None):
    """O `$ref` do lado anterior tem de ser lido no commit anterior, senao mudanca dentro
    de apis/schemas/ passa invisivel."""
    def load(relative: str) -> dict:
        path = (Path(SPEC_FILE).parent / relative).as_posix()
        if ref is None:
            target = Path(path)
            return loadFast(target.read_text(encoding="utf-8")) if target.is_file() else {}
        content = gitShow(ref, path)
        return loadFast(content) if content else {}
    return load


def resolveOne(schema, load) -> dict:
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not ref or "#" not in ref:
        return schema

    filePart, pointer = ref.split("#", 1)
    node = load(filePart)
    for segment in [part for part in pointer.split("/") if part]:
        node = (node or {}).get(segment) or {}

    merged = dict(node or {})
    merged.update({key: value for key, value in schema.items() if key != "$ref"})
    return merged


def resolveSchemas(spec: dict | None, load) -> dict:
    declared = ((spec or {}).get("components") or {}).get("schemas") or {}
    return {name: resolveOne(schema, load) for name, schema in declared.items()}


def shapeOf(property: dict | None) -> dict:
    """Valor de enum que some quebra quem o envia; valor novo nao quebra ninguem."""
    if not isinstance(property, dict):
        return {}
    shape = {key: property[key] for key in SHAPE_KEYS if key in property}
    if isinstance(shape.get("enum"), list):
        shape["enum"] = sorted(str(value) for value in shape["enum"])
    items = shape.get("items")
    if isinstance(items, dict):
        shape["items"] = {key: items[key] for key in SHAPE_KEYS if key in items}
    return shape


def removedPaths(current: dict, previous: dict) -> list[dict]:
    return [
        {"type": "removed_path", "severity": "MAJOR", "rule": RULE_OPERATION, "path": path}
        for path in sorted(set(previous) - set(current))
    ]


def removedOperations(current: dict, previous: dict) -> list[dict]:
    findings = []
    for path in sorted(set(previous) & set(current)):
        before = {method for method in METHODS if method in (previous[path] or {})}
        after = {method for method in METHODS if method in (current[path] or {})}
        for method in sorted(before - after):
            findings.append({"type": "removed_operation", "severity": "MAJOR",
                             "rule": RULE_OPERATION, "path": path, "method": method})
    return findings


def requiredParams(operation: dict | None) -> dict:
    return {
        (parameter.get("name"), parameter.get("in")): parameter
        for parameter in ((operation or {}).get("parameters") or [])
        if isinstance(parameter, dict) and parameter.get("required")
    }


def removedRequiredParams(current: dict, previous: dict) -> list[dict]:
    findings = []
    for path in sorted(set(previous) & set(current)):
        for method in METHODS:
            if method not in (previous[path] or {}) or method not in (current[path] or {}):
                continue
            before = requiredParams((previous[path] or {}).get(method))
            after = (current[path] or {}).get(method) or {}
            surviving = {(parameter.get("name"), parameter.get("in"))
                         for parameter in (after.get("parameters") or [])
                         if isinstance(parameter, dict)}
            for name, where in sorted(set(before) - surviving, key=lambda key: str(key)):
                findings.append({"type": "removed_required_param", "severity": "MAJOR",
                                 "rule": RULE_PARAM, "path": path, "method": method,
                                 "param": name, "in": where})
    return findings


def newlyRequiredParams(current: dict, previous: dict) -> list[dict]:
    """Parametro que passa a ser exigido quebra quem ja chama a operacao sem ele."""
    findings = []
    for path in sorted(set(previous) & set(current)):
        for method in METHODS:
            if method not in (previous[path] or {}) or method not in (current[path] or {}):
                continue
            before = requiredParams((previous[path] or {}).get(method))
            after = requiredParams((current[path] or {}).get(method))
            for name, where in sorted(set(after) - set(before), key=lambda key: str(key)):
                findings.append({"type": "param_became_required", "severity": "MAJOR",
                                 "rule": RULE_REQUIRED, "path": path, "method": method,
                                 "param": name, "in": where})
    return findings


def removedSchemas(current: dict, previous: dict) -> list[dict]:
    return [
        {"type": "removed_schema", "severity": "MAJOR", "rule": RULE_SCHEMA, "schema": name}
        for name in sorted(set(previous) - set(current))
    ]


def enumShrank(was: dict, now: dict) -> bool:
    return set(was.get("enum") or []) - set(now.get("enum") or []) != set()


def changedTypes(current: dict, previous: dict) -> list[dict]:
    findings = []
    for name in sorted(set(previous) & set(current)):
        before = (previous[name] or {}).get("properties") or {}
        after = (current[name] or {}).get("properties") or {}
        for field in sorted(set(before) & set(after)):
            wasShape, isShape = shapeOf(before[field]), shapeOf(after[field])
            if "enum" in wasShape and "enum" in isShape and not enumShrank(wasShape, isShape):
                wasShape, isShape = dict(wasShape), dict(isShape)
                wasShape.pop("enum"), isShape.pop("enum")
            if wasShape != isShape:
                findings.append({"type": "type_changed", "severity": "MAJOR", "rule": RULE_TYPE,
                                 "schema": name, "field": field, "was": wasShape, "now": isShape})
        for field in sorted(set(before) - set(after)):
            findings.append({"type": "removed_field", "severity": "MAJOR", "rule": RULE_SCHEMA,
                             "schema": name, "field": field})
    return findings


def collectRefs(node, found: set) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            collectRefs(value, found)
        return
    if isinstance(node, list):
        for value in node:
            collectRefs(value, found)


def requestSchemas(spec: dict | None) -> set:
    """Schemas alcancados por `requestBody`. Quem so aparece em `responses` e resposta.

    O sufixo `Create` seria convencao nossa; o `paths` e a fonte.
    """
    found = set()
    for path in ((spec or {}).get("paths") or {}).values():
        for operation in (path or {}).values():
            if isinstance(operation, dict) and operation.get("requestBody"):
                collectRefs(operation["requestBody"], found)
    return found


def changedRequiredFields(current: dict, previous: dict, requests: set) -> list[dict]:
    """Exigir campo novo quebra quem *envia*; deixar de garantir quebra quem *le*.

    Acrescentar `required` numa resposta e promessa mais forte do servidor: nao quebra
    ninguem. Tratar os dois casos como iguais produzia 181 alarmes falsos de 240.
    """
    findings = []
    for name in sorted(set(previous) & set(current)):
        before = set((previous[name] or {}).get("required") or [])
        after = set((current[name] or {}).get("required") or [])

        if name in requests:
            for field in sorted(after - before):
                findings.append({"type": "required_field_added", "severity": "MAJOR",
                                 "rule": RULE_REQUIRED, "schema": name, "field": field})
            continue

        surviving = (current[name] or {}).get("properties") or {}
        for field in sorted(before - after):
            if field not in surviving:
                continue
            findings.append({"type": "required_field_removed", "severity": "MAJOR",
                             "rule": RULE_REQUIRED_GONE, "schema": name, "field": field})
    return findings


def detectBreakingChanges(current: dict, previous: dict | None,
                          currentSchemas: dict | None = None,
                          previousSchemas: dict | None = None) -> list[dict]:
    if not previous:
        return []

    currPaths = current.get("paths") or {}
    prevPaths = previous.get("paths") or {}
    currSchemas = currentSchemas if currentSchemas is not None else resolveSchemas(current, lambda _: {})
    prevSchemas = previousSchemas if previousSchemas is not None else resolveSchemas(previous, lambda _: {})
    requests = requestSchemas(current) | requestSchemas(previous)

    return (removedPaths(currPaths, prevPaths)
            + removedOperations(currPaths, prevPaths)
            + removedRequiredParams(currPaths, prevPaths)
            + newlyRequiredParams(currPaths, prevPaths)
            + removedSchemas(currSchemas, prevSchemas)
            + changedTypes(currSchemas, prevSchemas)
            + changedRequiredFields(currSchemas, prevSchemas, requests))


def signature(change: dict) -> str:
    """Identidade de um achado, para a aprovacao casar por string exata."""
    if "path" in change:
        parts = [change["path"], change.get("method", ""), change.get("param", "")]
        return f"{change['type']} " + " ".join(part for part in parts if part)
    alvo = change.get("schema", "")
    if change.get("field"):
        alvo = f"{alvo}.{change['field']}"
    return f"{change['type']} {alvo}"


def parseApprovals(path: Path) -> list[dict]:
    """Aprovacao de BC: assinatura exata, motivo e quem aprovou. Nunca curinga.

    Mesma forma das dispensas de contrato — o que a governanca exige e uma decisao humana
    rastreavel, nao um interruptor.
    """
    target = Path(path)
    if not target.is_file():
        return []

    document = loadFast(target.read_text(encoding="utf-8")) or {}
    approvals = []
    for index, entry in enumerate(document.get("approvals") or [], start=1):
        entry = entry or {}
        assinatura = str(entry.get("signature") or "").strip()
        if not assinatura:
            raise ValueError(f"{target}:{index} aprovacao sem assinatura")
        if "*" in assinatura:
            raise ValueError(f"{target}:{index} aprovacao com curinga: {assinatura}")
        if not str(entry.get("reason") or "").strip():
            raise ValueError(f"{target}:{index} aprovacao sem motivo: {assinatura}")
        if not str(entry.get("approvedBy") or "").strip():
            raise ValueError(f"{target}:{index} aprovacao sem quem aprovou: {assinatura}")
        approvals.append({"signature": assinatura, "reason": entry["reason"].strip(),
                          "approvedBy": entry["approvedBy"].strip(),
                          "date": str(entry.get("date") or "").strip()})
    return approvals


def applyApprovals(changes: list[dict], approvals: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Bloqueantes, aprovados e aprovacoes que nao casaram com nada."""
    porAssinatura = {entry["signature"]: entry for entry in approvals}
    usadas, blocking, approved = set(), [], []

    for change in changes:
        entry = porAssinatura.get(signature(change))
        if not entry:
            blocking.append(change)
            continue
        usadas.add(entry["signature"])
        approved.append({**change, **entry})

    unused = [entry for entry in approvals if entry["signature"] not in usadas]
    return (blocking, approved, unused)


def main() -> int:
    parser = argparse.ArgumentParser(description="Detecta breaking changes entre versões da spec")
    parser.add_argument("--approvals", default=APPROVALS_FILE,
                        help=f"aprovações de BC registradas (padrão: {APPROVALS_FILE})")
    parser.add_argument("--base", required=True,
                        help="commit de comparação: o alvo da PR, nunca HEAD — a árvore é HEAD")
    args = parser.parse_args()

    if not Path(SPEC_FILE).exists():
        emit(f"[ERROR] spec não encontrada: {SPEC_FILE}")
        return 2

    emit("[INFO] verificando breaking changes...")
    try:
        previous = loadSpec(args.base)
    except LookupError as ref:
        emit(f"[ERROR] base {ref} não resolve para um commit — sem comparação não há veredito")
        return 2
    current = loadSpec()
    changes = detectBreakingChanges(current, previous,
                                    resolveSchemas(current, refLoader(None)),
                                    resolveSchemas(previous, refLoader(args.base)))

    try:
        approvals = parseApprovals(Path(args.approvals))
    except ValueError as error:
        emit(f"[ERROR] aprovação inválida: {error}")
        return 2

    changes, approved, unused = applyApprovals(changes, approvals)

    for entry in approved:
        emit(f"[INFO] aprovado: {signature(entry)} — {entry['reason']} ({entry['approvedBy']})")
    for entry in unused:
        emit(f"[WARN] aprovação não utilizada: {entry['signature']}"
             " — a mudança deixou de existir, remova a aprovação")

    if changes:
        emit("")
        emit(f"[ERROR] {len(changes)} breaking change(s) detectada(s):")
        emit(json.dumps(changes, indent=2))
        for rule in sorted({change["rule"] for change in changes}):
            emit(f"[INFO] regra violada, governance.md: {rule}")
        return 1

    emit("[OK] nenhuma breaking change bloqueante")
    return 0


if __name__ == "__main__":
    sys.exit(main())
