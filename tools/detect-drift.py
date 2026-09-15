import sys
import yaml
import argparse
import subprocess
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
SCHEMAS_DIR = "apis/schemas"

EXIT_IN_SYNC = 0
EXIT_DRIFTED = 1
EXIT_ERROR = 2
EXIT_NOT_APPLIED = 3


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def extract(resource: str, root: str | None) -> tuple[int, str]:
    command = [sys.executable, str(TOOLS_DIR / "extract-schema.py"), resource]
    command += ["--from", root] if root else []
    result = subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT)
    return (result.returncode, result.stdout if result.returncode == 0 else result.stdout + result.stderr)


def schemasOf(document: dict) -> dict:
    return ((document or {}).get("components") or {}).get("schemas") or {}


def compare(extracted: dict, applied: dict) -> list[str]:
    """Diferenças por schema e por campo, em linha, para caber no corpo de uma PR."""
    differences = []
    fromPython, fromSpec = schemasOf(extracted), schemasOf(applied)

    for name in sorted(set(fromPython) | set(fromSpec)):
        if name not in fromSpec:
            differences.append(f"schema novo no sdk-python: {name}")
            continue
        if name not in fromPython:
            differences.append(f"schema ausente no sdk-python: {name}")
            continue

        pythonProps = (fromPython[name] or {}).get("properties") or {}
        specProps = (fromSpec[name] or {}).get("properties") or {}
        for field in sorted(set(pythonProps) | set(specProps)):
            if field not in specProps:
                differences.append(f"{name}.{field}: campo novo no sdk-python")
                continue
            if field not in pythonProps:
                differences.append(f"{name}.{field}: campo saiu do sdk-python")
                continue
            if pythonProps[field] != specProps[field]:
                differences.append(f"{name}.{field}: {specProps[field]} → {pythonProps[field]}")

        for key in sorted(set(fromPython[name] or {}) | set(fromSpec[name] or {})):
            if key == "properties":
                continue
            if (fromPython[name] or {}).get(key) != (fromSpec[name] or {}).get(key):
                differences.append(f"{name}.{key}: "
                                   f"{(fromSpec[name] or {}).get(key)} → {(fromPython[name] or {}).get(key)}")

    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara o schema versionado com o sdk-python atual")
    parser.add_argument("resource", help="nome da classe, ex: Invoice")
    parser.add_argument("--from", dest="root", default=None, help="raiz do SDK Python")
    parser.add_argument("--schemas", default=SCHEMAS_DIR, help=f"diretório dos schemas (padrão: {SCHEMAS_DIR})")
    args = parser.parse_args()

    applied = REPO_ROOT / args.schemas / f"{args.resource.lower()}.yaml"
    if not applied.is_file():
        emit(f"[INFO] {args.resource} não tem schema versionado em {args.schemas} — nada a comparar")
        return EXIT_NOT_APPLIED

    code, output = extract(args.resource, args.root)
    if code != 0:
        emit(f"[ERROR] extração falhou para {args.resource}")
        for line in output.splitlines():
            emit(f"    {line}")
        return EXIT_ERROR

    differences = compare(yaml.safe_load(output), yaml.safe_load(applied.read_text(encoding="utf-8")))
    if not differences:
        emit(f"[OK] {args.resource}: schema versionado em sincronia com o sdk-python")
        return EXIT_IN_SYNC

    emit(f"[ERROR] {args.resource}: {len(differences)} divergência(s) entre o sdk-python e {applied}")
    for difference in differences:
        emit(f"    {difference}")
    return EXIT_DRIFTED


if __name__ == "__main__":
    sys.exit(main())
