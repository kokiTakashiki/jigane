from pathlib import Path
from typing import Any

from review_fakes import make_review, with_map

from jigane.figures.models import MovedSignature, SignatureChange
from jigane.policy_materials import changed_files
from jigane.reviews.lifecycle import Review
from jigane.storage.store import ReviewKind


def _region(
    number: int,
    path: str,
    kind: str,
    name: str,
    old: dict[str, int] | None,
    new: dict[str, int] | None,
) -> dict[str, Any]:
    return {
        "id": f"J{number}",
        "key": f"{path}::{name}::{kind}",
        "kind": kind,
        "path": path,
        "qualified_name": name,
        "summary": "s",
        "old_span": old,
        "new_span": new,
    }


def _kept_file_move_review(tmp_path: Path, name: str, declaration: str) -> Review:
    """宣言を a.py から b.py へ移し、a.py は残る。

    消えた側の箇所は、実際の判断の地図と同じく、種類が振る舞いの削除になる。
    """
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    (base / "a.py").write_text("x = 1\n\n\n" + declaration, encoding="utf-8")
    (after / "a.py").write_text("x = 1\n", encoding="utf-8")
    (after / "b.py").write_text(declaration, encoding="utf-8")
    last = declaration.count("\n")
    removed = {"start_line": 4, "end_line": 3 + last}
    added = {"start_line": 1, "end_line": last}
    return make_review(
        after,
        ["a.py", "b.py"],
        base_tree=base,
        judgment_map={
            "segments": [],
            "regions": [
                _region(1, "b.py", "public_interface", name, None, added),
                _region(2, "a.py", "behavior_removal", name, removed, None),
            ],
            "line_counts": {},
        },
        kind=ReviewKind.RANGE,
    )


def test_signature_section_shows_a_function_moved_out_of_a_kept_file(tmp_path: Path) -> None:
    review = _kept_file_move_review(
        tmp_path, "helper", "def helper(a: int) -> str:\n    return ''\n"
    )
    figure = changed_files.signature_changes(review)
    assert figure.entries == (
        MovedSignature("helper", "a.py:4", "b.py:1", "def helper(a: int) -> str:"),
    )


def test_signature_section_shows_a_type_moved_out_of_a_kept_file(tmp_path: Path) -> None:
    review = _kept_file_move_review(tmp_path, "Widget", "class Widget:\n    size: int\n")
    figure = changed_files.signature_changes(review)
    assert figure.entries == (MovedSignature("Widget", "a.py:4", "b.py:1", "class Widget:"),)


def _review_with_row_of(tmp_path: Path, path: str) -> Review:
    """b.py の宣言を消した状態で、地図には指定したファイルの行だけを残す。"""
    review = _kept_file_move_review(
        tmp_path, "helper", "def helper(a: int) -> str:\n    return ''\n"
    )
    (tmp_path / "after" / "b.py").write_text("y = 2\n", encoding="utf-8")
    kept = review.map_kind.to_dict()
    rows = [row for row in kept["regions"] if row["path"] == path]
    return with_map(review, {**kept, "regions": rows})


def test_signature_section_lists_a_function_deleted_from_a_kept_file(tmp_path: Path) -> None:
    """判定に効くのは a.py の削除の行である。b.py の行は、宣言が b.py に無いので効かない。"""
    figure = changed_files.signature_changes(_review_with_row_of(tmp_path, "a.py"))
    assert [(e.name, e.change) for e in figure.entries] == [("helper", SignatureChange.DELETED)]


def test_signature_section_omits_a_deletion_without_its_map_row(tmp_path: Path) -> None:
    figure = changed_files.signature_changes(_review_with_row_of(tmp_path, "b.py"))
    assert figure.entries == ()
