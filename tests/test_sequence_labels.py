from pathlib import Path

from jigane.languages.csharp import CSharpAdapter
from jigane.languages.swift import SwiftAdapter
from jigane.sequence.index import Index, TypeKey, build_index

SOURCE = """
class Engine {
    func fmt(a: Int) {}
    func fmt(b: Int) {}
    func fmt(_ c: Int) {}
    func two(a: Int, b: Int) {}
    func two(b: Int, a: Int) {}
}
"""


def _index(tmp_path: Path) -> Index:
    (tmp_path / "E.swift").write_text(SOURCE, encoding="utf-8")
    return build_index(tmp_path, ["E.swift"], SwiftAdapter())


def _label_of(index: Index, name: str, labels: list[str | None]) -> tuple[str | None, int]:
    member, count, _ = index.lookup(
        index.types[TypeKey("Engine")], name, ("method",), len(labels), tuple(labels)
    )
    assert member is not None
    return member.parameters[0].label, count


def test_labels_pick_the_matching_overload(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert _label_of(index, "fmt", ["b"]) == ("b", 1)
    assert _label_of(index, "fmt", ["a"]) == ("a", 1)


def test_underscore_external_name_has_no_label(tmp_path: Path) -> None:
    index = _index(tmp_path)
    labels = [m.parameters[0].label for m in index.types[TypeKey("Engine")].members["fmt"]]
    assert labels == ["a", "b", None]


def test_label_order_distinguishes_overloads(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert _label_of(index, "two", ["b", "a"])[0] == "b"


def test_unmatched_labels_fall_back_to_the_first_declaration(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert _label_of(index, "fmt", ["zzz"]) == ("a", 3)


def test_calls_without_labels_keep_the_first_declaration(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert _label_of(index, "fmt", [None]) == ("a", 3)


def test_csharp_named_arguments_do_not_narrow_overloads(tmp_path: Path) -> None:
    (tmp_path / "E.cs").write_text(
        "class Engine { void Two(int a, int b) {} void Two(int b, int a) {} }\n", encoding="utf-8"
    )
    index = build_index(tmp_path, ["E.cs"], CSharpAdapter())
    engine = index.types[TypeKey("Engine")]
    member, count, _ = index.lookup(engine, "Two", ("method",), 2, ("b", "a"))
    assert member is not None
    # 名前付き引数は順序を入れ替えられるので、位置ごとのラベルの比較は当てはまらず、
    # 宣言の先頭を採る
    assert (member.parameters[0].name, count) == ("a", 2)


def test_swift_labels_narrow_overloads_by_the_adapter_property(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert SwiftAdapter().positional_argument_labels
    assert not CSharpAdapter().positional_argument_labels
    assert _label_of(index, "two", ["b", "a"]) == ("b", 1)
