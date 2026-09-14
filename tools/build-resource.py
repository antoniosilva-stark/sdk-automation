import sys
import yaml
import shutil
import argparse
import tempfile
import subprocess
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
SPEC_FILE = "apis/spec-v2.openapi.yaml"

SCOPE = "supportingFiles=false,apiDocs=false,modelDocs=false,apiTests=false,modelTests=false"

GENERATORS = {
    "java": [
        {
            "role": "main",
            "generator": "java",
            "templateDir": "templates/java",
            "extra": [
                "--model-package", "com.starkbank",
                "--type-mappings", "OffsetDateTime=String,Date=String,URI=String",
            ],
        },
        {
            "role": "test",
            "generator": "java",
            "templateDir": "templates/java-test",
            "extra": [
                "--model-package", "com.starkbank",
                "--type-mappings", "OffsetDateTime=String,Date=String,URI=String",
            ],
        },
    ],
    "node": [
        {"role": "impl", "generator": "javascript", "templateDir": "templates/nodejs-impl", "extra": []},
        {"role": "barrel", "generator": "javascript", "templateDir": "templates/nodejs-barrel", "extra": []},
        {"role": "types", "generator": "typescript-node", "templateDir": "templates/nodejs-types", "extra": []},
    ],
}


GENERATOR_USERS = ("x-sdk-query", "x-sdk-log")
LIST_USERS = ("x-sdk-create", "x-sdk-put", "x-sdk-page", "x-sdk-log")
SETTINGS_USERS = ("x-sdk-query", "x-sdk-page", "x-sdk-log")
REST_USERS = ("x-sdk-create", "x-sdk-put", "x-sdk-get", "x-sdk-query", "x-sdk-page",
              "x-sdk-update", "x-sdk-delete", "x-sdk-cancel", "x-sdk-pdf", "x-sdk-log")


def declaredFlags(resource: str) -> set[str]:
    spec = yaml.safe_load((REPO_ROOT / SPEC_FILE).read_text(encoding="utf-8"))
    schema = ((spec.get("components") or {}).get("schemas") or {}).get(resource) or {}
    return {key for key in schema if key.startswith("x-sdk-")}


def javaImports(flags: set[str]) -> list[str]:
    """Quais imports o template Java pode emitir sem virar import morto.

    Só o que vale entra: additional-property chega como string, e "false" é
    verdadeiro no Mustache.
    """
    properties = []
    if flags & set(GENERATOR_USERS):
        properties.append("usesGenerator=true")
    if flags & set(LIST_USERS):
        properties.append("usesList=true")
    if flags & set(SETTINGS_USERS):
        properties.append("usesSettings=true")
    if flags & set(REST_USERS):
        properties.append("usesRest=true")
    return properties


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def runStep(label: str, command: list[str], quiet: bool = False) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT)
    if not quiet:
        for line in (result.stdout + result.stderr).splitlines():
            if line.strip():
                emit(f"    {line}")
        emit(f"[{'OK' if result.returncode == 0 else 'ERROR'}] {label} (exit {result.returncode})")
    return (result.returncode, result.stdout)


def lintSpec(resource: str) -> int:
    code, _ = runStep(
        "lint-spec",
        [sys.executable, str(TOOLS_DIR / "lint-spec.py"), "--quiet", "--require", resource],
    )
    return code


def plannedPairs(resource: str, language: str) -> list[tuple[str, str]]:
    code, output = runStep(
        "layout",
        [sys.executable, str(TOOLS_DIR / "place-generated.py"), resource, "--lang", language, "--list"],
        quiet=True,
    )
    if code != 0:
        return []
    return [
        tuple(part.strip() for part in line.split("->"))
        for line in output.splitlines()
        if "->" in line
    ]


def generate(resource: str, run: dict, outputDir: Path) -> int:
    extra = list(run["extra"])
    properties = javaImports(declaredFlags(resource)) if run["generator"] == "java" else []
    if properties:
        extra.extend(["--additional-properties", ",".join(properties)])

    code, _ = runStep(
        f"generate {run['role']} ({run['generator']})",
        [
            "npx", "openapi-generator-cli", "generate",
            "-i", SPEC_FILE,
            "-g", run["generator"],
            "--template-dir", run["templateDir"],
            "-o", str(outputDir),
            "--global-property", f"models={resource},{SCOPE}",
            *extra,
        ],
    )
    return code


def assertGenerated(artifact: Path, language: str, role: str) -> int:
    code, _ = runStep(
        f"assert-generated {role}",
        [sys.executable, str(TOOLS_DIR / "assert-generated.py"), str(artifact),
         "--lang", language, "--role", role, "--strict"],
    )
    return code


def place(resource: str, language: str, generatedDir: Path, targetDir: Path) -> int:
    code, _ = runStep(
        "place-generated",
        [
            sys.executable, str(TOOLS_DIR / "place-generated.py"), resource,
            "--lang", language,
            "--from", str(generatedDir),
            "--to", str(targetDir),
        ],
    )
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera um recurso, verifica e posiciona no layout do SDK")
    parser.add_argument("resource", help="nome do recurso, ex: SplitProfile")
    parser.add_argument("--lang", required=True, help=f"linguagem ({', '.join(sorted(GENERATORS))})")
    parser.add_argument("--into", required=True, help="raiz do repositório do SDK alvo")
    args = parser.parse_args()

    if args.lang not in GENERATORS:
        emit(f"[ERROR] linguagem sem gerador configurado: {args.lang}")
        emit(f"[INFO] configuradas: {', '.join(sorted(GENERATORS))}")
        return 2

    targetDir = Path(args.into)
    if not targetDir.is_dir():
        emit(f"[ERROR] destino não é um diretório: {targetDir}")
        return 2

    if not shutil.which("npx"):
        emit("[ERROR] npx ausente — necessário para o gerador")
        return 2

    runs = GENERATORS[args.lang]
    emit(f"[INFO] {args.resource} ({args.lang}, {len(runs)} run(s)) → {targetDir}")

    if lintSpec(args.resource) != 0:
        return 1

    pairs = plannedPairs(args.resource, args.lang)
    if len(pairs) != len(runs):
        emit(f"[ERROR] layout declara {len(pairs)} artefato(s) mas há {len(runs)} run(s) configurado(s)")
        return 1

    generatedDir = Path(tempfile.mkdtemp(prefix="build-resource-"))

    for run in runs:
        if generate(args.resource, run, generatedDir / run["role"]) != 0:
            return 1

    for run, (source, _) in zip(runs, pairs):
        artifact = generatedDir / source
        if not artifact.is_file():
            emit(f"[ERROR] gerador {run['role']} retornou 0 mas não produziu o artefato: {source}")
            return 1
        if assertGenerated(artifact, args.lang, run["role"]) != 0:
            return 1

    if place(args.resource, args.lang, generatedDir, targetDir) != 0:
        return 1

    emit(f"[OK] {args.resource} construído e verificado em {targetDir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())