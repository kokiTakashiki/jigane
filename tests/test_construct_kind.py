import pytest

from jigane.gates.construct_kind import ConstructKind
from jigane.gates.entry import GateEntry, GateSeverity, UnreadableDetail
from jigane.gates.tree_side import TreeSide


def _entry(kind: ConstructKind) -> GateEntry:
    return GateEntry(
        "W1",
        kind.entry_id,
        "a.kt",
        1,
        "x",
        {"text": "t"},
        GateSeverity.UNREADABLE,
        UnreadableDetail(kind, TreeSide.AFTER),
    )


def test_of_language_builds_the_value_and_id_of_existing_kinds() -> None:
    swift = ConstructKind.of_language("swift", "operator_function")
    csharp = ConstructKind.of_language("csharp", "nameless_declarator")
    assert (str(swift), swift.entry_id) == (
        "swift_operator_function",
        "UNREADABLE-SWIFT-OPERATOR-FUNCTION",
    )
    assert (str(csharp), csharp.entry_id) == (
        "csharp_nameless_declarator",
        "UNREADABLE-CSHARP-NAMELESS-DECLARATOR",
    )
    assert ConstructKind.SYNTAX_ERROR.entry_id == "UNREADABLE-SYNTAX-ERROR"


@pytest.mark.parametrize(
    ("language_name", "kind_name"),
    [
        ("Swift", "x"),
        ("my-lang", "x"),
        ("my_lang", "x"),
        ("", "x"),
        ("1lang", "x"),
        ("swift", ""),
        ("swift", "Foo"),
        ("swift", "a-b"),
        ("swift", "_a"),
        ("syntax", "error"),
    ],
)
def test_of_language_rejects_invalid_names_and_syntax_error_collision(
    language_name: str, kind_name: str
) -> None:
    with pytest.raises(ValueError, match=r"言語名|種類の名前|衝突"):
        ConstructKind.of_language(language_name, kind_name)


@pytest.mark.parametrize("value", ["", "Swift_x", "swift", "swift-x", "_x", "swift_", "1a_b"])
def test_constructor_rejects_values_outside_the_grammar(value: str) -> None:
    with pytest.raises(ValueError, match="綴りが不正"):
        ConstructKind(value)


def test_constructor_accepts_syntax_error_and_equal_values_compare_equal() -> None:
    assert ConstructKind("syntax_error") == ConstructKind.SYNTAX_ERROR
    assert ConstructKind("kotlin_foo") == ConstructKind.of_language("kotlin", "foo")
    assert len({ConstructKind("kotlin_foo"), ConstructKind.of_language("kotlin", "foo")}) == 1


def test_plugin_kind_round_trips_through_json() -> None:
    kind = ConstructKind.of_language("kotlin", "foo")
    entry = _entry(kind)
    data = entry.to_json()
    assert data["kind"] == "kotlin_foo"
    assert data["id"] == "UNREADABLE-KOTLIN-FOO"
    assert GateEntry.from_json(data) == entry


def test_kind_whose_id_differs_is_rejected() -> None:
    data = _entry(ConstructKind.of_language("kotlin", "foo")).to_json()
    with pytest.raises(ValueError, match="食い違"):
        GateEntry.from_json({**data, "kind": "kotlin_bar"})
