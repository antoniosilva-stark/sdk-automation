import sys
import json
import argparse
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

TOOLS_DIR = Path(__file__).resolve().parent
EXIT_USAGE = 2


def loadReplicate():
    spec = spec_from_file_location("replicate_all", TOOLS_DIR / "replicate-all.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fail(message: str) -> int:
    sys.stderr.write(f"[ERROR] {message}\n")
    return EXIT_USAGE


def splitNames(text: str) -> list[str]:
    return [name.strip() for name in text.split(",") if name.strip()]


def parseArguments(argv: list[str] | None):
    parser = argparse.ArgumentParser(description="Ordem de dependência dos recursos de um lote do SDK Sync")
    parser.add_argument("--lang", required=True, help="linguagem do SDK alvo")
    parser.add_argument("--resources", default="", help="lista separada por vírgula (padrão: todos)")
    parser.add_argument("--exclude", default="", help="lista separada por vírgula a tirar do lote")
    return parser.parse_args(argv)


def orderFromReference(language: str):
    replicate = loadReplicate()
    source = replicate.loadTool("derive-contract").referenceRepo(language, None)

    def order(wanted: list[str]) -> tuple[list[str], list[str]]:
        known = replicate.loadTool("parity-report").schemaResources()
        dependencies = replicate.dependenciesOf(source, known)
        return replicate.topologicalOrder(dependencies=dependencies, requested=wanted)

    return order


def knownResources() -> list[str]:
    return loadReplicate().loadTool("parity-report").schemaResources()


def main(argv: list[str] | None = None, order=None, known: list[str] | None = None) -> int:
    args = parseArguments(argv)
    known = known if known is not None else knownResources()
    requested = splitNames(args.resources)
    excluded = splitNames(args.exclude)
    # os nomes vêm de um input digitado: só entram os que o schema versionado conhece
    unknown = [name for name in [*requested, *excluded] if name not in known]
    if unknown:
        return fail(f"recurso desconhecido: {', '.join(unknown)}")
    wanted = [name for name in dict.fromkeys(requested or known) if name not in excluded]
    if not wanted:
        return fail("o lote ficou vazio depois de tirar os excluídos")
    ordered, stuck = (order or orderFromReference(args.lang))(wanted)
    if stuck:
        return fail(f"ciclo de dependência entre recursos: {', '.join(stuck)}")
    sys.stdout.write(f"{json.dumps(ordered)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
