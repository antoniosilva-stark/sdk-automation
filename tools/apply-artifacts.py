import os
import sys
import argparse
from pathlib import Path

EXIT_OK = 0
EXIT_REFUSED = 1


def log(text: str) -> None:
    sys.stderr.write(f"{text}\n")


def isSafeRelative(declared: str) -> bool:
    parts = Path(declared).parts
    if not parts or Path(declared).is_absolute():
        return False
    return not any(part in ("..", ".", ".git") for part in parts)


def sourceFile(staging: Path, declared: str) -> Path | None:
    """Arquivo do artefato, so se o caminho inteiro for de diretorios e arquivo comuns."""
    current = staging
    for part in Path(declared).parts:
        current = current / part
        if current.is_symlink() or not current.exists():
            return None
    return current if current.is_file() else None


def prepareDirectory(target: Path, declared: str) -> Path | None:
    """Cria a pasta destino componente a componente sem atravessar link simbolico do checkout alvo."""
    current = target
    for part in Path(declared).parent.parts:
        current = current / part
        if current.is_symlink():
            return None
        if not current.exists():
            current.mkdir()
            continue
        if not current.is_dir():
            return None
    return current


def place(staging: Path, target: Path, declared: str) -> str | None:
    """Motivo da recusa, ou None quando o arquivo foi copiado."""
    if not isSafeRelative(declared):
        return "caminho fora do padrao"

    source = sourceFile(staging, declared)
    if source is None:
        return "ausente no artefato ou link simbolico"

    directory = prepareDirectory(target, declared)
    if directory is None:
        return "o alvo tem link simbolico ou arquivo no lugar de uma pasta"

    destination = directory / Path(declared).name
    if destination.is_symlink() or destination.is_dir():
        return "o alvo tem link simbolico ou pasta no lugar do arquivo"
    if destination.exists():
        destination.unlink()

    # O_NOFOLLOW + O_EXCL: mesmo que algo apareca entre a checagem e a escrita, nao escreve atraves de link
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(source.read_bytes())
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Copia para o alvo so os caminhos declarados do artefato")
    parser.add_argument("--staging", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--paths-file", required=True, help="um caminho declarado por linha")
    args = parser.parse_args()

    declared = [line.strip() for line in Path(args.paths_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not declared:
        log("nenhum caminho declarado.")
        return EXIT_REFUSED

    for path in declared:
        reason = place(Path(args.staging), Path(args.target), path)
        if reason:
            log(f"recusado {path!r}: {reason}.")
            return EXIT_REFUSED
        sys.stdout.write(f"{path}\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
