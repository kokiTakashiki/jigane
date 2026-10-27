from pathlib import Path
from typing import Any

from review_fakes import make_review

from jigane.figures.models import (
    AddedSignature,
    ChangedSignature,
    DeletedSignature,
    MovedSignature,
    SignatureChange,
    SignatureFigure,
)
from jigane.policy_materials import changed_files
from jigane.policy_materials.classes import class_diagram
from jigane.reviews.lifecycle import Review
from jigane.storage.store import ReviewKind

PYTHON_AFTER = """\
from enum import Enum


class Color(Enum):
    RED = 1
    GREEN = 2


class Base:
    pass


class Widget(Base):
    size: int
    color: Color | None

    def build(self, scale: int, label: str = "x") -> "Gadget":
        return Gadget()

    def paint(self, color: Color) -> None:
        self.color = color


class Gadget:
    name: str
"""

PYTHON_BEFORE = """\
class Base:
    pass


class Widget(Base):
    size: str

    def build(self, scale: int) -> "Gadget":
        return Gadget()

    def old(self) -> None:
        pass


class Gadget:
    name: str
"""

SWIFT = """\
protocol Shape {
    func area() -> Double
}

enum Mode {
    case fast
    case slow
}

final class Circle: Shape {
    var radius: Double
    var mode: Mode?
    func area() -> Double { return radius }
    func resize(to value: Double, force: Bool) {}
}
"""

CSHARP = """\
namespace Shop;

public enum Level { Low, High }

public class Item
{
    public string Name { get; set; }
    public Level Rank;
    public int Price(Level level, int count) { return count; }
}
"""


def whole(path: str, last: int) -> dict[str, Any]:
    return {
        "new_path": path,
        "old_path": path,
        "new_span": {"start_line": 1, "end_line": last},
        "old_span": {"start_line": 1, "end_line": last},
        "new_line_count": last,
        "old_line_count": last,
        "qualified_name": "",
        "declaration_kind": None,
    }


def feature_review(root: Path, name: str, source: str) -> Review:
    (root / name).write_text(source, encoding="utf-8")
    lines = len(source.splitlines())
    return make_review(
        root,
        [name],
        judgment_map={"segments": [whole(name, lines)], "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )


def test_python_members_annotations_bases_enum_values_and_positions(tmp_path: Path) -> None:
    text = class_diagram(feature_review(tmp_path, "m.py", PYTHON_AFTER)).text()
    assert "classDiagram" in text
    assert "size: int" in text
    assert "color: Color | None" in text
    assert "build(scale: int, label?: str)" in text
    assert "RED" in text
    assert "GREEN" in text
    assert "Base <|-- Widget" in text
    assert "%% Widget m.py:13" in text
    assert "%% Color m.py:4" in text


def test_swift_members_signature_inheritance_and_enum_cases(tmp_path: Path) -> None:
    text = class_diagram(feature_review(tmp_path, "s.swift", SWIFT)).text()
    assert "radius: Double" in text
    assert "mode: Mode?" in text
    assert "resize(" in text
    assert "force: Bool" in text
    assert "fast" in text
    assert "slow" in text
    assert "Shape <|-- Circle" in text
    assert "s.swift:10" in text


def test_csharp_members_signature_and_enum_values(tmp_path: Path) -> None:
    text = class_diagram(feature_review(tmp_path, "i.cs", CSHARP)).text()
    assert "Name: string" in text
    assert "Rank: Level" in text
    assert "Price(level: Level, count: int) int" in text
    assert "Low" in text
    assert "High" in text
    assert "i.cs:5" in text
    assert "Item --> Level" in text


def test_diff_review_marks_added_changed_and_removed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    (base / "m.py").write_text(PYTHON_BEFORE, encoding="utf-8")
    (after / "m.py").write_text(PYTHON_AFTER, encoding="utf-8")
    review = make_review(
        after,
        ["m.py"],
        base_tree=base,
        judgment_map={"segments": [whole("m.py", 24)], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    text = class_diagram(review).text()
    assert "~size: int" in text
    assert "~build(" in text
    assert "+paint(" in text
    assert "+color: Color | None" in text
    assert "変更で削除した項目" in text
    assert "Widget.old() None" in text
    assert "追加した型" in text


def test_diff_review_shows_unchanged_members_by_name_only(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    (base / "m.py").write_text(PYTHON_BEFORE, encoding="utf-8")
    (after / "m.py").write_text(PYTHON_AFTER, encoding="utf-8")
    review = make_review(
        after,
        ["m.py"],
        base_tree=base,
        judgment_map={"segments": [whole("m.py", 24)], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    lines = [line.strip() for line in class_diagram(review).text().splitlines()]
    # 変わらない項目（Gadget.name）は名前だけで、型注記を落とす
    assert "name" in lines
    assert "name: str" not in lines
    # 変わった項目と追加した項目は、シグネチャを残す
    assert any(line.startswith("~size: int") for line in lines)
    assert any(line.startswith("+paint(") for line in lines)


def test_feature_review_keeps_member_signatures(tmp_path: Path) -> None:
    # 変更前との比べが無い機能レビュー式では、どの項目も変わっていないとは言えない
    text = class_diagram(feature_review(tmp_path, "i.cs", CSHARP)).text()
    assert "Price(level: Level, count: int) int" in text


def test_class_diagram_lists_every_type_and_member(tmp_path: Path) -> None:
    source = "".join(
        f"class T{i}:\n" + "".join(f"    f{j}: int\n" for j in range(40)) + "\n\n"
        for i in range(15)
    )
    text = class_diagram(feature_review(tmp_path, "m.py", source)).text()
    assert text.count("%% T") == 15
    assert "f39: int" in text
    assert "ほか" not in text
    assert "件" not in text


def test_unsupported_language_files_are_counted(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("class A:\n    x: int\n", encoding="utf-8")
    (tmp_path / "b.rb").write_text("class B; end\n", encoding="utf-8")
    review = make_review(
        tmp_path,
        ["a.py", "b.rb"],
        judgment_map={
            "segments": [whole("a.py", 2), whole("b.rb", 1)],
            "regions": [],
            "line_counts": {},
        },
        kind=ReviewKind.FEATURE,
    )
    text = class_diagram(review).text()
    assert "型を読めない言語のファイル 1 件" in text


def _interface_region(
    number: int, name: str, old: dict[str, int] | None, new: dict[str, int] | None
) -> dict[str, Any]:
    return {
        "id": f"J{number}",
        "key": f"m.py::{name}::public_interface",
        "kind": "public_interface",
        "path": "m.py",
        "qualified_name": name,
        "summary": "s",
        "old_span": old,
        "new_span": new,
    }


def test_signature_section_pairs_before_and_after_without_bodies(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    (base / "m.py").write_text(
        "def helper(a: int) -> str:\n    return 'old_body'\n", encoding="utf-8"
    )
    (after / "m.py").write_text(
        "def helper(a: int, b: str | None = None) -> str:\n    return 'new_body'\n\n\n"
        "def fresh() -> None:\n    pass\n",
        encoding="utf-8",
    )
    span = {"start_line": 1, "end_line": 2}
    review = make_review(
        after,
        ["m.py"],
        base_tree=base,
        judgment_map={
            "segments": [],
            "regions": [
                _interface_region(1, "helper", span, span),
                _interface_region(2, "fresh", None, {"start_line": 5, "end_line": 6}),
            ],
            "line_counts": {},
        },
        kind=ReviewKind.RANGE,
    )
    figure = changed_files.signature_changes(review)
    text = figure.text()
    assert "- 変更前: def helper(a: int) -> str:" in text
    assert "+ 変更後: def helper(a: int, b: str | None = None) -> str:" in text
    assert "- 変更前: （新規）" in text
    assert "+ 変更後: def fresh() -> None:" in text
    assert "m.py:1" in text
    assert "old_body" not in text
    assert "new_body" not in text
    # 資料の文は、宣言ごとの構造から作る。新規か変更かは change の値で持つ
    entries = figure.entries
    assert [(e.name, e.place, e.change) for e in entries] == [
        ("helper", "m.py:1", SignatureChange.CHANGED),
        ("fresh", "m.py:5", SignatureChange.ADDED),
    ]
    assert isinstance(entries[1], AddedSignature)
    assert isinstance(entries[0], ChangedSignature)
    assert entries[0].new == "def helper(a: int, b: str | None = None) -> str:"
    assert "####" not in str(entries)


def test_signature_entry_pairs_each_change_with_its_old_and_new() -> None:
    added = AddedSignature("g", "m.py:1", "def g()")
    deleted = DeletedSignature("h", "m.py:2", "def h()")
    changed = ChangedSignature("i", "m.py:3", "def i()", "def i(x)")
    assert (added.old_text(), added.new_text()) == ("（新規）", "def g()")
    assert (deleted.old_text(), deleted.new_text()) == ("def h()", "（削除）")
    assert (changed.old_text(), changed.new_text()) == ("def i()", "def i(x)")
    assert [e.change for e in (added, deleted, changed)] == [
        SignatureChange.ADDED,
        SignatureChange.DELETED,
        SignatureChange.CHANGED,
    ]


def test_signature_figure_json_carries_the_change_kind() -> None:
    entries = (
        AddedSignature("g", "m.py:1", "def g()"),
        DeletedSignature("h", "m.py:2", "def h()"),
        ChangedSignature("i", "m.py:3", "def i()", "def i(x)"),
    )
    assert SignatureFigure(entries).to_json() == {
        "kind": "signatures",
        "entries": [
            {"name": "g", "place": "m.py:1", "change": "added", "new": "def g()"},
            {"name": "h", "place": "m.py:2", "change": "deleted", "old": "def h()"},
            {
                "name": "i",
                "place": "m.py:3",
                "change": "changed",
                "old": "def i()",
                "new": "def i(x)",
            },
        ],
    }


def _named_region(number: int, path: str, name: str, line: int) -> dict[str, Any]:
    return {
        "id": f"J{number}",
        "key": f"{path}::{name}::public_interface",
        "kind": "public_interface",
        "path": path,
        "qualified_name": name,
        "summary": "s",
        "old_span": None,
        "new_span": {"start_line": line, "end_line": line},
    }


def test_class_diagram_has_no_type_cap(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    sources = {
        "a.py": "class A0:\n    x: int\n\n\nclass A1:\n    x: int\n",
        "b.py": "class B0:\n    x: int\n",
        "tests/test_c.py": "class TestC0:\n    x: int\n",
    }
    for name, source in sources.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
    review = make_review(
        tmp_path,
        list(sources),
        judgment_map={
            "segments": [whole(n, len(s.splitlines())) for n, s in sources.items()],
            "regions": [],
            "line_counts": {},
        },
        kind=ReviewKind.FEATURE,
    )
    text = class_diagram(review).text()
    for name in ("A0", "A1", "B0", "TestC0"):
        assert f"%% {name} " in text
    assert "件" not in text


def test_signatures_have_no_cap_and_exclude_non_callables(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    (base / "tests").mkdir(parents=True)
    (after / "tests").mkdir(parents=True)
    sources = {
        "a.py": (
            "from enum import Enum\n\nLIMIT = 3\n\n\nclass Mode(Enum):\n    FAST = 1\n\n\n"
            "class Box:\n    size: int\n\n    def grow(self) -> None:\n        pass\n\n\n"
            "def a1() -> None:\n    pass\n\n\ndef a2() -> None:\n    pass\n"
        ),
        "b.py": "def b1() -> None:\n    pass\n",
        "tests/test_c.py": "def test_c1() -> None:\n    pass\n",
    }
    for name, source in sources.items():
        (after / name).write_text(source, encoding="utf-8")
    names = {
        "a.py": ["LIMIT", "Mode", "Mode.FAST", "Box", "Box.size", "Box.grow", "a1", "a2"],
        "b.py": ["b1"],
        "tests/test_c.py": ["test_c1"],
    }
    regions = [
        _named_region(number, path, name, 1)
        for number, (path, name) in enumerate(
            [(p, n) for p, ns in names.items() for n in ns], start=1
        )
    ]
    review = make_review(
        after,
        list(sources),
        base_tree=base,
        judgment_map={"segments": [], "regions": regions, "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    figure = changed_files.signature_changes(review)
    # 項目、列挙の値、定数は載らない。テストのファイルは最後
    assert [e.name for e in figure.entries] == [
        "Mode",
        "Box",
        "Box.grow",
        "a1",
        "a2",
        "b1",
        "test_c1",
    ]


def test_annotation_and_base_names_do_not_resolve_to_same_named_modules(tmp_path: Path) -> None:
    (tmp_path / "list.py").write_text("def helper() -> None:\n    pass\n", encoding="utf-8")
    (tmp_path / "Base.py").write_text("X = 1\n", encoding="utf-8")
    source = (
        "class Holder(Base):\n"
        "    items: list[str]\n\n"
        "    def take(self, rows: list[int]) -> None:\n"
        "        pass\n"
    )
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    review = make_review(
        tmp_path,
        ["m.py", "list.py", "Base.py"],
        judgment_map={"segments": [whole("m.py", 5)], "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )
    text = class_diagram(review).text()
    assert "class Holder" in text
    assert "list.py" not in text
    assert "Base.py" not in text
    assert " <|-- " not in text
    assert " --> " not in text
    assert " ..> " not in text


MOVE_WIDGET = (
    "class Widget:\n    size: int\n\n    def build(self, scale: int) -> None:\n        pass\n"
)


def _moving_review(
    tmp_path: Path, before_a: str, before_b: str, after_a: str, after_b: str
) -> Review:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    for root, a, b in ((base, before_a, before_b), (after, after_a, after_b)):
        (root / "a.py").write_text(a, encoding="utf-8")
        (root / "b.py").write_text(b, encoding="utf-8")
    segments = [
        {**whole("a.py", 1), "new_path": None, "new_span": None, "new_line_count": 0},
        {**whole("b.py", 1), "old_path": None, "old_span": None, "old_line_count": 0},
    ]
    return make_review(
        after,
        ["a.py", "b.py"],
        base_tree=base,
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )


def test_class_diagram_notes_a_type_moved_between_files(tmp_path: Path) -> None:
    after_b = MOVE_WIDGET
    review = _moving_review(tmp_path, MOVE_WIDGET, "x = 1\n", "x = 1\n", after_b)
    text = class_diagram(review).text()
    assert "移した型（a.py:1 から）" in text
    assert "追加した型" not in text
    assert "+size: int" not in text


def test_class_diagram_keeps_added_for_a_type_with_a_different_shape(tmp_path: Path) -> None:
    review = _moving_review(
        tmp_path, MOVE_WIDGET, "x = 1\n", "x = 1\n", MOVE_WIDGET.replace("size: int", "size: str")
    )
    text = class_diagram(review).text()
    assert "追加した型" in text
    assert "移した型（" not in text


def _moved_function_review(tmp_path: Path, old: str, new: str) -> Review:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    (base / "a.py").write_text(old + "\n    return ''\n", encoding="utf-8")
    (after / "a.py").write_text("x = 1\n", encoding="utf-8")
    (after / "b.py").write_text(new + "\n    return ''\n", encoding="utf-8")
    one = {"start_line": 1, "end_line": 2}

    def region(
        number: int, path: str, old_span: dict[str, int] | None, new_span: dict[str, int] | None
    ) -> dict[str, Any]:
        return {**_interface_region(number, "helper", old_span, new_span), "path": path}

    return make_review(
        after,
        ["a.py", "b.py"],
        base_tree=base,
        judgment_map={
            "segments": [],
            "regions": [region(1, "a.py", one, None), region(2, "b.py", None, one)],
            "line_counts": {},
        },
        kind=ReviewKind.RANGE,
    )


def test_signature_section_shows_a_moved_function_as_one_entry(tmp_path: Path) -> None:
    signature = "def helper(a: int) -> str:"
    review = _moved_function_review(tmp_path, signature, signature)
    figure = changed_files.signature_changes(review)
    assert figure.entries == (MovedSignature("helper", "a.py:1", "b.py:1", signature),)
    assert "移した（a.py:1 → b.py:1）" in figure.text()
    assert "変更前" not in figure.text()
    assert figure.to_json()["entries"][0]["change"] == "moved"


def test_signature_section_keeps_add_and_delete_when_the_signature_differs(tmp_path: Path) -> None:
    review = _moved_function_review(tmp_path, "def helper(a: int):", "def helper(a: str):")
    figure = changed_files.signature_changes(review)
    assert sorted(e.change for e in figure.entries) == [
        SignatureChange.ADDED,
        SignatureChange.DELETED,
    ]


SAME_NAME_SWIFT = """\
private struct WaitFailure: Error {
    let reason: String
    var description: String { reason }
}
"""


def _two_files_adding_the_same_type(tmp_path: Path, suffix: str, source: str) -> Review:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    names = [f"One.{suffix}", f"Two.{suffix}"]
    for name in names:
        (after / name).write_text(source, encoding="utf-8")
    last = len(source.splitlines())
    segments = [{**whole(name, last), "old_path": None, "old_span": None} for name in names]
    return make_review(
        after,
        names,
        base_tree=base,
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )


def test_class_diagram_shows_two_files_private_swift_types_of_the_same_name_as_two_types(
    tmp_path: Path,
) -> None:
    review = _two_files_adding_the_same_type(tmp_path, "swift", SAME_NAME_SWIFT)
    text = class_diagram(review).text()
    assert "WaitFailure（One.swift）" in text
    assert "WaitFailure（Two.swift）" in text
    # 型ごとに項目が一度ずつ出る
    assert text.count("+reason: String") == 2
    assert text.count("+description: String") == 2


def test_python_module_without_types_shows_its_constants_and_functions(tmp_path: Path) -> None:
    source = (
        "CURSOR_WIDTH = 4\nSLIT_WIDTH: int = 2\n\n\ndef pattern_02(x: int) -> int:\n    return x\n"
    )
    text = class_diagram(feature_review(tmp_path, "title_call.py", source)).text()
    assert "%% title_call title_call.py:1 変更した型" in text
    assert "<<module>>" in text
    assert "CURSOR_WIDTH" in text
    assert "SLIT_WIDTH: int" in text
    assert "pattern_02(x: int) int" in text


def test_python_module_with_only_constants_is_shown(tmp_path: Path) -> None:
    text = class_diagram(feature_review(tmp_path, "limits.py", "MAX_WIDTH = 4\n")).text()
    assert "<<module>>" in text
    assert "MAX_WIDTH" in text


def test_python_module_with_only_unchanged_constants_leaves_no_empty_box(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    (base / "m.py").write_text("import os\n\nLIMIT = 3\nNAME = 'a'\n", encoding="utf-8")
    (after / "m.py").write_text("import sys\n\nLIMIT = 3\nNAME = 'a'\n", encoding="utf-8")
    # 変更は 1 行目（import）だけで、モジュールの型の行 1 に当たる。定数は変えていない
    segments = [{**whole("m.py", 1), "new_span": {"start_line": 1, "end_line": 1}}]
    review = make_review(
        after,
        ["m.py"],
        base_tree=base,
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    text = class_diagram(review).text()
    assert "<<module>>" not in text
    assert "LIMIT" not in text


def test_python_module_shows_only_changed_variables(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    before_source = "_RULES = 1\nREVIEW_FILE = 2\nLIMIT = 3\n\n\ndef run() -> int:\n    return 1\n"
    after_source = "_RULES = 1\nREVIEW_FILE = 2\nLIMIT = 4\n\n\ndef run() -> int:\n    return 2\n"
    (base / "m.py").write_text(before_source, encoding="utf-8")
    (after / "m.py").write_text(after_source, encoding="utf-8")
    segments = [
        {**whole("m.py", 3), "new_span": {"start_line": 3, "end_line": 3}},
        {**whole("m.py", 7), "new_span": {"start_line": 6, "end_line": 7}},
    ]
    review = make_review(
        after,
        ["m.py"],
        base_tree=base,
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    text = class_diagram(review).text()
    assert "LIMIT" in text
    # シグネチャの変わらない関数は、印が無いので名前だけになる
    assert "run" in text
    assert "run()" not in text
    assert "_RULES" not in text
    assert "REVIEW_FILE" not in text
    # 外した変数は削除として数えない
    assert "変更で削除した項目" not in text


SELF_REFERENCE = """\
class Node:
    def link(self, other: "Node") -> "Node":
        return other
"""


def test_a_member_naming_its_own_type_draws_no_relation(tmp_path: Path) -> None:
    text = class_diagram(feature_review(tmp_path, "m.py", SELF_REFERENCE)).text()
    diagram = text.split("classDiagram")[1]
    assert "class Node" in diagram
    assert "..>" not in diagram
    assert "-->" not in diagram
    assert "<|--" not in diagram
