import pytest
from pathlib import Path

from conftest import JAVA_SDK, NODE_SDK, REPO_ROOT, requiresJavaSdk, requiresNodeSdk, runTool

FORGED_JAVA = '''package com.starkbank;

import com.starkbank.utils.Rest;
import com.starkcore.utils.SubResource;

public final class Widget extends Resource {
    static ClassData data = new ClassData(Widget.class, "Widget");

    public long amount;
    public String[] tags;

    public Widget(long amount, String status,
                  String[] tags, String id) {
        super(id);
    }

    public Widget() {
        super(null);
    }

    public static Widget get(String id) throws Exception {
        return Widget.get(id, null);
    }

    public final static class Page {
        public List<Widget> widgets;
    }
}
'''

FORGED_NODE = '''const rest = require('../utils/rest.js');
const check = require('starkcore').check;

class Widget extends Resource {
}

exports.Widget = Widget;

exports.get = async function (id, {user} = {}) {
    return rest.getId({resource, id, user});
};

exports.page = async function ({cursor, limit, user} = {}) {
    return rest.getPage({resource, cursor, limit, user});
};
'''


def _forge(tmpPath: Path, relative: str, body: str) -> Path:
    target = tmpPath / "repo" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return tmpPath / "repo"


def _derive(resource: str, language: str, role: str, root: Path) -> list[str]:
    code, out = runTool("derive-contract.py", resource, "--lang", language, "--role", role, "--from", str(root))
    assert code == 0, out
    return [line for line in out.splitlines() if line and not line.startswith("#")]


def test_unknownLanguageReturnsTwoNamingTheAvailableOnes(tmpPath):
    code, out = runTool("derive-contract.py", "Widget", "--lang", "cobol", "--from", str(tmpPath))

    assert code == 2
    assert "java" in out and "node" in out


def test_missingSourceFileReturnsTwo(tmpPath):
    code, out = runTool("derive-contract.py", "Widget", "--lang", "java", "--role", "main", "--from", str(tmpPath))

    assert code == 2
    assert "[ERROR]" in out


def test_javaExtractorCoversEveryKind(tmpPath):
    root = _forge(tmpPath, "src/main/java/com/starkbank/Widget.java", FORGED_JAVA)
    lines = _derive("Widget", "java", "main", root)

    assert "declaration public final class Widget extends Resource" in lines
    assert 'field       static ClassData data = new ClassData(Widget.class, "Widget");' in lines
    assert "field public long amount;" in lines
    assert "field public String[] tags;" in lines
    assert "constructor public Widget()" in lines
    assert "signature public static Widget get(String id)" in lines
    assert "inner public final static class Page" in lines
    assert "require import com.starkcore.utils.SubResource;" in lines


def test_javaMultiLineConstructorIsJoined(tmpPath):
    """O construtor posicional do Invoice real quebra em 4 linhas; contrato e uma linha."""
    root = _forge(tmpPath, "src/main/java/com/starkbank/Widget.java", FORGED_JAVA)
    lines = _derive("Widget", "java", "main", root)

    joined = [line for line in lines if line.startswith("constructor") and "long amount" in line]
    assert joined == ["constructor public Widget(long amount, String status, String[] tags, String id)"]


def test_joinDoesNotLeaveSpaceBeforeTheClosingParen(tmpPath):
    """O construtor do Invoice real fecha o parentese em linha propria."""
    body = FORGED_JAVA.replace("String[] tags, String id) {", "String[] tags, String id\n    ) {")
    root = _forge(tmpPath, "src/main/java/com/starkbank/Widget.java", body)
    lines = _derive("Widget", "java", "main", root)

    assert not any(" )" in line for line in lines)


def test_nodeExtractorCoversEveryKind(tmpPath):
    root = _forge(tmpPath, "sdk/widget/widget.js", FORGED_NODE)
    lines = _derive("Widget", "node", "impl", root)

    assert "require const rest = require('../utils/rest.js')" in lines
    assert "require const check = require('starkcore').check" in lines
    assert "declaration class Widget extends Resource" in lines
    assert "export exports.Widget = Widget" in lines
    assert "export exports.get = async function (id, {user} = {})" in lines


def test_roleComesFromTheLayoutNotFromAGuess(tmpPath):
    """3 papeis Node, 3 arquivos distintos: o caminho sai do LAYOUTS do place-generated."""
    root = tmpPath / "repo"
    for relative, body in (
        ("sdk/widget/widget.js", FORGED_NODE),
        ("sdk/widget/index.js", "const widget = require('./widget.js');\nexports.get = widget.get;\n"),
        ("types/widget/widget.d.ts", "declare module 'starkbank' {\n    export namespace widget {\n    }\n}\n"),
    ):
        _forge(tmpPath, relative, body)

    impl = _derive("Widget", "node", "impl", root)
    barrel = _derive("Widget", "node", "barrel", root)
    types = _derive("Widget", "node", "types", root)

    assert "export exports.get = async function (id, {user} = {})" in impl
    assert "export exports.get = widget.get" in barrel
    assert "namespace export namespace widget" in types


@requiresJavaSdk
def test_derivedJavaCoversTheHandWrittenContract():
    """Criterio da fase: o derivado contem tudo que o contrato a mao exige hoje.

    Se nao contiver, o extrator esta incompleto — e o contrato a mao e a unica
    referencia que temos do que importa.
    """
    code, out = runTool("derive-contract.py", "Invoice", "--lang", "java", "--role", "main")
    assert code == 0, out

    derived = {line for line in out.splitlines() if line and not line.startswith("#")}
    contract = (REPO_ROOT / "tests/contract/java-invoice.contract").read_text(encoding="utf-8")

    required = [
        " ".join(line.split()) for line in contract.splitlines()
        if line.strip() and not line.strip().startswith("#") and not line.startswith("todo ")
    ]
    normalised = [" ".join(line.split()) for line in derived]
    missing = [line for line in required if not any(line in entry for entry in normalised)]

    assert missing == [], f"extrator incompleto: {missing[:5]}"


@requiresNodeSdk
def test_derivedNodeCoversTheHandWrittenContract():
    code, out = runTool("derive-contract.py", "Transfer", "--lang", "node", "--role", "impl")
    assert code == 0, out

    derived = {" ".join(line.split()) for line in out.splitlines() if line and not line.startswith("#")}
    contract = (REPO_ROOT / "tests/contract/node-transfer-impl.contract").read_text(encoding="utf-8")

    required = [
        " ".join(line.split()) for line in contract.splitlines()
        if line.strip() and not line.strip().startswith("#") and not line.startswith("todo ")
    ]
    missing = [line for line in required if not any(line in entry for entry in derived)]

    assert missing == [], f"extrator incompleto: {missing[:5]}"
