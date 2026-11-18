import re

import pytest
import yaml

from conftest import REPO_ROOT, requiresGenerator
from test_java_field_types import _generated, _outer, _realConventionsText

SCHEMAS = REPO_ROOT / "apis/schemas"

# campos que o sdk-python tem e o sdk-java real nao declara: a fonte e o Python, entao o Java gerado os expoe
PYTHON_ONLY_FIELDS = {
    "Transfer": ["displayDescription"],
    "MerchantPurchase": ["cardExpiration", "cardNumber", "cardSecurityCode"],
}


def _schemaProperties(resource: str) -> list:
    schema = yaml.safe_load((SCHEMAS / f"{resource.lower()}.yaml").read_text(encoding="utf-8"))
    return list(schema["components"]["schemas"][resource]["properties"])


def _declaredFields(source: str) -> set:
    return set(re.findall(r"^    public [\w<>\[\], .]+? (\w+);", _outer(source), re.M))


def _allArgsParameters(source: str, resource: str) -> set:
    signatures = re.findall(rf"^    public {resource}\(([^)]*)\)", _outer(source), re.M)
    longest = max(signatures, key=lambda text: text.count(","))
    return {part.strip().split()[-1] for part in longest.split(",")}


@pytest.mark.parametrize("resource", sorted(PYTHON_ONLY_FIELDS))
def test_theSchemaOfThePythonCarriesTheField(resource):
    assert set(PYTHON_ONLY_FIELDS[resource]) <= set(_schemaProperties(resource))


def test_noneOfThemIsOmittedByConvention():
    omitted = yaml.safe_load(_realConventionsText()).get("javaOmittedFields") or {}

    assert not set(PYTHON_ONLY_FIELDS) & set(omitted)


@requiresGenerator
@pytest.mark.parametrize("resource", sorted(PYTHON_ONLY_FIELDS))
def test_generatedClassDeclaresEveryFieldOfThePython(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())

    assert set(_schemaProperties(resource)) <= _declaredFields(source) | {"id"}


@requiresGenerator
@pytest.mark.parametrize("resource", sorted(PYTHON_ONLY_FIELDS))
def test_generatedAllArgsConstructorCarriesTheFieldsOfThePython(buildResource, tmpPath, resource):
    source = _generated(buildResource, tmpPath, resource, _realConventionsText())

    assert set(PYTHON_ONLY_FIELDS[resource]) <= _allArgsParameters(source, resource)
