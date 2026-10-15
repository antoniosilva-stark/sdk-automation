import sys
import argparse
import subprocess

EXIT_OK = 0
EXIT_UNREACHABLE = 2

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
    result = git("ls-remote", "--exit-code", "--heads", remote, branch)
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    return None


def fetchTip(remote: str, branch: str) -> bool:
    refspec = f"refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    return git("fetch", "--depth", HISTORY_DEPTH, remote, refspec).returncode == 0


def revision(ref: str) -> str:
    return git("rev-parse", ref).stdout.strip()


def prepare(remote: str, branch: str, base: str) -> int:
    present = remoteHasBranch(remote, branch)
    if present is None:
        log(f"nao foi possivel consultar o remoto {remote}.")
        return EXIT_UNREACHABLE

    if not fetchTip(remote, base):
        log(f"nao foi possivel atualizar a base {base} de {remote}.")
        return EXIT_UNREACHABLE
    baseRef = f"{remote}/{base}"

    if not present:
        git("checkout", "-B", branch, baseRef)
        log(f"Branch {branch} e nova, a partir de {baseRef}.")
        emit("exists=false")
        emit("old=")
        return EXIT_OK

    if not fetchTip(remote, branch):
        log(f"nao foi possivel buscar {branch} em {remote}.")
        return EXIT_UNREACHABLE
    branchRef = f"{remote}/{branch}"

    if git("merge-base", "--is-ancestor", baseRef, branchRef).returncode == 0:
        git("checkout", "-B", branch, branchRef)
        log(f"Branch {branch} ja existe e contem {baseRef} — comparando contra o que ja foi entregue.")
        emit("exists=true")
        emit("old=")
        return EXIT_OK

    # a base andou: o gerado e deterministico, entao regerar sobre ela vale mais que mesclar
    previous = revision(branchRef)
    git("checkout", "-B", branch, baseRef)
    log(f"Branch {branch} esta atras de {baseRef} — sera regerada sobre a base atual.")
    emit("exists=false")
    emit(f"old={previous}")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description="Posiciona a branch de trabalho do alvo sobre a base atual")
    parser.add_argument("branch", help="branch de trabalho, ex: sdk-automation/split-profile")
    parser.add_argument("base", help="branch default do alvo, ex: master")
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()
    return prepare(args.remote, args.branch, args.base)


if __name__ == "__main__":
    sys.exit(main())
