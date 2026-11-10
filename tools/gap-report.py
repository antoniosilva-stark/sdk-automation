import sys
import functools
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
CLASSES_FILE = TOOLS_DIR.parent / "apis/gap-classes.yaml"
CLASSES_SECTION = "gap-classes"
CLASS_KEYS = ("kind", "text", "class")
VALID_CLASSES = ("tem-fonte", "sem-fonte", "muda-api-publica", "bloqueado-por-dependencia")
SEM_CLASSIFICAR = "sem-classificar"


@functools.cache
def loadTool(name: str):
    spec = spec_from_file_location(name.replace("-", "_"), TOOLS_DIR / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classOf(classes: dict, resource: str, kind: str, text: str) -> str:
    return classes.get(resource, {}).get((kind, text), SEM_CLASSIFICAR)


def checkClassEntry(resource: str, index: int, entry) -> tuple[tuple[str, str], str]:
    where = f"{resource}[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: entrada deve ser um mapa {{kind, text, class}}, veio {entry!r}")

    unknown = [key for key in entry if key not in CLASS_KEYS]
    if unknown:
        raise ValueError(f"{where}.{unknown[0]}: chave desconhecida, esperado uma de {list(CLASS_KEYS)}")

    kinds = loadTool("compare-with-real").WAIVER_KINDS
    if entry.get("kind") not in kinds:
        raise ValueError(f"{where}.kind invalido: {entry.get('kind')!r}, esperado um de {list(kinds)}")

    text = entry.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{where}.text invalido: {text!r}, esperado texto nao vazio")

    if entry.get("class") not in VALID_CLASSES:
        raise ValueError(f"{where}.class invalido: {entry.get('class')!r}, esperado um de {list(VALID_CLASSES)}")
    return (entry["kind"], text), entry["class"]


def checkClassEntries(resource: str, entries) -> dict[tuple[str, str], str]:
    if not isinstance(entries, list):
        raise ValueError(f"{resource}: {CLASSES_SECTION} deve ser uma lista de entradas, veio {entries!r}")

    classByGap = {}
    for index, entry in enumerate(entries):
        key, value = checkClassEntry(resource=resource, index=index, entry=entry)
        if key in classByGap:
            raise ValueError(f"{resource}[{index}]: entrada duplicada {key[0]} {key[1]!r}")
        classByGap[key] = value
    return classByGap


def loadClasses(path) -> dict[str, dict[tuple[str, str], str]]:
    path = Path(path)
    extractSchema = loadTool("extract-schema")
    try:
        data = extractSchema.loadFast(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as error:
        raise ValueError(f"{path.name}: {CLASSES_SECTION} ilegivel: {error}") from error

    if not isinstance(data, dict):
        raise ValueError(f"{CLASSES_SECTION}: esperado um mapa recurso -> lista de entradas, veio {data!r}")

    extractSchema.checkKnownResources(CLASSES_SECTION, data)
    return {resource: checkClassEntries(resource=resource, entries=entries) for resource, entries in data.items()}


def main(argv=None) -> int:
    return 0


if __name__ == "__main__":
    sys.exit(main())
