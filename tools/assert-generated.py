import re
import sys
import shutil
import argparse
import subprocess
from pathlib import Path
from dataclasses import dataclass

CONTRACT_DIR = "tests/contract"

CODE_PLACEHOLDER = "PLACEHOLDER"
CODE_EMPTY_TOKEN = "EMPTY_TOKEN"
CODE_SYNTAX = "SYNTAX"
CODE_CONTRACT_GAP = "CONTRACT_GAP"
CODE_DEAD_IMPORT = "DEAD_IMPORT"

CONTRACT_KINDS = ("signature", "declaration", "constructor", "inner", "export",
                  "namespace", "field", "require")
CONTRACT_NOTES = ("todo",)

_PLACEHOLDER = re.compile(r"\{\{.*?\}\}")
_EMPTY_ARG = re.compile(r"\(\s*,|,\s*\)|,\s*,")
_EMPTY_SLOT = re.compile(r"\(\s+(?:instanceof|:|\))")
_DROPPED_NAME = re.compile(r"\w {2,}[:,)]")
_JAVAC_SYNTAX = re.compile(r"error: (?:.*expected|illegal start|bad initializer|reached end of file)")
_IMPORT = re.compile(r"^import (?:static )?([\w.]+);", re.M)
_FIELD_NAME = re.compile(r"(\w+)\s*;\s*$")


@dataclass
class Issue:
    file: str
    line: int
    col: int
    code: str
    message: str


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def parseContract(path: Path) -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {kind: [] for kind in CONTRACT_KINDS + CONTRACT_NOTES}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, value = line.partition(" ")
        if kind not in entries:
            raise ValueError(f"{path}:{number} kind desconhecido: {kind!r}")
        if not value.strip():
            raise ValueError(f"{path}:{number} kind {kind!r} sem valor")
        entries[kind].append(value.strip())
    return entries


def normalise(text: str) -> str:
    return " ".join(text.split())


def checkPlaceholders(source: str, filePath: str) -> list[Issue]:
    issues = []
    for number, line in enumerate(source.splitlines(), start=1):
        for match in _PLACEHOLDER.finditer(line):
            issues.append(
                Issue(filePath, number, match.start(), CODE_PLACEHOLDER,
                      f"placeholder não resolvido: {match.group(0)}")
            )
    return issues


def checkEmptyTokens(source: str, filePath: str) -> list[Issue]:
    issues = []
    for number, line in enumerate(source.splitlines(), start=1):
        body = line.lstrip()
        if not body or body.startswith(("*", "//", "#")):
            continue
        for pattern in (_EMPTY_ARG, _EMPTY_SLOT, _DROPPED_NAME):
            match = pattern.search(body)
            if not match:
                continue
            issues.append(
                Issue(filePath, number, match.start(), CODE_EMPTY_TOKEN,
                      f"identificador vazio em {match.group(0)!r}")
            )
            break
    return issues


def checkDeadImports(source: str, filePath: str) -> list[Issue]:
    body = _IMPORT.sub("", source)
    issues = []
    seen = set()

    for number, line in enumerate(source.splitlines(), start=1):
        match = _IMPORT.match(line)
        if not match:
            continue
        target = match.group(1)

        if target in seen:
            issues.append(Issue(filePath, number, 0, CODE_DEAD_IMPORT, f"import duplicado: {target}"))
            continue
        seen.add(target)

        simple = target.rsplit(".", 1)[-1]
        if simple != "*" and not re.search(r"\b" + re.escape(simple) + r"\b", body):
            issues.append(Issue(filePath, number, 0, CODE_DEAD_IMPORT, f"import nao usado: {target}"))

    return issues


def declaredField(source: str, name: str) -> str | None:
    """Declaração do campo `name` como o fonte gerado a escreveu, com o tipo dele."""
    match = re.search(r"^\s*(public [^;{}\n]*\b" + re.escape(name) + r")\s*;", source, re.M)
    return f"{match.group(1)};" if match else None


def contractGap(kind: str, expected: str, source: str) -> str:
    if kind != "field":
        return f"{kind} ausente: {expected}"

    name = _FIELD_NAME.search(expected)
    found = declaredField(source, name.group(1)) if name else None
    if not found:
        return f"field ausente: {expected}"
    return f"field divergente: esperado {expected!r}, gerado {found!r}"


def checkContract(source: str, contract: dict[str, list[str]], filePath: str) -> list[Issue]:
    collapsed = normalise(source)
    issues = []
    for kind in CONTRACT_KINDS:
        for expected in contract[kind]:
            if normalise(expected) in collapsed:
                continue
            issues.append(
                Issue(filePath, 0, 0, CODE_CONTRACT_GAP, contractGap(kind, expected, source))
            )
    return issues


def javacWorks() -> bool:
    if not shutil.which("javac"):
        return False
    probe = subprocess.run(["javac", "-version"], capture_output=True, text=True)
    return probe.returncode == 0


def javacSyntax(target: Path) -> tuple[bool, list[Issue]]:
    if not javacWorks():
        return (False, [])

    result = subprocess.run(
        ["javac", "-d", "/tmp/assert-generated", str(target)],
        capture_output=True,
        text=True,
    )
    issues = []
    for line in result.stderr.splitlines():
        if not _JAVAC_SYNTAX.search(line):
            continue
        parts = line.split(":", 2)
        number = int(parts[1]) if len(parts) > 2 and parts[1].isdigit() else 0
        issues.append(Issue(str(target), number, 0, CODE_SYNTAX, parts[-1].strip()))
    return (True, issues)


def resolveContract(target: Path, language: str, explicit: str | None, role: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    stem = target.stem.lower()
    if role:
        byRole = Path(CONTRACT_DIR) / f"{language}-{stem}-{role}.contract"
        if byRole.exists():
            return byRole
    candidate = Path(CONTRACT_DIR) / f"{language}-{stem}.contract"
    return candidate if candidate.exists() else None


def detectLanguage(target: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    if target.suffix == ".java":
        return "java"
    return "node"


def reportIssues(issues: list[Issue]) -> None:
    for issue in sorted(issues, key=lambda i: (i.line, i.code)):
        location = f"{issue.file}:{issue.line}" if issue.line else issue.file
        emit(f"[ERROR] {location} {issue.code} {issue.message}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica o arquivo gerado contra o contrato do SDK real")
    parser.add_argument("target", help="arquivo gerado a verificar")
    parser.add_argument("--lang", choices=["java", "node"], default=None)
    parser.add_argument("--contract", default=None, help="arquivo .contract explícito")
    parser.add_argument("--strict", action="store_true", help="trata gap de contrato como reprovação")
    parser.add_argument("--role", help="papel do artefato, ex: impl, barrel, types")
    args = parser.parse_args()

    target = Path(args.target)
    if not target.exists():
        emit(f"[ERROR] arquivo gerado não encontrado: {target}")
        return 2

    language = detectLanguage(target, args.lang)
    source = target.read_text(encoding="utf-8")
    filePath = str(target)

    executed = ["estrutura"]
    skipped = []
    blocking = checkPlaceholders(source, filePath) + checkEmptyTokens(source, filePath)
    blocking += checkDeadImports(source, filePath)

    if language == "java":
        ran, syntaxIssues = javacSyntax(target)
        if ran:
            executed.append("sintaxe (javac)")
            blocking.extend(syntaxIssues)
        else:
            skipped.append("verificação de sintaxe indisponível: javac ausente ou inoperante")

    contractPath = resolveContract(target, language, args.contract, args.role)
    gaps = []
    pending = []
    if contractPath and contractPath.exists():
        try:
            contract = parseContract(contractPath)
        except ValueError as error:
            emit(f"[ERROR] contrato inválido: {error}")
            return 2
        gaps = checkContract(source, contract, filePath)
        pending = contract["todo"]

    emit(f"[INFO] {target} — linguagem {language}")
    emit(f"[INFO] verificações executadas: {', '.join(executed)}")
    skipped.append("compilação contra o SDK real: fora do alcance desta verificação"
                   " — o gate roda no workflow, sobre o checkout do alvo")
    for reason in skipped:
        emit(f"[WARN] {reason}")
    if not contractPath:
        emit("[INFO] sem contrato para este recurso — gap não verificado")
    for note in pending:
        emit(f"[INFO] paridade pendente, nao verificada: {note}")

    if blocking:
        emit("")
        reportIssues(blocking)
        emit("")
        emit(f"[ERROR] {len(blocking)} problema(s) bloqueante(s)")
        return 1

    if gaps and args.strict:
        emit("")
        reportIssues(gaps)
        emit("")
        emit(f"[ERROR] {len(gaps)} gap(s) de contrato em modo --strict")
        return 1

    if gaps:
        emit(f"[WARN] {len(gaps)} gap(s) de contrato — use --strict para reprovar")
        for gap in gaps[:5]:
            emit(f"  {gap.message}")
        if len(gaps) > 5:
            emit(f"  ... e mais {len(gaps) - 5}")

    emit(f"[OK] {target} sem problemas bloqueantes")
    return 0


if __name__ == "__main__":
    sys.exit(main())