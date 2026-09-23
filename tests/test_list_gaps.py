import json
import pytest
from pathlib import Path

from conftest import requiresJavaSdk, requiresPythonSdk, runTool


def _python(root: Path, modules: dict[str, list[tuple[str, str]]]) -> Path:
    for directory, entries in modules.items():
        target = root / "starkbank" / directory
        target.mkdir(parents=True)
        for module, className in entries:
            base = "Resource" if className else "SubResource"
            body = f"class {className}({base}):\n    pass\n" if className else "def get(id):\n    pass\n"
            (target / f"__{module}.py").write_text(body, encoding="utf-8")
    return root


def _java(root: Path, classes: list[str]) -> Path:
    target = root / "src/main/java/com/starkbank"
    target.mkdir(parents=True)
    for className in classes:
        (target / f"{className}.java").write_text(f"public class {className} {{}}\n", encoding="utf-8")
    return target


def _run(tmpPath: Path, modules: dict, javaClasses: list[str], *extra: str):
    python = _python(tmpPath / "py", modules)
    java = _java(tmpPath / "java", javaClasses)
    return runTool("list-gaps.py", "--python", str(python), "--java", str(java), *extra)


def _json(tmpPath: Path, modules: dict, javaClasses: list[str]) -> dict:
    code, out = _run(tmpPath, modules, javaClasses, "--json")
    assert code == 0, out
    return json.loads(out)


def test_resourceMissingInJavaIsAGap(tmpPath):
    report = _json(tmpPath, {"widget": [("widget", "Widget")]}, [])
    assert report["gaps"] == ["Widget"]


def test_resourcePresentInBothIsNotAGap(tmpPath):
    report = _json(tmpPath, {"widget": [("widget", "Widget")]}, ["Widget"])
    assert report["gaps"] == []


def test_authenticationClassIsNeitherGapNorExtra(tmpPath):
    report = _json(tmpPath, {"widget": [("widget", "Widget")]}, ["Widget", "User", "Project", "Settings"])
    assert report["extras"] == []


def test_subObjectThatIsInnerClassInJavaIsNotAGap(tmpPath):
    modules = {"invoice": [("invoice", "Invoice"), ("payment", "Payment")]}
    report = _json(tmpPath, modules, ["Invoice"])

    assert report["gaps"] == []
    assert "Payment" in report["subObjects"]


def test_secondaryModuleWithOwnFileCountsAsPresent(tmpPath):
    modules = {"paymentpreview": [("paymentpreview", "PaymentPreview"), ("boletopreview", "BoletoPreview")]}
    report = _json(tmpPath, modules, ["PaymentPreview", "BoletoPreview"])

    assert report["gaps"] == []
    assert report["subObjects"] == []


def test_directoryWithoutClassIsAnnouncedNotSilenced(tmpPath):
    report = _json(tmpPath, {"request": [("request", "")]}, ["Request"])

    assert report["gaps"] == []
    assert "request" in report["withoutClass"]


def test_javaOnlyExtraIsAnnouncedWithoutBecomingWork(tmpPath):
    report = _json(tmpPath, {"widget": [("widget", "Widget")]}, ["Widget", "VerifiedAccount"])

    assert report["gaps"] == []
    assert report["extras"] == ["VerifiedAccount"]


def test_humanOutputListsTheGap(tmpPath):
    code, out = _run(tmpPath, {"widget": [("widget", "Widget")]}, [])
    assert code == 0
    assert "Widget" in out
    assert "[INFO]" in out


def test_invalidRootReturnsTwo(tmpPath):
    code, out = runTool("list-gaps.py", "--python", str(tmpPath / "nao-existe"), "--java", str(tmpPath))
    assert code == 2
    assert "[ERROR]" in out


@requiresPythonSdk
@requiresJavaSdk
def test_realGapIsOnlySplitProfile():
    code, out = runTool("list-gaps.py", "--json")
    assert code == 0, out

    report = json.loads(out)
    assert report["gaps"] == ["SplitProfile"], report["gaps"]
