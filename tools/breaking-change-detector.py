#!/usr/bin/env python3
"""Breaking Change Detector — detects breaking changes between spec versions."""
import json
import subprocess
import sys
import yaml

SPEC_FILE = "apis/spec-v2.openapi.yaml"

def load_spec(ref=None):
    try:
        if ref:
            result = subprocess.run(["git", "show", f"{ref}:{SPEC_FILE}"], capture_output=True, text=True, check=True)
            return yaml.safe_load(result.stdout)
        else:
            with open(SPEC_FILE, "r") as f:
                return yaml.safe_load(f)
    except Exception as e:
        if ref == "HEAD":
            print("ℹ️  No previous spec found (first commit)")
            return None
        raise

def detect_breaking_changes(current, previous):
    changes = []
    if not previous:
        return changes

    prev_paths = previous.get("paths", {})
    curr_paths = current.get("paths", {})

    for path in set(prev_paths.keys()) - set(curr_paths.keys()):
        changes.append({"type": "removed_path", "severity": "MAJOR", "path": path})

    return changes

def main():
    print("🔍 Detecting breaking changes...")
    previous = load_spec("HEAD")
    current = load_spec(None)
    changes = detect_breaking_changes(current, previous)

    if changes:
        print(f"\n❌ Breaking changes detected ({len(changes)}):")
        print(json.dumps(changes, indent=2))
        sys.exit(1)

    print("✅ No breaking changes detected")
    sys.exit(0)

if __name__ == "__main__":
    main()