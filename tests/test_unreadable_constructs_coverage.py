from pathlib import Path

import pytest
from language_plugin_support import external_plugin
from pr_support import commit_all, git

from jigane.commands.unparsed_files import review_unreadable_constructs
from jigane.commands.unreadable_notice import occurrence_text, unreadable_text
from jigane.flaky.checks import _swift_methods
from jigane.flaky.unreadable_explanation import explanation_of
from jigane.gates.construct_kind import ConstructKind
from jigane.gates.tree_side import TreeSide
from jigane.judgment_map.unreadable_constructs import (
    Occurrence,
    UnreadableConstructs,
    _scan,
)
from jigane.judgment_map.unreadable_wording import SYNTAX_ERROR_WORDING
from jigane.languages import registry
from jigane.languages.base import parse
from jigane.languages.states import LanguageReady
from jigane.languages.swift import SwiftAdapter
from jigane.languages.swift_nodes import (
    IdentifierName,
    InitName,
    OperatorName,
    function_name_of,
    operator_function_nodes,
)
from jigane.languages.swift_stores import _functions
from jigane.languages.value_set_core import walk_nodes
from jigane.reviews.lifecycle import create_feature_review, create_range_review
from jigane.text.terminal import one_line_text


def _range_repo(root: Path, base: dict[str, str], head: dict[str, str]) -> None:
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/octo/hello.git")
    for relative, body in base.items():
        (root / relative).write_text(body, encoding="utf-8")
    commit_all(root, "base")
    git(root, "checkout", "-b", "feature")
    for relative, body in head.items():
        (root / relative).write_text(body, encoding="utf-8")
    commit_all(root, "head")


def test_range_review_marks_the_base_side_and_shows_both_sides_of_the_same_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "work"
    # 新旧の両側に構文エラーがあり、旧側は 1 行目、新側は 2 行目にある
    _range_repo(
        root, {"A.swift": "func (x: Int) {}\n"}, {"A.swift": "let x = 1\nfunc (y: Int) {}\n"}
    )
    registry.set_states(None)
    review = create_range_review(root, "main..feature", "mine")
    assert review.base_tree_path is not None
    found = review_unreadable_constructs(review)
    syntax = found.of_kind(ConstructKind.SYNTAX_ERROR)
    assert sorted(occurrence_text(o) for o in syntax) == ["A.swift:2", "変更前 A.swift:1"]


def test_control_characters_in_the_path_are_stripped_in_the_notice() -> None:
    path = "src/a\x1b[31mb.swift"
    found = UnreadableConstructs(
        (
            Occurrence(
                ConstructKind.SYNTAX_ERROR, path, 1, TreeSide.AFTER, "", SYNTAX_ERROR_WORDING
            ),
        )
    )
    text = "\n".join(unreadable_text(found))
    assert "構文エラー（1 件）" in text
    assert "\x1b" not in text
    assert f"{one_line_text(path)}:1" in text


def test_plugin_language_gets_only_the_syntax_error_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builtin = tuple(LanguageReady(p) for p in registry.builtin_plugins())
    registry.set_states((*builtin, LanguageReady(external_plugin("zig", ".zig"))))
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.zig").write_text("def f(:\n", encoding="utf-8")
    (src / "ok.zig").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    found = review_unreadable_constructs(create_feature_review(tmp_path, ["src"], name="p"))
    syntax = found.of_kind(ConstructKind.SYNTAX_ERROR)
    assert [occurrence_text(o) for o in syntax] == ["src/bad.zig:1"]
    assert {o.kind for o in found.occurrences} == {ConstructKind.SYNTAX_ERROR}


def test_link_pointing_outside_the_tree_does_not_stop_the_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.swift"
    outside.write_text("func (x: Int) {}\n", encoding="utf-8")
    root = tmp_path / "work"
    (root / "src").mkdir(parents=True)
    Path(root / "src" / "Link.swift").symlink_to(outside)
    (root / "src" / "Bad.swift").write_text("func (x: Int) {}\n", encoding="utf-8")
    monkeypatch.chdir(root)
    registry.set_states(None)
    found = review_unreadable_constructs(create_feature_review(root, ["src"], name="l"))
    syntax = found.of_kind(ConstructKind.SYNTAX_ERROR)
    assert [occurrence_text(o) for o in syntax] == ["src/Bad.swift:1"]


BROKEN_SOURCES = {
    "swift": [b"func (", b"struct S { func +", b"protocol P { func }", b"func f(", b"extension {"],
    "csharp": [
        b"class C { Foo.Bar(x); int",
        b"class { int a",
        b"class C { void M( {",
        b"enum E {",
    ],
}


@pytest.mark.parametrize("language", sorted(BROKEN_SOURCES))
def test_declarations_do_not_raise_on_broken_input(language: str) -> None:
    adapter = registry.adapter_for("A.swift" if language == "swift" else "A.cs")
    assert adapter is not None
    for source in BROKEN_SOURCES[language]:
        root = parse(adapter, source).root_node
        adapter.declarations(root, source)


def test_swift_sites_agree_on_each_name_kind() -> None:
    adapter = registry.adapter_for("A.swift")
    assert isinstance(adapter, SwiftAdapter)
    source = b"func f() { g() }\nfunc + (a: Int) { g() }\nfunc `class`() { g() }\n"
    root = parse(adapter, source).root_node
    functions = [n for n in root.children if n.type == "function_declaration"]
    assert [type(function_name_of(n)) for n in functions] == [
        IdentifierName,
        OperatorName,
        IdentifierName,
    ]
    declared = [adapter.function_declaration_name(n) for n in functions]
    assert declared == ["f", "+", "`class`"]
    assert [name for _, name in _functions(root)] == ["f", "+", "`class`"]
    # 検査の対象は識別子だけで、演算子関数は飛ばす
    assert [name for name, _ in _swift_methods(root)] == ["f", "`class`"]


def test_range_review_marks_a_construct_in_the_base_tree_in_the_notice(
    tmp_path: Path,
) -> None:
    root = tmp_path / "work"
    # 変更前の木だけに演算子関数があり、変更後は消えている
    _range_repo(root, {"A.swift": "func + (a: Int) {}\n"}, {"A.swift": "func f() {}\n"})
    registry.set_states(None)
    review = create_range_review(root, "main..feature", "mine")
    found = review_unreadable_constructs(review)
    assert [(o.kind, o.side) for o in found.occurrences] == [
        (ConstructKind.of_language("swift", "operator_function"), TreeSide.BEFORE)
    ]
    assert "変更前 A.swift:1" in "\n".join(unreadable_text(found))


def test_notice_shows_five_items_per_rule_and_counts_the_rest(tmp_path: Path) -> None:
    root = tmp_path / "work"
    body = "".join("func + (a: Int) {}\n" for _ in range(7))
    (root / "src").mkdir(parents=True)
    (root / "src" / "A.swift").write_text(body, encoding="utf-8")
    registry.set_states(None)
    found = review_unreadable_constructs(create_feature_review(root, ["src"], name="m"))
    text = "\n".join(unreadable_text(found))
    assert "（7 件）" in text
    assert "src/A.swift:5" in text
    assert "src/A.swift:6" not in text
    assert "ほか 2 件" in text


def test_operator_function_in_a_protocol_is_found(tmp_path: Path) -> None:
    root = tmp_path / "work"
    (root / "src").mkdir(parents=True)
    (root / "src" / "P.swift").write_text(
        "protocol P {\n    static func == (a: Self, b: Self) -> Bool\n}\n", encoding="utf-8"
    )
    registry.set_states(None)
    found = review_unreadable_constructs(create_feature_review(root, ["src"], name="p"))
    assert [(o.kind, o.line) for o in found.occurrences] == [
        (ConstructKind.of_language("swift", "operator_function"), 2)
    ]


def test_initializer_name_is_the_name_field_and_is_not_reported() -> None:
    adapter = registry.adapter_for("A.swift")
    assert adapter is not None
    root = parse(adapter, b"struct S { init(x: Int) {} }\n").root_node
    init = next(n for n in walk_nodes(root) if n.type == "init_declaration")
    name = function_name_of(init)
    # `name` フィールドは `init` のキーワードで、識別子でない
    assert isinstance(name, InitName)
    assert name.node.type == "init"
    assert list(operator_function_nodes(root)) == []


def _swift_occurrences(code: str) -> tuple[Occurrence, ...]:
    adapter = SwiftAdapter()
    source = code.encode()
    root = parse(adapter, source).root_node
    return tuple(_scan(source, root, adapter, "a.swift", TreeSide.AFTER))


_OPERATORS = (
    "struct V {\n  static func + (l: V, r: V) -> V { l }\n"
    "  static func - (l: V, r: V) -> V { l }\n}\n"
)


def test_every_construct_kind_has_wording_and_a_distinct_entry_id() -> None:
    found = (
        *_swift_occurrences(_OPERATORS),
        Occurrence(ConstructKind.SYNTAX_ERROR, "a", 1, TreeSide.AFTER, "", SYNTAX_ERROR_WORDING),
    )
    kinds = {o.kind: o.wording for o in found}
    assert len(kinds) == 2
    for wording in kinds.values():
        assert wording.title
        assert wording.fix_hint
    assert len({kind.entry_id for kind in kinds}) == len(kinds)


def test_notice_gives_each_present_kind_its_fix_hint_once() -> None:
    found = UnreadableConstructs(_swift_occurrences(_OPERATORS))
    assert len(found.occurrences) == 2
    hint = found.occurrences[0].wording.fix_hint
    text = "\n".join(unreadable_text(found))
    assert text.count(hint) == 1
    assert SYNTAX_ERROR_WORDING.fix_hint not in text
    assert "書き換えるか" not in text


def test_warning_explanation_uses_the_fix_hint_of_its_wording() -> None:
    for occurrence in _swift_occurrences(_OPERATORS):
        assert occurrence.wording.fix_hint in explanation_of(occurrence.wording).text
