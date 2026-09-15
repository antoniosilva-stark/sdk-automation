import yaml
import pytest

from conftest import runTool


def _paths(*names: str) -> dict:
    return {"paths": {name: {} for name in names}}


def test_removedPathIsDetected(detector):
    changes = detector.detectBreakingChanges(_paths("/invoice"), _paths("/invoice", "/transfer"))
    assert len(changes) == 1
    assert changes[0] == {"type": "removed_path", "severity": "MAJOR", "path": "/transfer"}


def test_multipleRemovedPathsAreOrdered(detector):
    previous = _paths("/invoice", "/transfer", "/balance")
    changes = detector.detectBreakingChanges(_paths("/invoice"), previous)
    assert [c["path"] for c in changes] == ["/balance", "/transfer"]


def test_currentWithoutPathsRemovesEverything(detector):
    changes = detector.detectBreakingChanges({}, _paths("/invoice", "/transfer"))
    assert len(changes) == 2
    

def test_withoutPreviousNothingIsReported(detector):
    assert detector.detectBreakingChanges(_paths("/invoice"), None) == []


def test_identicalSpecReportsNothing(detector):
    current = _paths("/invoice", "/transfer")
    assert detector.detectBreakingChanges(current, current) == []


def test_addedPathIsNotBreaking(detector):
    changes = detector.detectBreakingChanges(_paths("/invoice", "/novo"), _paths("/invoice"))
    assert changes == []


# --- contrato de saida ---

def test_cleanSpecReturnsZero():
    code, out = runTool("breaking-change-detector.py")
    assert code == 0
    assert "[OK]" in out


def test_invalidYamlPropagates(detector, tmpPath, monkeypatch):
    """YAML malformado deve levantar, não ser lido como "nada para comparar".

    O código anterior usava `except Exception` e devolvia None nesse caso, o que
    fazia o detector passar verde com a spec quebrada.
    """
    broken = tmpPath / "broken.yaml"
    broken.write_text("paths: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(detector, "SPEC_FILE", str(broken))

    with pytest.raises(yaml.YAMLError):
        detector.loadSpec()