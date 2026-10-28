import os
import ast
import sys
import json
import argparse
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

TOOLS_DIR = Path(__file__).resolve().parent

JAVA_PACKAGE = "src/main/java/com/starkbank"
VENDORED_JAVA = Path("_references/sdk-java")
JAVA_ROOT = VENDORED_JAVA / JAVA_PACKAGE

JAVA_NOT_A_RESOURCE = frozenset({
    "User", "Project", "Organization", "MarketplaceApp", "Settings", "Key", "Cache", "Main",
})

PYTHON_NOT_A_RESOURCE = frozenset({"utils", "__pycache__"})

RESOURCE_BASES = frozenset({"Resource", "SubResource"})


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def isStarkBankSdk(root: Path) -> bool:
    return (root / JAVA_PACKAGE).is_dir()


def resolveJavaRoot(explicit: str | None = None) -> Path | None:
    """Raiz das classes Java: a explícita vence; depois SDK_JAVA, o clone e o checkout local."""
    if explicit:
        return Path(explicit)

    fromEnv = os.environ.get("SDK_JAVA")
    for candidate in ([Path(fromEnv)] if fromEnv else []) + [VENDORED_JAVA]:
        if isStarkBankSdk(candidate):
            return candidate / JAVA_PACKAGE
    return None


def declaredClass(source: str) -> str | None:
    """Nome da classe de recurso declarada no módulo, lido do fonte.

    O nome em UpperCamelCase não é derivável do diretório: `boletopayment` vira
    `BoletoPayment`, e não há separador para inferir a quebra.
    """
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {base.id for base in node.bases if isinstance(base, ast.Name)}
        if bases & RESOURCE_BASES:
            return node.name
    return None


def pythonModules(root: Path) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Classes canônicas (`__<dir>.py`), secundárias, e diretórios sem classe."""
    canonical: dict[str, str] = {}
    secondary: dict[str, str] = {}
    withoutClass: list[str] = []

    for directory in sorted(path for path in (root / "starkbank").iterdir() if path.is_dir()):
        if directory.name in PYTHON_NOT_A_RESOURCE:
            continue

        found = False
        for module in sorted(directory.glob("__*.py")):
            if module.name == "__init__.py":
                continue
            className = declaredClass(module.read_text(encoding="utf-8"))
            if not className:
                continue
            found = True
            target = canonical if module.stem == f"__{directory.name}" else secondary
            target[className] = directory.name

        if not found:
            withoutClass.append(directory.name)

    return (canonical, secondary, withoutClass)


def javaClasses(root: Path) -> set[str]:
    return {path.stem for path in root.glob("*.java")} - JAVA_NOT_A_RESOURCE


def buildReport(pythonRoot: Path, javaRoot: Path) -> dict:
    canonical, secondary, withoutClass = pythonModules(pythonRoot)
    java = javaClasses(javaRoot)

    subObjects = sorted(name for name in secondary if name not in java)

    return {
        "gaps": sorted(name for name in canonical if name not in java),
        "extras": sorted(java - set(canonical) - set(secondary)),
        "subObjects": subObjects,
        "withoutClass": sorted(withoutClass),
    }


def reportHuman(report: dict) -> None:
    gaps = report["gaps"]
    emit(f"[INFO] {len(gaps)} recurso(s) do Python sem contrapartida no Java")
    for name in gaps:
        emit(f"  {name}")

    for key, label in (("extras", "só no Java"),
                       ("subObjects", "sub-objeto, classe interna no Java"),
                       ("withoutClass", "sem classe de recurso no módulo")):
        if report[key]:
            emit(f"[INFO] {label}: {', '.join(report[key])}")


def loadExtractSchema():
    """`extract-schema.py` tem hífen no nome, então não é importável por `import`.

    Reusar em vez de copiar: a resolução da raiz do SDK Python já é regra testada lá,
    e esta é a segunda ferramenta a precisar dela.
    """
    spec = spec_from_file_location("extract_schema", TOOLS_DIR / "extract-schema.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="Lista recursos do SDK Python sem contrapartida no Java")
    parser.add_argument("--python", dest="pythonRoot", default=None, help="raiz do SDK Python")
    parser.add_argument("--java", dest="javaRoot", default=None, help=f"raiz das classes Java (padrão: SDK_JAVA ou {VENDORED_JAVA})")
    parser.add_argument("--json", action="store_true", help="saída em JSON, para alimentar matriz no workflow")
    args = parser.parse_args()

    extractSchema = loadExtractSchema()
    pythonRoot = extractSchema.resolveRoot(args.pythonRoot)
    issue = extractSchema.rootIssue(pythonRoot)
    if issue:
        emit(f"[ERROR] {issue}")
        return 2

    javaRoot = Path(args.javaRoot) if args.javaRoot else (resolveJavaRoot() or JAVA_ROOT)
    if not javaRoot.is_dir():
        emit(f"[ERROR] {javaRoot} não existe — rode `make clone-sdk-ref` ou passe --java")
        return 2

    report = buildReport(pythonRoot, javaRoot)
    if args.json:
        emit(json.dumps(report))
        return 0

    reportHuman(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
