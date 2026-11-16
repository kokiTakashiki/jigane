"""検出がインターフェース経由で動くことの試験。追加の言語のアダプタで往復を確かめる。"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from language_plugin_support import FakeAdapter, external_plugin
from tree_sitter import Node

from jigane.commands.unparsed_files import review_unreadable_constructs
from jigane.commands.unreadable_notice import unreadable_text
from jigane.commands.views.review import gate_lines
from jigane.flaky.unreadable_explanation import explanation_of
from jigane.gates.construct_kind import ConstructKind
from jigane.gates.tree_side import TreeSide
from jigane.judgment_map.unreadable_constructs import (
    Occurrence,
    UnreadableConstructs,
    _scan,
)
from jigane.judgment_map.unreadable_wording import SYNTAX_ERROR_WORDING, UnreadableWording
from jigane.languages import registry
from jigane.languages.base import (
    LanguageAdapter,
    UnsupportedSyntax,
    UnsupportedSyntaxDetector,
    UnsupportedSyntaxKind,
    parse,
)
from jigane.languages.java import JavaAdapter
from jigane.languages.plugin import LanguagePlugin
from jigane.languages.python import PythonAdapter
from jigane.languages.states import LanguageReady
from jigane.reviews.lifecycle import create_feature_review

_FOO = UnsupportedSyntaxKind("foo", "名前を取れない foo（Kotlin）", "foo を書き換えてください。")


class DetectingAdapter(FakeAdapter):
    """unsupported_syntax が返す値を差し替えられる追加の言語のアダプタ。"""

    def __init__(self, name: str, suffix: str, returns: Callable[[], object]) -> None:
        super().__init__(name, frozenset({suffix}))
        self.returns = returns
        self.calls = 0

    def unsupported_syntax(self, _root_node: Node, _source: bytes, /) -> object:
        self.calls += 1
        return self.returns()


def _plugin(adapter: DetectingAdapter) -> LanguagePlugin:
    base = external_plugin(adapter.name, next(iter(adapter.suffixes)))
    return replace(base, adapter=adapter)  # type: ignore[arg-type]


def _use(adapter: DetectingAdapter) -> None:
    builtin = tuple(LanguageReady(p) for p in registry.builtin_plugins())
    registry.set_states((*builtin, LanguageReady(_plugin(adapter))))


def _scan_adapter(adapter: DetectingAdapter, code: str = "x = 1\ny = 2\n") -> list[Occurrence]:
    source = code.encode()
    language: LanguageAdapter = adapter  # type: ignore[assignment]
    root = parse(language, source).root_node
    return list(_scan(source, root, language, "a.kt", TreeSide.AFTER))


def test_plugin_detection_round_trips_to_a_gate_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = DetectingAdapter("kotlin", ".kt", lambda: [UnsupportedSyntax(_FOO, 2)])
    assert isinstance(adapter, UnsupportedSyntaxDetector)
    _use(adapter)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.kt").write_text("x = 1\ny = 2\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    review = create_feature_review(tmp_path, ["src"], name="k")
    found = review_unreadable_constructs(review)
    kind = ConstructKind.of_language("kotlin", "foo")
    assert [(o.kind, o.line, o.excerpt) for o in found.occurrences] == [(kind, 2, "y = 2")]
    assert found.occurrences[0].wording == UnreadableWording(_FOO.title, _FOO.fix_hint)
    assert review.gates is not None
    [entry] = review.gates.warnings
    assert entry.id == "UNREADABLE-KOTLIN-FOO"
    assert _FOO.fix_hint in entry.explanation["text"]
    shown = gate_lines(review)
    assert any("UNREADABLE-KOTLIN-FOO" in line and _FOO.title in line for line in shown)
    assert review.to_dict()["gates"]["warnings"][0]["kind"] == "kotlin_foo"


def test_lines_are_sorted_and_two_findings_on_one_line_stay_two() -> None:
    adapter = DetectingAdapter(
        "kotlin", ".kt", lambda: [UnsupportedSyntax(_FOO, 2), UnsupportedSyntax(_FOO, 1)] * 2
    )
    assert [o.line for o in _scan_adapter(adapter)] == [1, 1, 2, 2]


def _syntax(name: str, title: object, fix_hint: object, line: object) -> list[UnsupportedSyntax]:
    kind = UnsupportedSyntaxKind(name, title, fix_hint)  # type: ignore[arg-type]
    return [UnsupportedSyntax(kind, line)]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("language", "returns"),
    [
        pytest.param("kotlin", lambda: (UnsupportedSyntax(_FOO, 1),), id="not-a-list"),
        pytest.param("kotlin", lambda: [object()], id="wrong-element-type"),
        pytest.param("kotlin", lambda: _syntax("foo", "", "f", 1), id="empty-title"),
        pytest.param("kotlin", lambda: _syntax("foo", "t", "", 1), id="empty-fix-hint"),
        pytest.param("kotlin", lambda: _syntax("foo", "\x1b", "f", 1), id="title-only-control"),
        pytest.param("kotlin", lambda: _syntax("foo", 1, "f", 1), id="title-not-str"),
        pytest.param("kotlin", lambda: _syntax("foo", "t", "f", 0), id="line-zero"),
        pytest.param("kotlin", lambda: _syntax("foo", "t", "f", 4), id="line-beyond-file"),
        pytest.param("kotlin", lambda: _syntax("foo", "t", "f", "1"), id="line-not-int"),
        pytest.param("kotlin", lambda: _syntax("foo", "t", "f", line=True), id="line-bool"),
        pytest.param(
            "kotlin",
            lambda: [*_syntax("foo", "t", "f", 1), *_syntax("foo", "t", "別の直し方。", 2)],
            id="same-kind-different-wording",
        ),
        pytest.param("kotlin", lambda: _syntax("Foo", "t", "f", 1), id="kind-name-grammar"),
        pytest.param("syntax", lambda: _syntax("error", "t", "f", 1), id="syntax-error-collision"),
        pytest.param("my-lang", lambda: _syntax("foo", "t", "f", 1), id="language-with-hyphen"),
        pytest.param(
            "my_lang", lambda: _syntax("foo", "t", "f", 1), id="language-with-underscore"
        ),
    ],
)
def test_invalid_return_values_raise_value_error_naming_the_adapter(
    language: str, returns: Callable[[], object]
) -> None:
    with pytest.raises(ValueError, match=language):
        _scan_adapter(DetectingAdapter(language, ".kt", returns))


def test_syntax_error_file_never_calls_the_interface() -> None:
    def explode() -> object:
        raise AssertionError("構文エラーのファイルで呼ばれた")

    adapter = DetectingAdapter("kotlin", ".kt", explode)
    found = _scan_adapter(adapter, "def f(:\n")
    assert [o.kind for o in found] == [ConstructKind.SYNTAX_ERROR]
    assert found[0].wording == SYNTAX_ERROR_WORDING
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("adapter", "source"),
    [(PythonAdapter(), b"x = 1\n"), (JavaAdapter(), b"class C {}\n")],
    ids=["python", "java"],
)
def test_adapters_without_the_interface_create_nothing(
    adapter: LanguageAdapter, source: bytes
) -> None:
    assert not isinstance(adapter, UnsupportedSyntaxDetector)
    root = parse(adapter, source).root_node
    assert list(_scan(source, root, adapter, "a", TreeSide.AFTER)) == []


def test_control_characters_are_removed_from_the_wording() -> None:
    returns = lambda: _syntax("foo", "見\x1b[31m出\x07し", "直\x1b]0;x\x07し方。", 1)  # noqa: E731 戻り値の差し替えを引数に渡す一行の関数
    [occurrence] = _scan_adapter(DetectingAdapter("kotlin", ".kt", returns))
    assert occurrence.wording == UnreadableWording("見出し", "直し方。")
    text = "\n".join(unreadable_text(UnreadableConstructs((occurrence,))))
    text += explanation_of(occurrence.wording).text
    assert "\x1b" not in text
    assert "\x07" not in text


def test_notice_orders_syntax_error_then_registry_order_then_kind_name() -> None:
    _use(DetectingAdapter("kotlin", ".kt", list))

    def occurrence(language: str, name: str) -> Occurrence:
        wording = UnreadableWording(f"{language}-{name}", "f")
        kind = ConstructKind.of_language(language, name)
        return Occurrence(kind, "p", 1, TreeSide.AFTER, "", wording)

    syntax = Occurrence(
        ConstructKind.SYNTAX_ERROR, "p", 1, TreeSide.AFTER, "", SYNTAX_ERROR_WORDING
    )
    found = UnreadableConstructs(
        (
            occurrence("kotlin", "b"),
            occurrence("kotlin", "a"),
            occurrence("csharp", "nameless_declarator"),
            occurrence("swift", "operator_function"),
            syntax,
        )
    )
    heads = [line.split("（")[0] for line in unreadable_text(found) if "（1 件）" in line]
    assert heads == [
        "構文エラー",
        "swift-operator_function",
        "csharp-nameless_declarator",
        "kotlin-a",
        "kotlin-b",
    ]
