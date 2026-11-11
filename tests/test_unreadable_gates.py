import copy
import json
from pathlib import Path

import pytest
from gates_support import write_gates_file
from pr_support import commit_all, git

from jigane.commands.views.review import gate_lines
from jigane.flaky.catalog import load_catalog
from jigane.flaky.gates import GATES_FILE, load_gates, update_gates
from jigane.gates.entry import (
    GATES_FORMAT,
    GateEntry,
    Gates,
    GateSeverity,
    GateSource,
    UnreadableDetail,
)
from jigane.gates.tree_side import TreeSide
from jigane.languages import registry
from jigane.reviews.lifecycle import (
    Review,
    create_feature_review,
    create_range_review,
    refresh_feature,
    refresh_range,
)
from jigane.storage.review_error import ReviewError

BROKEN = "func (x: Int) {}\n"


def _gates(root: Path) -> tuple[Review, Gates]:
    registry.set_states(None)
    review = create_feature_review(root, ["src"], name="gg")
    assert review.gates is not None
    return review, review.gates


def test_new_saves_unreadable_warnings_in_format_3(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.swift").write_text("let a = 1\n" + BROKEN, encoding="utf-8")
    review, gates = _gates(tmp_path)
    assert gates.violations == ()
    [entry] = gates.warnings
    assert (entry.id, entry.path, entry.line, entry.number) == (
        "UNREADABLE-SYNTAX-ERROR",
        "src/A.swift",
        2,
        "W1",
    )
    assert entry.excerpt == "func (x: Int) {}"
    assert entry.source is GateSource.UNREADABLE
    assert entry.severity is GateSeverity.UNREADABLE
    assert entry.detail == UnreadableDetail(TreeSide.AFTER)
    saved = json.loads((review.id.directory() / GATES_FILE).read_text(encoding="utf-8"))
    assert saved["format"] == GATES_FORMAT == 3
    assert saved["warnings"][0]["source"] == "unreadable"
    assert saved["warnings"][0]["side"] == "after"
    assert "一部の検査で読まれず、その対象から外れます" in entry.explanation["text"]
    assert review.to_dict()["gates"]["warnings"][0]["id"] == "UNREADABLE-SYNTAX-ERROR"
    assert not gates.blocks_exit
    lines = gate_lines(review)
    assert lines[0] == "ゲート違反 0 件、警告 1 件"
    assert lines[1].startswith("  W1 UNREADABLE-SYNTAX-ERROR src/A.swift:2 ")
    assert "（変更前）" not in lines[1]


def test_no_unreadable_construct_leaves_warnings_empty_but_format_is_3(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    review, gates = _gates(tmp_path)
    assert gates.warnings == ()
    saved = json.loads((review.id.directory() / GATES_FILE).read_text(encoding="utf-8"))
    assert saved["format"] == 3
    assert gate_lines(review) == ["ゲート違反 0 件、警告 0 件"]


def test_format_2_file_is_read_without_changing_values(tmp_path: Path) -> None:
    entry = {
        "number": "W1",
        "id": "FLK-X",
        "path": "a.py",
        "line": 3,
        "excerpt": "x",
        "explanation": {"text": "t"},
        "source": "flaky",
        "reason": "r",
        "cause": "c",
        "confidence": "direct",
        "origin": "o",
        "legitimate": "",
        "detector_note": "",
    }
    write_gates_file(tmp_path, {"format": 2, "violations": [], "warnings": [entry]})
    before = copy.deepcopy(json.loads((tmp_path / GATES_FILE).read_text(encoding="utf-8")))
    gates = load_gates(tmp_path)
    assert gates is not None
    [loaded] = gates.warnings
    assert (loaded.number, loaded.id, loaded.line, loaded.excerpt) == ("W1", "FLK-X", 3, "x")
    assert gates.to_json()["warnings"] == before["warnings"]
    assert gates.to_json()["format"] == 3


def test_update_gates_writes_format_3_with_or_without_warnings(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    write_gates_file(out, {"format": 2, "violations": [], "warnings": []})
    gates = update_gates(
        out, tmp_path, ["a.py"], [], load_catalog(), renames={}, unreadable_segments=()
    )
    assert gates.warnings == ()
    assert json.loads((out / GATES_FILE).read_text(encoding="utf-8"))["format"] == 3


def test_newer_format_is_rejected(tmp_path: Path) -> None:
    write_gates_file(tmp_path, {"format": 4, "violations": [], "warnings": []})
    with pytest.raises(ReviewError, match="形の版 4"):
        load_gates(tmp_path)


def test_design_doc_style_empty_segments_detect_nothing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "A.swift").write_text(BROKEN, encoding="utf-8")
    gates = update_gates(
        out, tmp_path, ["A.swift"], [], load_catalog(), renames={}, unreadable_segments=()
    )
    assert gates.warnings == ()


def test_refresh_keeps_numbers_and_never_reuses_a_fixed_one(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "A.swift"
    source.write_text("let a = 1\n" + BROKEN, encoding="utf-8")
    review, gates = _gates(tmp_path)
    assert [e.number for e in gates.warnings] == ["W1"]
    # 本文が同じなら、行が動いても番号は同じ
    source.write_text("let a = 1\nlet b = 2\n" + BROKEN, encoding="utf-8")
    refreshed = refresh_feature(review).review
    assert refreshed.gates is not None
    assert [(e.number, e.line) for e in refreshed.gates.warnings] == [("W1", 3)]
    # 直すと消え、本文を変えた別の記述は新しい番号になる
    source.write_text("let a = 1\n", encoding="utf-8")
    fixed = refresh_feature(refreshed).review
    assert fixed.gates is not None
    assert fixed.gates.warnings == ()
    source.write_text("func (y: Int) {}\n", encoding="utf-8")
    again = refresh_feature(fixed).review
    assert again.gates is not None
    assert [e.number for e in again.gates.warnings] == ["W2"]


def test_base_side_entry_is_marked_in_show_and_json(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/octo/hello.git")
    (root / "A.swift").write_text(BROKEN, encoding="utf-8")
    commit_all(root, "base")
    git(root, "checkout", "-b", "feature")
    (root / "A.swift").write_text("let ok = 1\n" + BROKEN, encoding="utf-8")
    commit_all(root, "head")
    registry.set_states(None)
    review = create_range_review(root, "main..feature", "mine")
    assert review.gates is not None
    sides = sorted(
        str(e.detail.side) for e in review.gates.warnings if isinstance(e.detail, UnreadableDetail)
    )
    assert sides == ["after", "before"]
    lines = gate_lines(review)
    assert lines[0] == "ゲート違反 0 件、警告 2 件"
    assert sum("（変更前）" in line for line in lines) == 1
    refreshed = refresh_range(review).review
    assert refreshed.gates is not None
    assert [e.number for e in refreshed.gates.warnings] == [
        e.number for e in review.gates.warnings
    ]
    assert {w["side"] for w in review.to_dict()["gates"]["warnings"]} == {"before", "after"}


@pytest.mark.parametrize("side", list(TreeSide))
def test_unreadable_entry_round_trips_through_json(side: TreeSide) -> None:
    entry = GateEntry(
        "W3",
        "UNREADABLE-SYNTAX-ERROR",
        "src/A.swift",
        2,
        "func (x: Int) {}",
        {"text": "t"},
        GateSeverity.UNREADABLE,
        UnreadableDetail(side),
    )
    data = entry.to_json()
    assert data["source"] == "unreadable"
    assert data["side"] == str(side)
    assert GateEntry.from_json(data) == entry
