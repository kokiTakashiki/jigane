from pathlib import Path

from review_fakes import make_review
from test_policy_classes import (
    JSON_STORE,
    MOVE_WIDGET,
    ORDER_STORE,
    PYTHON_AFTER,
    PYTHON_BEFORE,
    _moving_review,
    children_source,
    feature_review,
    sibling_review,
    whole,
)

from jigane.policy_materials.siblings import MAX_SIBLINGS, NotSearched, Searched
from jigane.policy_materials.type_deltas import (
    TypeDelta,
    TypeRole,
    type_deltas,
)
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


def test_role_is_the_single_source_of_changed(tmp_path: Path) -> None:
    result = type_deltas(_range_review(tmp_path, PYTHON_BEFORE, PYTHON_AFTER))
    assert {d.role for d in result.deltas} <= {TypeRole.CHANGED, TypeRole.REFERENCED}
    assert all(d.changed == (d.role is TypeRole.CHANGED) for d in result.deltas)


def test_a_type_copying_a_base_without_inheriting_it_gets_that_base_and_its_children(
    tmp_path: Path,
) -> None:
    """基底を継承せずに基底と同名のメソッドを自前で持つ型と、同じ基底を継承する別の型が変更していないファイルにある形。"""
    review = sibling_review(
        tmp_path, {"store.py": JSON_STORE}, {"order.py": ORDER_STORE}, kind=ReviewKind.RANGE
    )
    result = type_deltas(review)
    assert isinstance(result.siblings, Searched)
    (group,) = result.siblings.groups
    assert group.base.name == "JsonStore"
    assert sorted(t.name for t in group.siblings) == ["ProductStore", "UserStore"]
    assert group.omitted == 0
    roles = {d.info.name: d.role for d in result.deltas}
    assert roles == {
        "OrderStore": TypeRole.CHANGED,
        "JsonStore": TypeRole.BASE,
        "UserStore": TypeRole.SIBLING,
        "ProductStore": TypeRole.SIBLING,
    }
    sibling_deltas = [d for d in result.deltas if d.role is not TypeRole.CHANGED]
    assert all(not d.added and not d.member_marks for d in sibling_deltas)


def test_siblings_are_capped_per_base_and_the_rest_is_counted(tmp_path: Path) -> None:
    names = [f"C{i}" for i in range(MAX_SIBLINGS + 1)]
    unchanged = {"base.py": children_source("Base", names)}
    changed = {"w.py": "from base import Base\n\n\nclass Widget(Base):\n    size: int\n"}
    result = type_deltas(sibling_review(tmp_path, unchanged, changed, kind=ReviewKind.RANGE))
    assert isinstance(result.siblings, Searched)
    (group,) = result.siblings.groups
    assert len(group.siblings) == MAX_SIBLINGS
    assert group.omitted == 1


def test_two_bases_make_two_bundles_with_their_own_caps(tmp_path: Path) -> None:
    unchanged = {
        "a.py": children_source("Alpha", [f"A{i}" for i in range(4)]),
        "b.py": children_source("Beta", [f"B{i}" for i in range(5)]),
    }
    changed = {
        "w.py": "from a import Alpha\nfrom b import Beta\n\n\n"
        "class Widget(Alpha, Beta):\n    size: int\n"
    }
    result = type_deltas(sibling_review(tmp_path, unchanged, changed, kind=ReviewKind.RANGE))
    assert isinstance(result.siblings, Searched)
    by_base = {g.base.name: g for g in result.siblings.groups}
    assert set(by_base) == {"Alpha", "Beta"}
    assert (len(by_base["Alpha"].siblings), by_base["Alpha"].omitted) == (3, 1)
    assert (len(by_base["Beta"].siblings), by_base["Beta"].omitted) == (3, 2)


def test_changed_types_sharing_a_base_make_one_bundle(tmp_path: Path) -> None:
    unchanged = {"base.py": children_source("Base", ["Gadget", "Gizmo"])}
    changed = {
        "w.py": "from base import Base\n\n\nclass Widget(Base):\n    size: int\n\n\n"
        "class Sprocket(Base):\n    teeth: int\n"
    }
    result = type_deltas(sibling_review(tmp_path, unchanged, changed, kind=ReviewKind.RANGE))
    assert isinstance(result.siblings, Searched)
    (group,) = result.siblings.groups
    assert group.base.name == "Base"
    assert sorted(t.name for t in group.siblings) == ["Gadget", "Gizmo"]


def test_a_base_with_a_single_other_child_is_not_inferred(tmp_path: Path) -> None:
    only_one = JSON_STORE.replace("\n\nclass ProductStore(JsonStore):\n    pass\n", "")
    review = sibling_review(
        tmp_path, {"store.py": only_one}, {"order.py": ORDER_STORE}, kind=ReviewKind.RANGE
    )
    result = type_deltas(review)
    assert isinstance(result.siblings, Searched)
    assert result.siblings.groups == ()
    assert [d.info.name for d in result.deltas] == ["OrderStore"]


def test_a_file_review_does_not_search_siblings(tmp_path: Path) -> None:
    (tmp_path / "store.py").write_text(JSON_STORE, encoding="utf-8")
    result = type_deltas(feature_review(tmp_path, "order.py", ORDER_STORE))
    assert isinstance(result.siblings, NotSearched)
    assert [d.info.name for d in result.deltas] == ["OrderStore"]


def test_a_sibling_already_referenced_keeps_its_referenced_role(tmp_path: Path) -> None:
    unchanged = {"base.py": children_source("Base", ["Gadget", "Gizmo"])}
    changed = {
        "w.py": "from base import Base, Gadget\n\n\nclass Widget(Base):\n    peer: Gadget\n"
    }
    result = type_deltas(sibling_review(tmp_path, unchanged, changed, kind=ReviewKind.RANGE))
    names = [d.info.name for d in result.deltas]
    assert names.count("Gadget") == 1
    assert _by_name(result.deltas, "Gadget").role is TypeRole.REFERENCED
    assert _by_name(result.deltas, "Gizmo").role is TypeRole.SIBLING
    assert isinstance(result.siblings, Searched)
    assert [t.name for t in result.siblings.groups[0].siblings] == ["Gizmo"]


def test_siblings_keep_only_members_named_like_a_changed_types_members(tmp_path: Path) -> None:
    unchanged = {
        "base.py": "class Base:\n    size: int\n    unrelated: int\n\n\n"
        "class Gadget(Base):\n    size: int\n    extra: int\n\n\n"
        "class Gizmo(Base):\n    pass\n"
    }
    changed = {"w.py": "from base import Base\n\n\nclass Widget(Base):\n    size: int\n"}
    result = type_deltas(sibling_review(tmp_path, unchanged, changed, kind=ReviewKind.RANGE))
    kept = {d.info.name: set(d.info.members) for d in result.deltas}
    assert kept["Gadget"] == {"size"}
    assert kept["Gizmo"] == set()


def test_a_base_declared_twice_counts_each_child_once(tmp_path: Path) -> None:
    unchanged = {
        "base.py": "class Base:\n    pass\n\n\nclass Twice(Base, Base):\n    pass\n\n\n"
        "class Once(Base):\n    pass\n"
    }
    changed = {"w.py": "from base import Base\n\n\nclass Widget(Base):\n    size: int\n"}
    result = type_deltas(sibling_review(tmp_path, unchanged, changed, kind=ReviewKind.RANGE))
    assert isinstance(result.siblings, Searched)
    (group,) = result.siblings.groups
    assert sorted(t.name for t in group.siblings) == ["Once", "Twice"]
