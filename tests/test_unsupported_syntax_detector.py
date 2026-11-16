"""UnsupportedSyntaxDetector の試験。メンバーの返す値を、現行の検出の結果と突き合わせる。"""

import pytest

from jigane.gates.construct_kind import ConstructKind
from jigane.gates.tree_side import TreeSide
from jigane.judgment_map.unreadable_constructs import _scan
from jigane.languages import base
from jigane.languages.base import UnsupportedSyntaxDetector, parse
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.java import JavaAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter

_SWIFT = (
    "struct V {\n  static func + (l: V, r: V) -> V { l }\n"
    "  func `class`() {}\n  static func - (l: V, r: V) -> V { l }\n}\n"
    "protocol P { static func == (l: Self, r: Self) -> Bool }\n"
)
_CSHARP = "class C {\n  Foo.Bar(x);\n  int ok = 1;\n  void M() { Foo.Bar(x); }\n  Baz.Qux(y);\n}\n"

_CASES = [
    pytest.param(
        SwiftAdapter(),
        "A.swift",
        _SWIFT,
        ConstructKind.of_language("swift", "operator_function"),
        id="swift",
    ),
    pytest.param(
        CSharpAdapter(),
        "A.cs",
        _CSHARP,
        ConstructKind.of_language("csharp", "nameless_declarator"),
        id="csharp",
    ),
]


@pytest.mark.parametrize(("adapter", "path", "code", "kind"), _CASES)
def test_member_returns_the_kind_and_lines_of_the_current_detection(
    adapter: base.LanguageAdapter, path: str, code: str, kind: ConstructKind
) -> None:
    source = code.encode()
    root = parse(adapter, source).root_node
    assert isinstance(adapter, UnsupportedSyntaxDetector)
    returned = adapter.unsupported_syntax(root, source)
    current = list(_scan(source, root, adapter, path, TreeSide.AFTER))
    assert current
    assert sorted(item.line for item in returned) == sorted(o.line for o in current)
    assert {o.kind for o in current} == {kind}
    for item in returned:
        assert {(o.wording.title, o.wording.fix_hint) for o in current} == {
            (item.kind.title, item.kind.fix_hint)
        }
        assert kind.value == f"{adapter.name}_{item.kind.name}"


@pytest.mark.parametrize(
    ("adapter", "code"), [(SwiftAdapter(), "func f() {}\n"), (CSharpAdapter(), "class C {}\n")]
)
def test_member_returns_nothing_for_a_clean_file(
    adapter: UnsupportedSyntaxDetector, code: str
) -> None:
    source = code.encode()
    assert adapter.unsupported_syntax(parse(adapter, source).root_node, source) == []


@pytest.mark.parametrize("adapter", [PythonAdapter(), JavaAdapter()])
def test_adapters_without_the_member_are_not_detectors_but_stay_language_adapters(
    adapter: base.LanguageAdapter,
) -> None:
    assert isinstance(adapter, base.LanguageAdapter)
    assert not isinstance(adapter, UnsupportedSyntaxDetector)
