import os
import sys
import json
import yaml
import shutil
import argparse
import tempfile
import subprocess
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
SPEC_FILE = "apis/spec-v2.openapi.yaml"


def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXIT_ABSENT_UPSTREAM = loadTool("derive-contract").EXIT_ABSENT_UPSTREAM
EXIT_NO_EXECUTABLE = 127
EXIT_EXISTS = 4  # recurso já existe no SDK de referência: relatório emitido, nada entregue

SCOPE = "supportingFiles=false,apiDocs=false,modelDocs=false,apiTests=false,modelTests=false"

GENERATORS = {
    "java": [
        {
            "role": "main",
            "generator": "java",
            "templateDir": "templates/java",
            "extra": [
                "--model-package", "com.starkbank",
                "--type-mappings", "OffsetDateTime=String,Date=String,LocalDate=String,URI=String,BigDecimal=Number",
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
SUBRESOURCE_USERS = ("x-sdk-page", "x-sdk-log")
REST_USERS = ("x-sdk-create", "x-sdk-put", "x-sdk-get", "x-sdk-query", "x-sdk-page",
              "x-sdk-update", "x-sdk-delete", "x-sdk-cancel", "x-sdk-pdf", "x-sdk-log")


def declaredFlags(resource: str) -> set[str]:
    spec = loadFast((REPO_ROOT / SPEC_FILE).read_text(encoding="utf-8"))
    schema = ((spec.get("components") or {}).get("schemas") or {}).get(resource) or {}
    return {key for key in schema if key.startswith("x-sdk-")}


def hasObjectArray(resource: str) -> bool:
    """Array de objeto sai como List<Object> no template; array de string sai como String[]."""
    lint = loadTool("lint-spec")
    specPath = REPO_ROOT / SPEC_FILE
    schemas = (loadFast(specPath.read_text(encoding="utf-8")).get("components") or {}).get("schemas") or {}
    props = lint.schemaProps(schemas.get(resource), specPath)
    return any(
        prop.get("type") == "array" and (prop.get("items") or {}).get("type") != "string"
        for prop in props.values()
    )


def resolveRef(spec: dict, name: str, apisDir: Path) -> dict | None:
    """Schema do componente, seguindo o `$ref` para o arquivo em `schemas/` quando ha."""
    entry = ((spec.get("components") or {}).get("schemas") or {}).get(name)
    if entry is None or "$ref" not in entry:
        return entry
    fileName, _, fragment = entry["$ref"].partition("#")
    node = loadFast((apisDir / fileName).read_text(encoding="utf-8"))
    for part in fragment.strip("/").split("/"):
        node = node[part]
    return node


def stageJavaDocs(resource: str, stagedDir: Path) -> dict:
    """Copia `apis/` para `stagedDir` e injeta o Javadoc e o corpo do Map-ctor pre-renderizados na entrada do
    recurso da copia.

    O gerador descarta as extensoes `x-*` do arquivo apontado por `$ref` (so as irmas do `$ref` chegam a
    `vendorExtensions`), entao `x-sdk-java-docs` e `x-sdk-java-code` ficam ao lado das flags `x-sdk-*` da spec
    copiada. O `x-sdk-docs` original segue no schema copiado e a spec e os schemas do repo nao sao tocados."""
    javaDocs = loadTool("java-docs")
    javaCode = loadTool("java-code")
    apisDir = REPO_ROOT / "apis"
    specPath = REPO_ROOT / SPEC_FILE
    specText = specPath.read_text(encoding="utf-8")
    spec = loadFast(specText)
    schema = resolveRef(spec, resource, apisDir)
    createSchema = resolveRef(spec, f"{resource}Create", apisDir)
    flags = {key for key in spec["components"]["schemas"][resource] if key.startswith("x-sdk-")}
    entity = javaDocs.describeEntity(schema, createSchema)
    rendered = javaDocs.renderDocs(resource, schema.get("x-sdk-docs") or {}, flags, entity)
    code = javaCode.renderMapConstructor(schema, entity)

    shutil.copytree(apisDir, stagedDir / "apis")
    entryLine = f"\n    {resource}:\n"
    if specText.count(entryLine) != 1:
        raise ValueError(f"entrada de {resource} ausente ou repetida na spec")
    docsLine = (f"      x-sdk-java-docs: {json.dumps(rendered['lines'])}\n"
                f"      x-sdk-java-code: {json.dumps(code)}\n")
    stagedSpec = stagedDir / SPEC_FILE
    stagedSpec.write_text(specText.replace(entryLine, f"{entryLine}{docsLine}", 1), encoding="utf-8")
    return {"spec": stagedSpec, "warnings": rendered["warnings"], "fallbacks": rendered["fallbacks"]}


def javaImports(flags: set[str], objectArray: bool = False) -> list[str]:
    """Quais imports o template Java pode emitir sem virar import morto.

    Só o que vale entra: additional-property chega como string, e "false" é
    verdadeiro no Mustache.
    """
    properties = []
    if flags & set(GENERATOR_USERS):
        properties.append("usesGenerator=true")
    if objectArray:
        properties.append("usesObjectList=true")
    if flags & set(LIST_USERS):
        properties.append("usesList=true")
    if flags & set(SUBRESOURCE_USERS):
        properties.append("usesSubResource=true")
    if flags & set(REST_USERS):
        properties.append("usesRest=true")
    return properties


def loadFast(text: str):
    """libyaml quando disponivel: a spec tem 7.131 linhas e o parser puro custa 138 ms."""
    return yaml.load(text, Loader=SafeLoader)


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def runStep(label: str, command: list[str], quiet: bool = False,
            accepted: tuple[int, ...] = (0,)) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT)
    except FileNotFoundError:
        if not quiet:
            emit(f"[ERROR] {label}: executável ausente: {command[0]}")
        return (EXIT_NO_EXECUTABLE, "")
    if not quiet:
        for line in (result.stdout + result.stderr).splitlines():
            if line.strip():
                emit(f"    {line}")
        status = "OK" if result.returncode in accepted else "ERROR"
        emit(f"[{status}] {label} (exit {result.returncode})")
    return (result.returncode, result.stdout)


def lintSpec(resource: str) -> int:
    code, _ = runStep(
        "lint-spec",
        [sys.executable, str(TOOLS_DIR / "lint-spec.py"), "--quiet", "--require", resource],
    )
    return code


def plannedPairs(resource: str, language: str) -> dict[str, tuple[str, str]]:
    """Pares por papel, nao por posicao: o papel e o primeiro segmento do caminho de origem,
    que o `place-generated` monta a partir do proprio LAYOUTS."""
    code, output = runStep(
        "layout",
        [sys.executable, str(TOOLS_DIR / "place-generated.py"), resource, "--lang", language, "--list"],
        quiet=True,
    )
    if code != 0:
        return {}

    pairs = {}
    for line in output.splitlines():
        if "->" not in line:
            continue
        source, target = (part.strip() for part in line.split("->"))
        pairs[source.split("/", 1)[0]] = (source, target)
    return pairs


def generate(resource: str, run: dict, outputDir: Path, specFile: str = SPEC_FILE) -> int:
    extra = list(run["extra"])
    properties = javaImports(declaredFlags(resource), hasObjectArray(resource)) if run["generator"] == "java" else []
    if properties:
        extra.extend(["--additional-properties", ",".join(properties)])

    code, _ = runStep(
        f"generate {run['role']} ({run['generator']})",
        [
            "npx", "openapi-generator-cli", "generate",
            "-i", specFile,
            "-g", run["generator"],
            "--template-dir", run["templateDir"],
            "-o", str(outputDir),
            "--global-property", f"models={resource},{SCOPE}",
            *extra,
        ],
    )
    return code


def stageAndAnnounceDocs(resource: str, stagedDir: Path) -> Path:
    staged = stageJavaDocs(resource, stagedDir)
    for warning in staged["warnings"]:
        emit(f"[WARN] javadoc: {warning}")
    if staged["fallbacks"]:
        emit(f"[INFO] javadoc: {len(staged['fallbacks'])} operação(ões) sem x-sdk-docs usam o bloco genérico:"
             f" {', '.join(staged['fallbacks'])}")
    return staged["spec"]


BUCKET_NAMES = {"gaps": "lacuna canônica", "subObjects": "sub-objeto", "extras": "extra do Java"}


def gapBucket(resource: str) -> str | None:
    """Em qual balde o `list-gaps` põe o recurso: lacuna real, sub-objeto ou nenhum."""
    code, output = runStep(
        "list-gaps",
        [sys.executable, str(TOOLS_DIR / "list-gaps.py"), "--json"],
        quiet=True,
    )
    if code != 0:
        return None
    report = json.loads(output)
    for bucket, name in BUCKET_NAMES.items():
        if resource in report.get(bucket, []):
            return name
    return None


def unsupportedShape(resource: str) -> str | None:
    """Formato que o template Java nao sabe emitir: create de objeto unico (sai create(List) com Rest.post) e get
    sem id (singleton, sai get(String id)). Quem decide e o docstring, o mesmo predicado da documentacao."""
    javaDocs = loadTool("java-docs")
    spec = loadFast((REPO_ROOT / SPEC_FILE).read_text(encoding="utf-8"))
    docs = resolveRef(spec, resource, REPO_ROOT / "apis").get("x-sdk-docs") or {}
    flags = declaredFlags(resource)
    for operation in ("create", "put"):
        if f"x-sdk-{operation}" in flags and not javaDocs.returnsList(docs.get(operation) or {}):
            return f"create de objeto unico ({resource}): o template emite create(List) com Rest.post"
    getDoc = docs.get("get")
    # `uuid` (DynamicBrcode) tambem identifica: singleton e o get que so recebe `user`
    if "x-sdk-get" in flags and getDoc and {param["name"] for param in getDoc.get("params") or []} <= {"user"}:
        return f"get sem id (singleton) ({resource})"
    return None


def announceIntent(resource: str, language: str, absent: int, total: int) -> None:
    if absent == total:
        bucket = gapBucket(resource) if language == "java" else None
        detail = f", classificado pelo list-gaps como {bucket}" if bucket else ""
        emit(f"[INFO] {resource} não existe no sdk-{language}{detail}"
             " — esta execução preenche a lacuna")
        return
    if absent == 0:
        emit(f"[INFO] {resource} já existe no sdk-{language} — relatório de paridade, nada será entregue")
        return
    emit(f"[INFO] {resource} existe parcialmente no sdk-{language} ({total - absent} de {total} papéis presentes)"
         " — relatório de paridade, nada será entregue")


def deriveRuler(resource: str, language: str, role: str, into: Path) -> tuple[Path | None, int]:
    """Régua efêmera, extraída do SDK real nesta execução. Exit 3 = recurso novo no alvo."""
    ruler = into / f"{language}-{resource.lower()}-{role}.contract"
    code, _ = runStep(
        f"derive-contract {role}",
        [sys.executable, str(TOOLS_DIR / "derive-contract.py"), resource,
         "--lang", language, "--role", role, "--out", str(ruler)],
        accepted=(0, EXIT_ABSENT_UPSTREAM),
    )
    if code != 0:
        return (None, code)
    return (ruler, code)


def referenceSha(language: str) -> str:
    deriveContract = loadTool("derive-contract")
    repo = deriveContract.referenceRepo(language)
    if repo is None:
        return "referência não resolvida"
    code, output = runStep("reference sha", ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], quiet=True)
    return output.strip() if code == 0 else "SHA indisponível"


def assertGenerated(artifact: Path, language: str, role: str, ruler: Path | None,
                    strict: bool, substitution: bool, style: bool = False) -> int:
    command = [sys.executable, str(TOOLS_DIR / "assert-generated.py"), str(artifact),
               "--lang", language, "--role", role]
    command += ["--contract", str(ruler)] if ruler else ["--allow-missing-contract"]
    command += ["--strict"] if strict else []
    command += ["--substitution"] if substitution else []
    command += ["--style"] if style else []
    code, _ = runStep(f"assert-generated {role}", command)
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


def reportParity(language: str, runs: list[dict], artifacts: dict[str, Path],
                 rulers: dict[str, Path | None]) -> None:
    """Mede sem bloquear: o gate de substituição roda em todo papel presente, sem parar no primeiro gap."""
    present = [run["role"] for run in runs if rulers[run["role"]] is not None]
    failed = [role for role in present
              if assertGenerated(artifacts[role], language, role, rulers[role], True, True) != 0]
    emit(f"[INFO] paridade medida pelo gate estrito: {len(present) - len(failed)} passaram,"
         f" {len(failed)} reprovaram, {len(runs) - len(present)} ausentes no SDK de referência")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera um recurso, verifica e posiciona no layout do SDK")
    parser.add_argument("resource", help="nome do recurso, ex: SplitProfile")
    parser.add_argument("--lang", required=True, help=f"linguagem ({', '.join(sorted(GENERATORS))})")
    parser.add_argument("--into", required=True, help="raiz do repositório do SDK alvo")
    parser.add_argument("--advisory", action="store_true",
                        help="mede sem bloquear; para desenvolvimento de template, nunca no pipeline")
    args = parser.parse_args()

    if args.lang not in GENERATORS:
        emit(f"[ERROR] linguagem sem gerador configurado: {args.lang}")
        emit(f"[INFO] configuradas: {', '.join(sorted(GENERATORS))}")
        return 2

    targetDir = Path(args.into).resolve()
    os.chdir(REPO_ROOT)  # os subprocessos rodam na raiz do repo; o caminho relativo do chamador ja foi resolvido
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
    faltando = [run["role"] for run in runs if run["role"] not in pairs]
    if faltando or len(pairs) != len(runs):
        emit(f"[ERROR] layout e geradores discordam de papel: {len(pairs)} no layout,"
             f" {len(runs)} configurado(s), sem par: {', '.join(faltando) or 'nenhum'}")
        return 1

    generatedDir = Path(tempfile.mkdtemp(prefix="build-resource-"))
    stagedDir = Path(tempfile.mkdtemp(prefix="build-apis-"))
    try:
        emit(f"[INFO] régua derivada de sdk-{args.lang} @ {referenceSha(args.lang)}")

        for run in runs:
            specFile = SPEC_FILE
            if run["generator"] == "java":
                specFile = str(stageAndAnnounceDocs(args.resource, stagedDir))
            if generate(args.resource, run, generatedDir / run["role"], specFile) != 0:
                return 1

        rulers, absent = {}, 0
        for run in runs:
            ruler, code = deriveRuler(args.resource, args.lang, run["role"], generatedDir)
            if code == EXIT_ABSENT_UPSTREAM:
                absent += 1
            elif code != 0:
                emit(f"[ERROR] régua de {run['role']} não derivada — sem régua não se gera")
                return 1
            rulers[run["role"]] = ruler

        announceIntent(args.resource, args.lang, absent, len(runs))

        unsupported = unsupportedShape(args.resource) if args.lang == "java" and absent == len(runs) else None
        if unsupported and not args.advisory:
            emit(f"[ERROR] formato nao suportado: {unsupported}")
            return 1

        artifacts = {}
        for run in runs:
            source, _ = pairs[run["role"]]
            artifacts[run["role"]] = generatedDir / source
            if not artifacts[run["role"]].is_file():
                emit(f"[ERROR] gerador {run['role']} retornou 0 mas não produziu o artefato: {source}")
                return 1

        if absent < len(runs):
            reportParity(args.lang, runs, artifacts, rulers)
            return EXIT_EXISTS

        for run in runs:
            if assertGenerated(artifacts[run["role"]], args.lang, run["role"], rulers[run["role"]],
                               not args.advisory, False, style=not args.advisory) != 0:
                return 1

        if place(args.resource, args.lang, generatedDir, targetDir) != 0:
            return 1

        emit(f"[OK] {args.resource} construído e verificado em {targetDir}")
        return 0
    finally:
        shutil.rmtree(generatedDir, ignore_errors=True)
        shutil.rmtree(stagedDir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())