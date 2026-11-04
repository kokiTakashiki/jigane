from pathlib import Path

from review_fakes import make_review
from test_policy_classes import (
    MOVE_WIDGET,
    PYTHON_AFTER,
    PYTHON_BEFORE,
    _moving_review,
    whole,
)

from jigane.policy_materials.type_deltas import TypeDelta, type_deltas
from jigane.reviews.lifecycle import Review
from jigane.storage.store import ReviewKind


def _range_review(tmp_path: Path, before: str, after: str) -> Review:
    base = tmp_path / "base"
    tree = tmp_path / "after"
    base.mkdir()
    tree.mkdir()
    (base / "m.py").write_text(before, encoding="utf-8")
    (tree / "m.py").write_text(after, encoding="utf-8")
    lines = len(after.splitlines())
    return make_review(
        tree,
        ["m.py"],
        base_tree=base,
        judgment_map={"segments": [whole("m.py", lines)], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )


def _by_name(deltas: tuple[TypeDelta, ...], name: str) -> TypeDelta:
    return next(d for d in deltas if d.info.name == name)


def test_added_changed_and_removed_members_land_in_the_delta(tmp_path: Path) -> None:
    result = type_deltas(_range_review(tmp_path, PYTHON_BEFORE, PYTHON_AFTER))
    widget = _by_name(result.deltas, "Widget")
    assert widget.changed
    assert not widget.added
    assert widget.moved_from is None
    assert set(widget.member_marks.values()) == {"+", "~"}
    assert any(r.endswith("Widget.old() None") for r in widget.removed_members)
    assert any("size" in p for p in widget.previous_forms)
    assert result.before_indexes is not None


def test_added_type_marks_every_member_added(tmp_path: Path) -> None:
    result = type_deltas(_range_review(tmp_path, PYTHON_BEFORE, PYTHON_AFTER))
    color = _by_name(result.deltas, "Color")
    assert color.added
    assert color.changed
    assert set(color.member_marks.values()) <= {"+"}
    assert color.removed_members == ()


def test_enum_value_changes_land_in_value_marks_and_removed_members(tmp_path: Path) -> None:
    base = tmp_path / "base"
    tree = tmp_path / "after"
    base.mkdir()
    tree.mkdir()
    (base / "s.swift").write_text(
        "enum Mode {\n    case fast\n    case slow\n}\n", encoding="utf-8"
    )
    (tree / "s.swift").write_text(
        "enum Mode {\n    case fast\n    case idle\n}\n", encoding="utf-8"
    )
    review = make_review(
        tree,
        ["s.swift"],
        base_tree=base,
        judgment_map={"segments": [whole("s.swift", 4)], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    mode = _by_name(type_deltas(review).deltas, "Mode")
    assert dict(mode.value_marks) == {"fast": "", "idle": "+"}
    assert "Mode.slow" in mode.removed_members


def test_base_change_lands_in_shape(tmp_path: Path) -> None:
    before = "class Base:\n    pass\n\n\nclass Widget(Base):\n    size: int\n"
    after = "class Base:\n    pass\n\n\nclass Widget:\n    size: int\n"
    widget = _by_name(type_deltas(_range_review(tmp_path, before, after)).deltas, "Widget")
    assert widget.shape is not None
    assert widget.shape.bases_before == ("Base",)
    assert widget.shape.bases_after == ()


def test_moved_type_records_its_origin(tmp_path: Path) -> None:
    review = _moving_review(tmp_path, MOVE_WIDGET, "x = 1\n", "x = 1\n", MOVE_WIDGET)
    widget = _by_name(type_deltas(review).deltas, "Widget")
    assert widget.moved_from is not None
    assert widget.moved_from.path == "a.py"
    assert not widget.member_marks


def test_referenced_type_is_not_changed(tmp_path: Path) -> None:
    result = type_deltas(_range_review(tmp_path, PYTHON_BEFORE, PYTHON_AFTER))
    gadget_like = [d for d in result.deltas if not d.changed]
    assert all(not d.added and not d.member_marks and not d.removed_members for d in gadget_like)
    changed_first = [d.changed for d in result.deltas]
    assert changed_first == sorted(changed_first, reverse=True)


def test_no_changed_type_gives_empty_deltas(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    review = make_review(
        tmp_path,
        ["m.py"],
        judgment_map={"segments": [], "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )
    assert type_deltas(review).deltas == ()
