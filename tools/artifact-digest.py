import sys
import hashlib
import argparse
from pathlib import Path

EXIT_OK = 0
EXIT_MISMATCH = 1


def digest(root: Path) -> str | None:
    """Resumo de caminho e conteudo de cada arquivo; None se houver link simbolico ou arquivo irregular."""
    total = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            return None
        if path.is_dir():
            continue
        if not path.is_file():
            return None
        relative = path.relative_to(root).as_posix()
        total.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return total.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumo do artefato, para o job com o token conferir o que baixou")
    parser.add_argument("root")
    parser.add_argument("--expect", default=None, help="resumo esperado; sem ele, so imprime")
    args = parser.parse_args()

    result = digest(Path(args.root))
    if result is None:
        sys.stderr.write("artefato com link simbolico ou arquivo irregular.\n")
        return EXIT_MISMATCH
    if args.expect is None:
        sys.stdout.write(f"{result}\n")
        return EXIT_OK
    if result != args.expect:
        sys.stderr.write(f"o artefato baixado nao e o que o build produziu ({result} != {args.expect}).\n")
        return EXIT_MISMATCH
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
