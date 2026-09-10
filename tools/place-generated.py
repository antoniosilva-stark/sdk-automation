import sys
import shutil
import argparse
from pathlib import Path

LAYOUTS = {
    "java": [
        ("main", "src/main/java/com/starkbank/{Resource}.java", "src/main/java/com/starkbank/{Resource}.java"),
    ],
    "node": [
        ("impl", "src/model/{Resource}.js", "sdk/{resource}/{resource}.js"),
        ("barrel", "src/model/{Resource}.js", "sdk/{resource}/index.js"),
        ("types", "model/{resource}.ts", "types/{resource}/{resource}.d.ts"),
    ],
}

TARGETS = {
    "java": "sdk-java",
    "node": "sdk-node",
}


def emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def varName(resource: str) -> str:
    return resource[0].lower() + resource[1:]


def resolvePairs(language: str, resource: str) -> list[tuple[str, str]]:
    if language not in LAYOUTS:
        raise KeyError(language)
    fields = {"Resource": resource, "resource": varName(resource)}
    return [
        (f"{role}/{source.format(**fields)}", target.format(**fields))
        for role, source, target in LAYOUTS[language]
    ]


def missingSources(generatedDir: Path, pairs: list[tuple[str, str]]) -> list[str]:
    return [source for source, _ in pairs if not (generatedDir / source).is_file()]


def placeFiles(generatedDir: Path, targetDir: Path, pairs: list[tuple[str, str]]) -> list[str]:
    placed = []
    for source, target in pairs:
        destination = targetDir / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(generatedDir / source, destination)
        placed.append(target)
    return placed


def main() -> int:
    parser = argparse.ArgumentParser(description="Move a saída do gerador para o layout do SDK alvo")
    parser.add_argument("resource", nargs="?", help="nome do recurso, ex: SplitProfile")
    parser.add_argument("--lang", required=True, help=f"linguagem ({', '.join(sorted(LAYOUTS))})")
    parser.add_argument("--from", dest="generated", help="raiz da saída do gerador, com um subdiretório por papel")
    parser.add_argument("--to", dest="target", help="raiz do repositório do SDK alvo")
    parser.add_argument("--list", action="store_true", help="imprime os pares origem -> destino sem copiar")
    parser.add_argument("--repo", action="store_true", help="imprime o repositório alvo da linguagem e encerra")
    args = parser.parse_args()

    if args.repo:
        if args.lang not in TARGETS:
            emit(f"[ERROR] linguagem sem repositório alvo mapeado: {args.lang}")
            emit(f"[INFO] mapeadas: {', '.join(sorted(TARGETS))}")
            return 2
        emit(TARGETS[args.lang])
        return 0

    if not args.resource:
        emit("[ERROR] resource é obrigatório fora do modo --repo")
        return 2

    try:
        pairs = resolvePairs(args.lang, args.resource)
    except KeyError:
        emit(f"[ERROR] linguagem sem layout mapeado: {args.lang}")
        emit(f"[INFO] mapeadas: {', '.join(sorted(LAYOUTS))}")
        return 2

    if args.list:
        for source, target in pairs:
            emit(f"{source} -> {target}")
        return 0

    if not args.generated or not args.target:
        emit("[ERROR] --from e --to são obrigatórios fora do modo --list")
        return 2

    generatedDir = Path(args.generated)
    targetDir = Path(args.target)
    for label, path in (("saída do gerador", generatedDir), ("repositório alvo", targetDir)):
        if not path.is_dir():
            emit(f"[ERROR] {label} não é um diretório: {path}")
            return 2

    missing = missingSources(generatedDir, pairs)
    if missing:
        emit(f"[ERROR] {len(missing)} arquivo(s) esperado(s) ausente(s) na saída do gerador:")
        for source in missing:
            emit(f"  {source}")
        return 1

    placed = placeFiles(generatedDir, targetDir, pairs)
    for target in placed:
        emit(f"[OK] {target}")
    emit(f"[OK] {len(placed)} arquivo(s) posicionado(s) em {targetDir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
