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

CONTRACT_KINDS = ("signature", "declaration", "constructor", "inner", "export", "namespace")

_PLACEHOLDER = re.compile(r"\{\{.*?\}\}")
_EMPTY_ARG = re.compile(r"\(\s*,|,\s*\)|,\s*,")
_EMPTY_SLOT = re.compile(r"\(\s+(?:instanceof|:|\))")
_DROPPED_NAME = re.compile(r"\w {2,}[:,)]")
_JAVAC_SYNTAX = re.compile(r"error: (?:.*expected|illegal start|bad initializer|reached end of file)")


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
    entries: dict[str, list[str]] = {kind: [] for kind in CONTRACT_KINDS}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, value = line.partition(" ")
        if kind in entries and value.strip():
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


def checkContract(source: str, contract: dict[str, list[str]], filePath: str) -> list[Issue]:
    collapsed = normalise(source)
    issues = []
    for kind in CONTRACT_KINDS:
        for expected in contract[kind]:
            if normalise(expected) in collapsed:
                continue
            issues.append(
                Issue(filePath, 0, 0, CODE_CONTRACT_GAP, f"{kind} ausente: {expected}")
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


def resolveContract(target: Path, language: str, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    candidate = Path(CONTRACT_DIR) / f"{language}-{target.stem.lower()}.contract"
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

    if language == "java":
        ran, syntaxIssues = javacSyntax(target)
        if ran:
            executed.append("sintaxe (javac)")
            blocking.extend(syntaxIssues)
        else:
            skipped.append("verificação de sintaxe indisponível: javac ausente ou inoperante")

    contractPath = resolveContract(target, language, args.contract)
    gaps = []
    if contractPath and contractPath.exists():
        gaps = checkContract(source, parseContract(contractPath), filePath)

    emit(f"[INFO] {target} — linguagem {language}")
    emit(f"[INFO] verificações executadas: {', '.join(executed)}")
    for reason in skipped:
        emit(f"[WARN] {reason}")
    emit("[INFO] compilação contra o SDK real não implementada — roda no CI")
    if not contractPath:
        emit("[INFO] sem contrato para este recurso — gap não verificado")

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