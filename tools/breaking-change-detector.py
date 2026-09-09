"""Breaking Change Detector — detects breaking changes between spec versions."""

import sys
import json
import yaml
import subprocess
from pathlib import Path

SPEC_FILE = "apis/spec-v2.openapi.yaml"


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def loadSpec(ref: str | None = None) -> dict | None:
    if ref is None:
        return yaml.safe_load(Path(SPEC_FILE).read_text(encoding="utf-8"))

    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{SPEC_FILE}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        emit("[INFO] nenhuma spec anterior encontrada (primeiro commit)")
        return None

    return yaml.safe_load(result.stdout)


def detectBreakingChanges(current: dict, previous: dict | None) -> list[dict]:
    if not previous:
        return []

    prevPaths = previous.get("paths") or {}
    currPaths = current.get("paths") or {}

    return [
        {"type": "removed_path", "severity": "MAJOR", "path": path}
        for path in sorted(set(prevPaths) - set(currPaths))
    ]


def main() -> int:
    if not Path(SPEC_FILE).exists():
        emit(f"[ERROR] spec não encontrada: {SPEC_FILE}")
        return 2

    emit("[INFO] verificando breaking changes...")
    previous = loadSpec("HEAD")
    current = loadSpec()
    changes = detectBreakingChanges(current, previous)

    if changes:
        emit("")
        emit(f"[ERROR] {len(changes)} breaking change(s) detectada(s):")
        emit(json.dumps(changes, indent=2))
        return 1

    emit("[OK] nenhuma breaking change detectada")
    return 0


if __name__ == "__main__":
    sys.exit(main())