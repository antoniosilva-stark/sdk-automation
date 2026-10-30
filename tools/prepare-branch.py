import sys
import argparse
import subprocess
from pathlib import Path

EXIT_OK = 0
EXIT_UNREACHABLE = 2
EXIT_FOREIGN_COMMITS = 3
EXIT_ALREADY_EXISTS = 4

BOT_EMAIL = "sdk-automation@starkbank.com"

HISTORY_DEPTH = "100"


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def log(text: str) -> None:
    sys.stderr.write(f"{text}\n")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def remoteHasBranch(remote: str, branch: str) -> bool | None:
    """True se a branch existe, False se o remoto responde que nao, None se nao foi possivel perguntar.

    `ls-remote --exit-code` devolve 2 so para "ausente"; qualquer outro codigo e falha de acesso."""
    result = git("ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}")
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    return None


def fetchTip(remote: str, branch: str) -> bool:
    refspec = f"refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    return git("fetch", "--depth", HISTORY_DEPTH, remote, refspec).returncode == 0


def revision(ref: str) -> str | None:
    result = git("rev-parse", ref)
    return result.stdout.strip() if result.returncode == 0 else None


def checkout(branch: str, ref: str) -> bool:
    result = git("checkout", "-B", branch, ref)
    if result.returncode != 0:
        log(f"git checkout -B {branch} {ref} falhou: {result.stderr.strip()}")
    return result.returncode == 0


def deepen(remote: str, base: str, branch: str) -> bool:
    """Completa o historico dos dois ramos; False quando o clone ja era completo."""
    if git("rev-parse", "--is-shallow-repository").stdout.strip() != "true":
        return False
    git("fetch", "--unshallow", "--quiet", remote,
        f"refs/heads/{base}:refs/remotes/{remote}/{base}",
        f"refs/heads/{branch}:refs/remotes/{remote}/{branch}")
    return True


def forkPointKnown(remote: str, base: str, branch: str) -> bool:
    """O clone e raso: sem o ponto de partida, `base..branch` lista commits da propria base como se fossem da branch."""
    baseRef, branchRef = f"{remote}/{base}", f"{remote}/{branch}"
    if git("merge-base", baseRef, branchRef).returncode == 0:
        return True
    if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        # refspec explicito: o clone raso e de uma branch so e `--unshallow` sem ele nao traz mais nada
        git("fetch", "--unshallow", "--quiet", remote,
            f"refs/heads/{base}:refs/remotes/{remote}/{base}",
            f"refs/heads/{branch}:refs/remotes/{remote}/{branch}")
    return git("merge-base", baseRef, branchRef).returncode == 0


def strayPaths(baseRef: str, branchRef: str, allowedPaths: set[str] | None) -> list[str]:
    """Arquivos que a branch muda alem dos que esta execucao gera: sem isso a PR do bot carregaria commit alheio."""
    if allowedPaths is None:
        return []
    # --no-renames: com deteccao de rename, o arquivo apagado pelo rename some da lista
    result = git("diff", "--no-renames", "--name-only", "-z", f"{baseRef}...{branchRef}")
    if result.returncode != 0:
        return [f"(git diff falhou: {result.stderr.strip()})"]
    return sorted({name for name in result.stdout.split("\0") if name} - allowedPaths)


def foreignAuthors(baseRef: str, branchRef: str, botEmail: str) -> list[str]:
    """Autores, que nao o bot, dos commits da branch que a regeneracao descartaria."""
    result = git("log", "--format=%ae", f"{baseRef}..{branchRef}")
    if result.returncode != 0:
        return [f"(git log falhou: {result.stderr.strip()})"]
    return sorted({email for email in result.stdout.split() if email != botEmail})


def existingPaths(baseRef: str, allowedPaths: set[str]) -> list[str] | None:
    """Caminhos declarados que ja estao na arvore da base; None quando nao foi possivel listar a arvore.

    `ls-tree` sai 0 tambem para caminho ausente, entao so erro de verdade vira None (fail-closed)."""
    result = git("ls-tree", "--full-tree", "--name-only", "-z", baseRef, "--", *sorted(allowedPaths))
    if result.returncode != 0:
        return None
    return sorted({name for name in result.stdout.split("\0") if name} & allowedPaths)


def prepare(remote: str, branch: str, base: str, botEmail: str, allowedPaths: set[str] | None = None,
            createOnly: bool = False) -> int:
    present = remoteHasBranch(remote, branch)
    if present is None:
        log(f"nao foi possivel consultar o remoto {remote}.")
        return EXIT_UNREACHABLE

    if not fetchTip(remote, base):
        log(f"nao foi possivel atualizar a base {base} de {remote}.")
        return EXIT_UNREACHABLE
    baseRef = f"{remote}/{base}"

    if createOnly:
        # antes de qualquer checkout: a branch reaproveitada nao pode mascarar um recurso criado a mao na base
        existing = existingPaths(baseRef, allowedPaths)
        if existing is None:
            log(f"nao foi possivel listar a arvore de {baseRef}.")
            return EXIT_UNREACHABLE
        if existing:
            log(f"{', '.join(existing)} ja existe em {baseRef}; a politica e create-only, nada foi alterado.")
            return EXIT_ALREADY_EXISTS

    if not present:
        if not checkout(branch, baseRef):
            return EXIT_UNREACHABLE
        log(f"Branch {branch} e nova, a partir de {baseRef}.")
        emit("exists=false")
        emit("old=")
        return EXIT_OK

    if not fetchTip(remote, branch):
        log(f"nao foi possivel buscar {branch} em {remote}.")
        return EXIT_UNREACHABLE
    branchRef = f"{remote}/{branch}"

    contains = git("merge-base", "--is-ancestor", baseRef, branchRef).returncode
    if contains == 1 and deepen(remote, base, branch):
        # historico raso pode esconder a base num ramo longo: so vale "atras" com o historico completo
        contains = git("merge-base", "--is-ancestor", baseRef, branchRef).returncode
    if contains not in (0, 1):
        log(f"nao foi possivel comparar {branchRef} com {baseRef}.")
        return EXIT_UNREACHABLE

    if contains == 0:
        stray = strayPaths(baseRef, branchRef, allowedPaths)
        if stray:
            log(f"{branch} muda arquivos fora dos declarados: {', '.join(stray)}. Integre ou remova a branch.")
            return EXIT_FOREIGN_COMMITS
        if not checkout(branch, branchRef):
            return EXIT_UNREACHABLE
        log(f"Branch {branch} ja existe e contem {baseRef} — comparando contra o que ja foi entregue.")
        emit("exists=true")
        emit("old=")
        return EXIT_OK

    # a base andou: o gerado e deterministico, entao regerar sobre ela vale mais que mesclar,
    # mas so quando nada de quem revisa seria descartado
    if not forkPointKnown(remote, base, branch):
        log(f"nao foi possivel achar o ponto em que {branch} saiu de {baseRef}, mesmo com o historico completo.")
        return EXIT_UNREACHABLE
    foreign = foreignAuthors(baseRef, branchRef, botEmail)
    if foreign:
        log(f"{branch} tem commits de {', '.join(foreign)}; regerar os descartaria. Integre ou remova a branch.")
        return EXIT_FOREIGN_COMMITS

    previous = revision(branchRef)
    if previous is None:
        log(f"nao foi possivel resolver {branchRef}.")
        return EXIT_UNREACHABLE
    if not checkout(branch, baseRef):
        return EXIT_UNREACHABLE
    log(f"Branch {branch} esta atras de {baseRef} — sera regerada sobre a base atual.")
    emit("exists=false")
    emit(f"old={previous}")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description="Posiciona a branch de trabalho do alvo sobre a base atual")
    parser.add_argument("branch", help="branch de trabalho, ex: sdk-automation/split-profile")
    parser.add_argument("base", help="branch default do alvo, ex: master")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--allowed-paths-file", default=None,
                        help="um caminho por linha; a branch reaproveitada so pode mudar estes")
    parser.add_argument("--bot-email", default=BOT_EMAIL, help="autor dos commits que podem ser regerados")
    parser.add_argument("--create-only", action="store_true",
                        help="falha se algum caminho declarado ja existe na base; exige --allowed-paths-file")
    args = parser.parse_args()
    if args.create_only and not args.allowed_paths_file:
        parser.error("--create-only exige --allowed-paths-file")
    allowed = None
    if args.allowed_paths_file:
        lines = Path(args.allowed_paths_file).read_text(encoding="utf-8").splitlines()
        allowed = {line.strip() for line in lines if line.strip()}
    return prepare(args.remote, args.branch, args.base, args.bot_email, allowed, args.create_only)


if __name__ == "__main__":
    sys.exit(main())
