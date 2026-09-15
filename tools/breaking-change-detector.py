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
METHODS = ("get", "post", "put", "patch", "delete")

RULE_OPERATION = "removes operation"
RULE_SCHEMA = "removes schema"
RULE_PARAM = "removes required param"
RULE_TYPE = "type changes"
RULE_REQUIRED = "required field added"

SHAPE_KEYS = ("type", "format", "items")


def loadFast(text: str):
    """libyaml quando disponivel: a spec tem 7.131 linhas e o parser puro custa 138 ms."""
    return yaml.load(text, Loader=SafeLoader)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def gitShow(commit: str, path: str) -> str | None:
    result = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def loadSpec(ref: str | None = None) -> dict | None:
    if ref is None:
        return loadFast(Path(SPEC_FILE).read_text(encoding="utf-8"))

    content = gitShow(ref, SPEC_FILE)
    if content is None:
        emit("[INFO] nenhuma spec anterior encontrada (primeiro commit)")
        return None
    return loadFast(content)


def refLoader(ref: str | None):
    """Decisao 67: o `$ref` do lado anterior tem de ser lido no commit anterior,
    senao mudanca dentro de apis/schemas/ passa invisivel."""
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
    if not isinstance(property, dict):
        return {}
    shape = {key: property[key] for key in SHAPE_KEYS if key in property}
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


def removedSchemas(current: dict, previous: dict) -> list[dict]:
    return [
        {"type": "removed_schema", "severity": "MAJOR", "rule": RULE_SCHEMA, "schema": name}
        for name in sorted(set(previous) - set(current))
    ]


def changedTypes(current: dict, previous: dict) -> list[dict]:
    findings = []
    for name in sorted(set(previous) & set(current)):
        before = (previous[name] or {}).get("properties") or {}
        after = (current[name] or {}).get("properties") or {}
        for field in sorted(set(before) & set(after)):
            wasShape, isShape = shapeOf(before[field]), shapeOf(after[field])
            if wasShape != isShape:
                findings.append({"type": "type_changed", "severity": "MAJOR", "rule": RULE_TYPE,
                                 "schema": name, "field": field, "was": wasShape, "now": isShape})
        for field in sorted(set(before) - set(after)):
            findings.append({"type": "removed_field", "severity": "MAJOR", "rule": RULE_SCHEMA,
                             "schema": name, "field": field})
    return findings


def addedRequiredFields(current: dict, previous: dict) -> list[dict]:
    findings = []
    for name in sorted(set(previous) & set(current)):
        before = set((previous[name] or {}).get("required") or [])
        after = set((current[name] or {}).get("required") or [])
        for field in sorted(after - before):
            findings.append({"type": "required_field_added", "severity": "MAJOR",
                             "rule": RULE_REQUIRED, "schema": name, "field": field})
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

    return (removedPaths(currPaths, prevPaths)
            + removedOperations(currPaths, prevPaths)
            + removedRequiredParams(currPaths, prevPaths)
            + removedSchemas(currSchemas, prevSchemas)
            + changedTypes(currSchemas, prevSchemas)
            + addedRequiredFields(currSchemas, prevSchemas))


def main() -> int:
    parser = argparse.ArgumentParser(description="Detecta breaking changes entre versões da spec")
    parser.add_argument("--base", required=True,
                        help="commit de comparação: o alvo da PR, nunca HEAD — a árvore é HEAD")
    args = parser.parse_args()

    if not Path(SPEC_FILE).exists():
        emit(f"[ERROR] spec não encontrada: {SPEC_FILE}")
        return 2

    emit("[INFO] verificando breaking changes...")
    previous = loadSpec(args.base)
    current = loadSpec()
    changes = detectBreakingChanges(current, previous,
                                    resolveSchemas(current, refLoader(None)),
                                    resolveSchemas(previous, refLoader(args.base)))

    if changes:
        emit("")
        emit(f"[ERROR] {len(changes)} breaking change(s) detectada(s):")
        emit(json.dumps(changes, indent=2))
        for rule in sorted({change["rule"] for change in changes}):
            emit(f"[INFO] regra violada, governance.md: {rule}")
        return 1

    emit("[OK] nenhuma breaking change detectada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
