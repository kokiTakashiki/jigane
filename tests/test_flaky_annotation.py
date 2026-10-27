import pytest

from jigane.flaky.catalog import load_catalog
from jigane.flaky.detector import detect_source
from jigane.gates.line_scope import AllLines
from jigane.languages.base import LanguageAdapter
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.java import JavaAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter

CATALOG = load_catalog()
OK = "jigane: allow-flaky FLK-ASYNC-WAIT -- polled by design"
NO_REASON = "jigane: allow-flaky FLK-ASYNC-WAIT --"
UNKNOWN = "jigane: allow-flaky FLK-NOPE -- x"

# 注記の置き場所ごとに、コメントの書式の雛形を持つ。{note} に注記の本文が入る
JAVA_LIKE = {
    "line": "// {note}\n",
    "block": "/* {note} */\n",
    "block_multiline_first": "/* {note}\n * 補足 */\n",
    "block_multiline_second": "/* 補足\n * {note} */\n",
    "block_multiline_middle": "/* 補足\n * {note}\n * 補足 */\n",
}

CASES: list[tuple[LanguageAdapter, str, dict[str, str], str]] = [
    (JavaAdapter(), "src/test/java/FooTest.java", JAVA_LIKE, "Thread.sleep(500);"),
    (CSharpAdapter(), "tests/FooTests.cs", JAVA_LIKE, "Thread.Sleep(500);"),
    (SwiftAdapter(), "Tests/FooTests.swift", JAVA_LIKE, "Thread.sleep(forTimeInterval: 1)"),
    (PythonAdapter(), "tests/test_x.py", {"line": "# {note}\n"}, "time.sleep(1)"),
]


def _program(adapter: LanguageAdapter, comment: str, call: str) -> str:
    if adapter.name == "java":
        return (
            "class FooTest {\n    @Test\n    void waits() throws Exception {\n"
            f"{comment}{call}\n    }}\n}}\n"
        )
    if adapter.name == "csharp":
        return (
            "public class FooTests\n{\n    [Fact]\n    public void Waits()\n    {\n"
            f"{comment}{call}\n    }}\n}}\n"
        )
    if adapter.name == "swift":
        return (
            "import XCTest\n\nfinal class WaitTests: XCTestCase {\n"
            f"    func testSleeps() {{\n{comment}{call}\n    }}\n}}\n"
        )
    return f"import time\n\n\ndef test_a():\n{comment}    {call}\n"


def _found(adapter: LanguageAdapter, path: str, comment: str, call: str) -> list[str]:
    source = _program(adapter, comment, call).encode()
    return [d.item_id for d in detect_source(path, source, adapter, CATALOG, AllLines())]


def _params() -> list[tuple[LanguageAdapter, str, str, str, str]]:
    return [
        (adapter, path, name, template, call)
        for adapter, path, templates, call in CASES
        for name, template in templates.items()
    ]


@pytest.mark.parametrize(
    ("adapter", "path", "_name", "template", "call"),
    _params(),
    ids=lambda v: v if isinstance(v, str) and len(v) < 30 else None,
)
def test_annotation_with_reason_suppresses(
    adapter: LanguageAdapter, path: str, _name: str, template: str, call: str
) -> None:
    comment = template.format(note=OK)
    assert _found(adapter, path, comment, call) == []


@pytest.mark.parametrize(
    ("adapter", "path", "_name", "template", "call"),
    _params(),
    ids=lambda v: v if isinstance(v, str) and len(v) < 30 else None,
)
def test_annotation_without_reason_is_violation(
    adapter: LanguageAdapter, path: str, _name: str, template: str, call: str
) -> None:
    comment = template.format(note=NO_REASON)
    assert sorted(_found(adapter, path, comment, call)) == ["FLK-ANNOTATION", "FLK-ASYNC-WAIT"]


@pytest.mark.parametrize(
    ("adapter", "path", "_name", "template", "call"),
    _params(),
    ids=lambda v: v if isinstance(v, str) and len(v) < 30 else None,
)
def test_annotation_unknown_id_is_violation(
    adapter: LanguageAdapter, path: str, _name: str, template: str, call: str
) -> None:
    comment = template.format(note=UNKNOWN)
    assert sorted(_found(adapter, path, comment, call)) == ["FLK-ANNOTATION", "FLK-ASYNC-WAIT"]


@pytest.mark.parametrize("separator", ["\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029", "\r"])
def test_annotation_line_follows_newline_only(separator: str) -> None:
    # tree-sitter の row は \n だけを数える。splitlines が分ける他の文字で行番号がずれてはならない
    source = f"class FooTests\n{{\n    /* 補足{separator}補足\n     * {NO_REASON} */\n}}\n"
    found = detect_source(
        "tests/FooTests.cs", source.encode(), CSharpAdapter(), CATALOG, AllLines()
    )
    assert [(d.item_id, d.line) for d in found] == [("FLK-ANNOTATION", 4)]


@pytest.mark.parametrize("separator", ["\x0c", "\r", "\u2028"])
def test_excerpt_follows_newline_only(separator: str) -> None:
    call = "void A() { Thread.Sleep(500); }"
    source = f"class FooTests\n{{\n    // x{separator}y\n    {call}\n}}\n"
    found = detect_source(
        "tests/FooTests.cs", source.encode(), CSharpAdapter(), CATALOG, AllLines()
    )
    assert [(d.line, d.text) for d in found] == [(4, call)]
