from pathlib import Path
from typing import Any

from review_fakes import make_review, with_map

from jigane.figures.models import MovedSignature, SignatureChange
from jigane.policy_materials import changed_files
from jigane.policy_materials.classes import class_diagram
from jigane.reviews.lifecycle import Review
from jigane.storage.store import ReviewKind

OLD_SOURCE = (
    "LIMIT = 3\n\n\n"
    "def helper(a: int) -> str:\n    return ''\n\n\n"
    "def orphan(b: int) -> int:\n    return b\n\n\n"
    "class Widget:\n    size: int\n"
)
MOVED_SOURCE = "def helper(a: int) -> str:\n    return ''\n\n\nclass Widget:\n    size: int\n"


def _region(
    number: int,
    path: str,
    kind: str,
    old: dict[str, int] | None,
    new: dict[str, int] | None,
) -> dict[str, Any]:
    return {
        "id": f"J{number}",
        "key": f"{path}::{kind}",
        "kind": kind,
        "path": path,
        "qualified_name": "",
        "summary": "s",
        "old_span": old,
        "new_span": new,
    }


def _segment(path: str, *, deleted: bool, last: int) -> dict[str, Any]:
    span = {"start_line": 1, "end_line": last}
    return {
        "old_path": path if deleted else None,
        "new_path": None if deleted else path,
        "old_span": span if deleted else None,
        "new_span": None if deleted else span,
        "qualified_name": "",
        "old_line_count": last if deleted else 0,
        "new_line_count": 0 if deleted else last,
        "declaration_kind": None,
    }


def _deleted_file_review(tmp_path: Path, moved_source: str = MOVED_SOURCE) -> Review:
    """a.py を丸ごと消し、その中の宣言を b.py へ移す（git の名前の変更にならない）。"""
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    (base / "a.py").write_text(OLD_SOURCE, encoding="utf-8")
    (after / "b.py").write_text(moved_source, encoding="utf-8")
    old_last = OLD_SOURCE.count("\n")
    new_last = moved_source.count("\n")
    removed = {"start_line": 1, "end_line": old_last}
    added = {"start_line": 1, "end_line": new_last}
    return make_review(
        after,
        ["b.py"],
        base_tree=base,
        judgment_map={
            "segments": [
                _segment("a.py", deleted=True, last=old_last),
                _segment("b.py", deleted=False, last=new_last),
            ],
            "regions": [
                _region(1, "a.py", "behavior_removal", removed, None),
                _region(2, "b.py", "public_interface", None, added),
            ],
            "line_counts": {},
        },
        kind=ReviewKind.RANGE,
    )


def test_signature_section_shows_declarations_moved_out_of_a_deleted_file(tmp_path: Path) -> None:
    figure = changed_files.signature_changes(_deleted_file_review(tmp_path))
    assert figure.entries == (
        MovedSignature("helper", "a.py:4", "b.py:1", "def helper(a: int) -> str:"),
        MovedSignature("Widget", "a.py:12", "b.py:5", "class Widget:"),
    )


def test_signature_section_does_not_repeat_entries_when_two_regions_name_a_deleted_file(
    tmp_path: Path,
) -> None:
    review = _deleted_file_review(tmp_path)
    regions = review.map_kind.to_dict()["regions"]
    duplicate = {**regions[0], "id": "J3", "key": "a.py::behavior_removal::again"}
    patched = with_map(review, {**review.map_kind.to_dict(), "regions": [*regions, duplicate]})
    assert changed_files.signature_changes(patched).entries == (
        changed_files.signature_changes(review).entries
    )


def test_signature_section_omits_unpaired_declarations_of_a_deleted_file(tmp_path: Path) -> None:
    figure = changed_files.signature_changes(_deleted_file_review(tmp_path))
    names = [e.name for e in figure.entries]
    assert "orphan" not in names
    assert "LIMIT" not in names


def test_signature_section_keeps_a_changed_shape_out_of_the_moved_pairs(tmp_path: Path) -> None:
    review = _deleted_file_review(tmp_path, MOVED_SOURCE.replace("a: int", "a: str"))
    figure = changed_files.signature_changes(review)
    assert [(e.name, e.change) for e in figure.entries] == [
        ("Widget", SignatureChange.MOVED),
        ("helper", SignatureChange.ADDED),
    ]


def test_class_diagram_notes_a_type_moved_out_of_a_deleted_file(tmp_path: Path) -> None:
    text = class_diagram(_deleted_file_review(tmp_path)).text()
    assert "移した型（a.py:12 から）" in text
    # 追加した型として残るのは、新しいファイルのモジュールだけ
    assert text.count("追加した型") == 1
