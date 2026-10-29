import re
import sys
import shutil
import tempfile
import argparse
import subprocess
from pathlib import Path
from collections import Counter
from dataclasses import dataclass
from importlib.util import spec_from_file_location, module_from_spec

TOOLS_DIR = Path(__file__).resolve().parent

CONTRACT_DIR = "tests/contract"
WAIVER_DIR = "tests/waivers"

CODE_PLACEHOLDER = "PLACEHOLDER"
CODE_EMPTY_TOKEN = "EMPTY_TOKEN"
CODE_SYNTAX = "SYNTAX"
CODE_CONTRACT_GAP = "CONTRACT_GAP"
CODE_DEAD_IMPORT = "DEAD_IMPORT"
CODE_MISSING_CONTRACT = "MISSING_CONTRACT"
CODE_STYLE_RESIDUE = "STYLE_PYTHON_RESIDUE"
CODE_STYLE_EMPTY_DOC = "STYLE_EMPTY_DOC"
CODE_STYLE_TRAILING_SPACE = "STYLE_TRAILING_SPACE"
CODE_STYLE_PARAM_ORPHAN = "STYLE_PARAM_ORPHAN"
CODE_STYLE_NUMERIC_CAST = "STYLE_NUMERIC_CAST"
CODE_STYLE_UNBRACED = "STYLE_UNBRACED"
CODE_STYLE_PYTHON_CTOR = "STYLE_PYTHON_CTOR"
CODE_STYLE_SNAKE_CASE = "STYLE_SNAKE_CASE"

_INLINE_COMMENT = re.compile(r"\s+#")
_WAIVER_SECTION = re.compile(r"^\[([\w.-]+)/([\w.-]+)\]$")

CONTRACT_KINDS = ("signature", "declaration", "constructor", "inner", "export",
                  "namespace", "field", "require")

_PLACEHOLDER = re.compile(r"\{\{.*?\}\}")
_EMPTY_ARG = re.compile(r"\(\s*,|,\s*\)|,\s*,")
_EMPTY_SLOT = re.compile(r"\(\s+(?:instanceof|:|\))")
_DROPPED_NAME = re.compile(r"\w {2,}[:,)]")
_JAVAC_DIAGNOSTIC = re.compile(r"^.*:(\d+): error: (.*)$")
# opcoes ocultas do javac: para depois do parse, entao todo erro reportado e de sintaxe e
# simbolo nao resolvido (o SDK real nao esta no classpath) nunca aparece
_JAVAC_PARSE_ONLY = ("-XDshould-stop.ifNoError=PARSE", "-XDshould-stop.ifError=PARSE")
_IMPORT = re.compile(r"^import (?:static )?([\w.]+);", re.M)
_FIELD_NAME = re.compile(r"(\w+)\s*;\s*$")
_JAVADOC_RESIDUE = re.compile(r"datetime\.\w|timedelta|\bNone\b|\bTrue\b|\bFalse\b|\\\"|starkbank\.user\b|User\.defaultUser")
_EMPTY_DOC_LINE = re.compile(r"\]\s*:\s*$")
_PARAM_TAG = re.compile(r"^\s*\*\s*@param\s+(\w+)")
_NUMERIC_REMOVE_CAST = re.compile(r"\(\s*(?:Integer|Long|Float|Double|Short|Byte)\s*\)\s*dataCopy\.remove\(")
_BRANCH_KEYWORD = re.compile(r"\b(?:if|for)\s*\(")
_PYTHON_CTOR = re.compile(r"\b[A-Z]\w*(?:\.[A-Z]\w*)*\(\w+=")
_SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


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
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, value = _INLINE_COMMENT.split(line, maxsplit=1)[0].partition(" ")
        if kind not in entries:
            raise ValueError(f"{path}:{number} kind desconhecido: {kind!r}")
        if not value.strip():
            raise ValueError(f"{path}:{number} kind {kind!r} sem valor")
        entries[kind].append(value.strip())
    return entries


def parseWaivers(path: Path) -> dict[tuple[str, str], list[dict]]:
    """Divergências aceitas, por recurso e papel. Motivo é obrigatório e string é exata."""
    waivers: dict[tuple[str, str], list[dict]] = {}
    section = None

    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        header = _WAIVER_SECTION.match(line)
        if header:
            section = (header.group(1), header.group(2))
            waivers.setdefault(section, [])
            continue

        if section is None:
            raise ValueError(f"{path}:{number} dispensa fora de seção [recurso/papel]")

        body, separator, reason = line.partition("#")
        if not separator or not reason.strip():
            raise ValueError(f"{path}:{number} dispensa sem motivo: {body.strip()}")

        kind, _, value = body.strip().partition(" ")
        if kind not in CONTRACT_KINDS:
            raise ValueError(f"{path}:{number} kind desconhecido: {kind!r}")
        if not value.strip():
            raise ValueError(f"{path}:{number} kind {kind!r} sem valor")
        if "*" in value:
            raise ValueError(f"{path}:{number} dispensa com curinga: {value.strip()}")

        waivers[section].append({"kind": kind, "value": value.strip(), "reason": reason.strip()})

    return waivers


def resolveWaivers(language: str, explicit: str | None) -> Path | None:
    candidate = Path(explicit) if explicit else Path(WAIVER_DIR) / f"{language}.waivers"
    return candidate if candidate.is_file() else None


def normalise(text: str) -> str:
    return " ".join(text.split())


def withoutComments(source: str) -> str:
    """Fonte sem comentários, com as strings intactas: uma assinatura citada em Javadoc não é código."""
    return "\n".join(loadTool("derive-contract").withoutNoise(source, keepStrings=True))


def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def splitTopLevel(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        depth += {"<": 1, ">": -1}.get(char, 0)
        current += char
    if current.strip():
        parts.append(current)
    return parts


def withoutParameterNames(entry: str) -> str:
    """Em Java o nome do parâmetro não afeta quem chama; tipo e ordem afetam."""
    start, end = entry.find("("), entry.rfind(")")
    if start < 0 or end < start:
        return normalise(entry)

    types = []
    for parameter in splitTopLevel(entry[start + 1:end]):
        words = normalise(parameter).rsplit(" ", 1)
        isNamed = len(words) == 2 and re.fullmatch(r"[A-Za-z_]\w*", words[1])
        types.append(words[0] if isNamed else normalise(parameter))
    return normalise(f"{entry[:start]}({', '.join(types)}){entry[end + 1:]}")


def javaShapes(source: str) -> dict[str, set[str]]:
    """Assinaturas e construtores do fonte gerado, sem nome de parâmetro."""
    shapes = {"signature": set(), "constructor": set()}
    for kind, value in loadTool("derive-contract").extractJava(source):
        if kind in shapes:
            shapes[kind].add(withoutParameterNames(value))
    return shapes


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


def commentLines(source: str) -> list[tuple[int, str]]:
    """Linhas que só têm comentário: o scanner de ruído as esvazia. Comentário ao fim de linha com código fica de fora."""
    cleaned = loadTool("derive-contract").withoutNoise(source, keepStrings=True)
    return [(number, raw) for number, (raw, clean) in enumerate(zip(source.split("\n"), cleaned), start=1)
            if raw.strip() and not clean.strip()]


def checkJavadocText(comments: list[tuple[int, str]], filePath: str) -> list[Issue]:
    issues = []
    for number, text in comments:
        residue = _JAVADOC_RESIDUE.search(text)
        if residue:
            issues.append(Issue(filePath, number, residue.start(), CODE_STYLE_RESIDUE,
                                f"resíduo de Python no Javadoc {residue.group(0)!r}: {text.strip()}"))
        if _EMPTY_DOC_LINE.search(text):
            issues.append(Issue(filePath, number, 0, CODE_STYLE_EMPTY_DOC,
                                f"descrição vazia no Javadoc: {text.strip()}"))
    return issues


def warnJavadocText(comments: list[tuple[int, str]], filePath: str) -> list[Issue]:
    """Sem mapeamento seguro para o Java: avisa, não bloqueia."""
    warnings = []
    for number, text in comments:
        constructor = _PYTHON_CTOR.search(text)
        if constructor:
            warnings.append(Issue(filePath, number, constructor.start(), CODE_STYLE_PYTHON_CTOR,
                                  f"construtor Python em exemplo de doc: {text.strip()}"))
        snake = _SNAKE_CASE.search(text)
        if snake:
            warnings.append(Issue(filePath, number, snake.start(), CODE_STYLE_SNAKE_CASE,
                                  f"snake_case que não é nome conhecido: {text.strip()}"))
    return warnings


def checkTrailingSpace(source: str, filePath: str) -> list[Issue]:
    return [Issue(filePath, number, len(line.rstrip()), CODE_STYLE_TRAILING_SPACE,
                  f"espaço no fim da linha: {line.strip()}")
            for number, line in enumerate(source.split("\n"), start=1) if line != line.rstrip()]


def declarationAfter(cleaned: list[str], start: int) -> str:
    """Texto da declaração que segue o índice `start`: pula anotações e acumula até `{` ou `;`."""
    index = start
    while index < len(cleaned) and (not cleaned[index].strip() or cleaned[index].strip().startswith("@")):
        index += 1
    declaration = ""
    while index < len(cleaned):
        declaration += " " + cleaned[index].strip()
        if "{" in cleaned[index] or ";" in cleaned[index]:
            break
        index += 1
    return declaration


def signatureNames(declaration: str) -> set[str]:
    """Nomes dos parâmetros da assinatura; campo com inicializador e declaração sem `(` não têm nenhum."""
    start, end = declaration.find("("), declaration.rfind(")")
    if start < 0 or end < start or "=" in declaration[:start]:
        return set()
    return {normalise(part).rsplit(" ", 1)[-1] for part in splitTopLevel(declaration[start + 1:end])}


def checkParamNames(source: str, filePath: str) -> list[Issue]:
    lines = source.split("\n")
    cleaned = loadTool("derive-contract").withoutNoise(source)
    issues, index = [], 0
    while index < len(lines):
        if not lines[index].strip().startswith("/**"):
            index += 1
            continue
        end = index
        while end < len(lines) and "*/" not in lines[end]:
            end += 1
        declaration = declarationAfter(cleaned, end + 1)
        names = signatureNames(declaration)
        for number in range(index, min(end, len(lines) - 1) + 1):
            tag = _PARAM_TAG.match(lines[number])
            if tag and tag.group(1) not in names:
                issues.append(Issue(filePath, number + 1, 0, CODE_STYLE_PARAM_ORPHAN,
                                    f"@param {tag.group(1)} não está na assinatura de{declaration}:"
                                    f" {lines[number].strip()}"))
        index = end + 1
    return issues


def checkNumericCasts(source: str, filePath: str) -> list[Issue]:
    cleaned = loadTool("derive-contract").withoutNoise(source)
    return [Issue(filePath, number, 0, CODE_STYLE_NUMERIC_CAST,
                  f"cast direto de numérico no Map, leia como Number: {raw.strip()}")
            for number, (raw, line) in enumerate(zip(source.split("\n"), cleaned), start=1)
            if _NUMERIC_REMOVE_CAST.search(line)]


def matchingParen(text: str, opening: int) -> int:
    depth = 0
    for position in range(opening, len(text)):
        depth += {"(": 1, ")": -1}.get(text[position], 0)
        if depth == 0:
            return position
    return -1


def checkBraces(source: str, filePath: str) -> list[Issue]:
    """Heurística sobre o fonte sem comentário nem string: `if`/`for` cujo `)` fechador não é seguido de `{`."""
    bare = "\n".join(loadTool("derive-contract").withoutNoise(source))
    lines = source.split("\n")
    issues = []
    for match in _BRANCH_KEYWORD.finditer(bare):
        closing = matchingParen(bare, match.end() - 1)
        if closing < 0 or bare[closing + 1:].lstrip().startswith("{"):
            continue
        number = bare.count("\n", 0, match.start()) + 1
        issues.append(Issue(filePath, number, 0, CODE_STYLE_UNBRACED,
                            f"{match.group(0).split()[0].rstrip('(')} sem chaves: {lines[number - 1].strip()}"))
    return issues


def checkStyle(source: str, filePath: str) -> tuple[list[Issue], list[Issue]]:
    """Bloqueios e avisos de estilo do Java gerado: resíduo de Python e convenções que o gate de assinatura não vê."""
    comments = commentLines(source)
    blocking = checkJavadocText(comments, filePath) + checkTrailingSpace(source, filePath)
    blocking += checkParamNames(source, filePath) + checkNumericCasts(source, filePath)
    blocking += checkBraces(source, filePath)
    return (blocking, warnJavadocText(comments, filePath))


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


def checkContract(source: str, contract: dict[str, list[str]], filePath: str,
                  waivers: list[dict] | None = None,
                  language: str | None = None) -> tuple[list[Issue], list[dict], list[dict]]:
    """Gaps bloqueantes, dispensas aplicadas e dispensas que não casaram com nada."""
    code = withoutComments(source)
    collapsed = normalise(code)
    shapes = javaShapes(code) if language == "java" else {}
    pending = list(waivers or [])
    issues, applied = [], []

    for kind in CONTRACT_KINDS:
        for expected in contract[kind]:
            if normalise(expected) in collapsed:
                continue

            if kind in shapes and withoutParameterNames(expected) in shapes[kind]:
                continue

            match = next(
                (entry for entry in pending
                 if entry["kind"] == kind and normalise(entry["value"]) == normalise(expected)),
                None,
            )
            if match:
                pending.remove(match)
                applied.append(match)
                continue

            issues.append(
                Issue(filePath, 0, 0, CODE_CONTRACT_GAP, contractGap(kind, expected, code))
            )

    return (issues, applied, pending)


def nodeSyntax(target: Path) -> tuple[bool, list[Issue]]:
    """`node --check` e o equivalente do javac: o `mvn test-compile` do workflow so cobre Java,
    entao sem isto JS quebrado passava o gate e virava PR."""
    if not shutil.which("node"):
        return (False, [])

    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
    if result.returncode == 0:
        return (True, [])

    detalhe = next((line.strip() for line in result.stderr.splitlines()
                    if line.strip() and not line.startswith(str(target))), "sintaxe inválida")
    return (True, [Issue(str(target), 0, 0, CODE_SYNTAX, detalhe)])


def javacWorks() -> bool:
    if not shutil.which("javac"):
        return False
    probe = subprocess.run(["javac", "-version"], capture_output=True, text=True)
    return probe.returncode == 0


def javacSyntax(target: Path) -> tuple[bool, list[Issue]]:
    if not javacWorks():
        return (False, [])

    output = tempfile.mkdtemp(prefix="assert-generated-")
    try:
        result = subprocess.run(
            ["javac", "-J-Duser.language=en", "-encoding", "UTF-8", *_JAVAC_PARSE_ONLY, "-d", output, str(target)],
            capture_output=True,
            text=True,
        )
    finally:
        shutil.rmtree(output, ignore_errors=True)
    if result.returncode == 0:
        return (True, [])

    issues = []
    for line in result.stderr.splitlines():
        match = _JAVAC_DIAGNOSTIC.match(line)
        if match:
            issues.append(Issue(str(target), int(match.group(1)), 0, CODE_SYNTAX, match.group(2).strip()))
    if not issues:
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        detail = next((line for line in lines if "error" in line), " ".join(lines) or "sem diagnóstico")
        issues.append(Issue(str(target), 0, 0, CODE_SYNTAX, f"javac falhou sem apontar o erro: {detail}"))
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


def reportIssues(issues: list[Issue], level: str = "ERROR") -> None:
    for issue in sorted(issues, key=lambda i: (i.line, i.code)):
        location = f"{issue.file}:{issue.line}" if issue.line else issue.file
        emit(f"[{level}] {location} {issue.code} {issue.message}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica o arquivo gerado contra o contrato do SDK real")
    parser.add_argument("target", help="arquivo gerado a verificar")
    parser.add_argument("--lang", choices=["java", "node"], default=None)
    parser.add_argument("--contract", default=None, help="arquivo .contract explícito")
    parser.add_argument("--substitution", action="store_true",
                        help="o arquivo já existe no SDK alvo: nenhuma dispensa se aplica")
    parser.add_argument("--allow-missing-contract", dest="allowMissingContract", action="store_true",
                        help="aceita a ausência de régua, para recurso que ainda não existe no SDK alvo")
    parser.add_argument("--strict", action="store_true", help="trata gap de contrato como reprovação")
    parser.add_argument("--style", action="store_true",
                        help="Java: reprova resíduo de Python no Javadoc e convenção quebrada; só no caminho de criação")
    parser.add_argument("--role", help="papel do artefato, ex: impl, barrel, types")
    parser.add_argument("--waivers", default=None, help=f"arquivo de dispensas (padrão: {WAIVER_DIR}/<lang>.waivers)")
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

    styleWarnings = []
    if args.style and language == "java":
        styleBlocking, styleWarnings = checkStyle(source, filePath)
        blocking += styleBlocking
        executed.append("estilo")

    if language == "java":
        ran, syntaxIssues = javacSyntax(target)
        if ran:
            executed.append("sintaxe (javac)")
            blocking.extend(syntaxIssues)
        else:
            skipped.append("verificação de sintaxe indisponível: javac ausente ou inoperante")

    if language == "node" and target.suffix == ".js":
        ran, syntaxIssues = nodeSyntax(target)
        if ran:
            executed.append("sintaxe (node)")
            blocking.extend(syntaxIssues)
        else:
            skipped.append("verificação de sintaxe indisponível: node ausente")

    waiverPath = resolveWaivers(language, args.waivers)
    waivers = []
    ignored = []
    if waiverPath:
        try:
            parsed = parseWaivers(waiverPath)
        except ValueError as error:
            emit(f"[ERROR] dispensa inválida: {error}")
            return 2
        waivers = parsed.get((target.stem.lower(), args.role or "main"), [])
        if args.substitution:
            ignored, waivers = waivers, []

    contractPath = resolveContract(target, language, args.contract, args.role)
    # a fixture legada, achada pelo nome, não vale quando a régua foi dispensada: o recurso é novo
    skipLegacyFixture = args.allowMissingContract and not args.contract
    hasContract = bool(contractPath and contractPath.exists()) and not skipLegacyFixture
    gaps, applied, unused = [], [], []
    if hasContract:
        try:
            contract = parseContract(contractPath)
        except ValueError as error:
            emit(f"[ERROR] contrato inválido: {error}")
            return 2
        gaps, applied, unused = checkContract(source, contract, filePath, waivers, language)

    emit(f"[INFO] {target} — linguagem {language}")
    emit(f"[INFO] verificações executadas: {', '.join(executed)}")
    skipped.append("compilação contra o SDK real: fora do alcance desta verificação"
                   " — o gate roda no workflow, sobre o checkout do alvo")
    for reason in skipped:
        emit(f"[WARN] {reason}")
    if not hasContract and args.allowMissingContract:
        emit("[INFO] recurso sem arquivo real no SDK alvo — régua dispensada nesta execução")
    if not hasContract and not args.allowMissingContract:
        emit("[WARN] sem contrato para este recurso — nada a medir contra o SDK real")
    if styleWarnings:
        reportIssues(styleWarnings, level="WARN")
        kinds = Counter(warning.code for warning in styleWarnings)
        emit(f"[WARN] {len(styleWarnings)} aviso(s) de estilo que não bloqueiam"
             f" ({', '.join(f'{code} {count}' for code, count in sorted(kinds.items()))})")
    for entry in applied:
        emit(f"[INFO] dispensado: {entry['kind']} {entry['value']} — {entry['reason']}")
    for entry in ignored:
        emit(f"[WARN] dispensa não vale em substituição: {entry['kind']} {entry['value']}"
             " — o arquivo já existe no SDK alvo e perder o item é regressão")
    for entry in unused:
        emit(f"[WARN] dispensa não utilizada: {entry['kind']} {entry['value']}"
             " — o item deixou de divergir, remova a dispensa")

    if blocking:
        emit("")
        reportIssues(blocking)
        emit("")
        emit(f"[ERROR] {len(blocking)} problema(s) bloqueante(s)")
        return 1

    if not hasContract and args.strict and not args.allowMissingContract:
        emit("")
        emit(f"[ERROR] {CODE_MISSING_CONTRACT} régua ausente para {target.name} — derive o contrato"
             " do SDK real ou declare --allow-missing-contract para recurso genuinamente novo")
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
            emit(f"... e mais {len(gaps) - 5}")

    emit(f"[OK] {target} sem problemas bloqueantes")
    return 0


if __name__ == "__main__":
    sys.exit(main())