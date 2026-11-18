import re
from hashlib import sha1

import pytest
import yaml

from conftest import requiresGenerator, requiresJavaSdk
from test_java_field_types import REAL_SOURCES, _generated, _realConventionsText

LEGACY_IMPORT = "import com.starkbank.utils.SubResource;"
CORE_IMPORT = "import com.starkcore.utils.SubResource;"


def _entries() -> list:
    return yaml.safe_load(_realConventionsText())["javaLegacySubResource"]


def _imports(source: str) -> list:
    return re.findall(r"^import [^\n]*SubResource;$", source, re.M)


# 1. a entrada contra o real, nos dois sentidos

@requiresJavaSdk
def test_theEntryIsTheOnlyRealResourceThatImportsTheLegacySubResource():
    legacy = sorted(path.stem for path in REAL_SOURCES.glob("*.java")
                    if LEGACY_IMPORT in path.read_text(encoding="utf-8"))

    assert legacy == _entries() == ["MerchantSession"]


@requiresJavaSdk
def test_theRealMerchantSessionWritesTheCoreSubResourceQualifiedInItsPageLoops():
    source = (REAL_SOURCES / "MerchantSession.java").read_text(encoding="utf-8")

    assert _imports(source) == [LEGACY_IMPORT]
    assert re.findall(r"for \(([\w.]*SubResource) \w+: page\.entities\)", source) == ["com.starkcore.utils.SubResource"] * 2


# 2. o gerado

@requiresGenerator
def test_generatedMerchantSessionImportsTheLegacyAndQualifiesTheCoreInThePageLoops(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "MerchantSession", _realConventionsText())

    assert _imports(source) == [LEGACY_IMPORT]
    assert re.findall(r"for \(([\w.]*SubResource) \w+: page\.entities\)", source) == ["com.starkcore.utils.SubResource"] * 2
    assert "public static class AllowedInstallment extends SubResource {" in source


@requiresGenerator
def test_withoutTheEntryMerchantSessionKeepsTheCoreImportAndTheShortLoops(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "MerchantSession", "javaLegacySubResource:\n")

    assert _imports(source) == [CORE_IMPORT]
    assert re.findall(r"for \(([\w.]*SubResource) \w+: page\.entities\)", source) == ["SubResource"] * 2


@requiresGenerator
def test_aResourceWithoutTheEntryIsUntouched(buildResource, tmpPath):
    source = _generated(buildResource, tmpPath, "MerchantCard", _realConventionsText())

    assert LEGACY_IMPORT not in source
    assert "com.starkcore.utils.SubResource " not in source.replace(CORE_IMPORT, "")


# 3. a borda

def _load(extractSchema, tmpPath, text: str, resource: str = "MerchantSession"):
    conventions = tmpPath / f"{sha1(text.encode()).hexdigest()}.yaml"  # loadConventions memoiza por caminho
    conventions.write_text(text, encoding="utf-8")
    return extractSchema.javaLegacySubResource(resource, conventions)


def test_javaLegacySubResourceIsTrueOnlyForTheListedResource(extractSchema, tmpPath):
    entry = "javaLegacySubResource:\n  - MerchantSession\n"

    assert _load(extractSchema, tmpPath, entry) is True
    assert _load(extractSchema, tmpPath, entry, "MerchantCard") is False
    assert _load(extractSchema, tmpPath, "javaTypes:\n") is False


@pytest.mark.parametrize("text, message", [
    ("javaLegacySubResource:\n  - MerchantSessions\n", "MerchantSessions: javaLegacySubResource cita recurso"),
    ("javaLegacySubResource: MerchantSession\n", "javaLegacySubResource deve ser uma lista de recursos"),
    ("javaLegacySubResource:\n  MerchantSession: true\n", "javaLegacySubResource deve ser uma lista de recursos"),
])
def test_invalidJavaLegacySubResourceDataFailsNamingTheValue(extractSchema, tmpPath, text, message):
    with pytest.raises(ValueError, match=message):
        _load(extractSchema, tmpPath, text)


@requiresGenerator
def test_aListedResourceThatDoesNotImportSubResourceFailsInsteadOfDoingNothing(buildResource, tmpPath):
    with pytest.raises(ValueError, match="Balance: javaLegacySubResource cita um recurso que nao importa SubResource"):
        _generated(buildResource, tmpPath, "Balance", "javaLegacySubResource:\n  - Balance\n")
