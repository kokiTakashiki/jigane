import json
from pathlib import Path

import pytest
from gates_support import write_gates_file
from pr_support import commit_all, git

from jigane.commands.views.review import gate_lines
from jigane.flaky.catalog import load_catalog
from jigane.flaky.gates import GATES_FILE, load_gates, update_gates
from jigane.gates.construct_kind import ConstructKind
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


def test_new_saves_unreadable_warnings_in_format_4(tmp_path: Path) -> None:
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
    assert entry.detail == UnreadableDetail(ConstructKind.SYNTAX_ERROR, TreeSide.AFTER)
    saved = json.loads((review.id.directory() / GATES_FILE).read_text(encoding="utf-8"))
    assert saved["format"] == GATES_FORMAT == 4
    assert saved["warnings"][0]["source"] == "unreadable"
    assert saved["warnings"][0]["side"] == "after"
    assert saved["warnings"][0]["kind"] == "syntax_error"
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
    assert saved["format"] == 4
    assert gate_lines(review) == ["ゲート違反 0 件、警告 0 件"]


def test_update_gates_writes_format_4_with_or_without_warnings(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    write_gates_file(out, {"violations": [], "warnings": []})
    gates = update_gates(
        out, tmp_path, ["a.py"], [], load_catalog(), renames={}, unreadable_segments=()
    )
    assert gates.warnings == ()
    assert json.loads((out / GATES_FILE).read_text(encoding="utf-8"))["format"] == 4


def test_format_5_gates_stop_as_newer_with_file_name_and_versions(tmp_path: Path) -> None:
    write_gates_file(tmp_path, {"format": 5, "violations": [], "warnings": []})
    with pytest.raises(ReviewError) as caught:
        load_gates(tmp_path)
    message = str(caught.value)
    assert message.startswith("gates.json は、この jigane より新しいバージョンの jigane が作った")
    assert "JSON の構造のバージョンは 5 で、この jigane が読み込めるのは 4 までです" in message
    assert "`uv tool upgrade jigane`" in message
    assert "`pipx upgrade jigane`" in message
    assert "形の版" not in message


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
        UnreadableDetail(ConstructKind.SYNTAX_ERROR, side),
    )
    data = entry.to_json()
    assert data["source"] == "unreadable"
    assert data["side"] == str(side)
    assert GateEntry.from_json(data) == entry
    # kind は必須で、id から補わない
    del data["kind"]
    with pytest.raises(KeyError, match="kind"):
        GateEntry.from_json(data)


def test_from_json_rejects_unknown_id_bad_kind_and_mismatch() -> None:
    entry = GateEntry(
        "W1",
        "UNREADABLE-SYNTAX-ERROR",
        "a.swift",
        1,
        "x",
        {"text": "t"},
        GateSeverity.UNREADABLE,
        UnreadableDetail(ConstructKind.SYNTAX_ERROR, TreeSide.AFTER),
    )
    data = entry.to_json()
    cases = (
        ({**data, "kind": "nope"}, "nope"),
        ({**data, "kind": "swift_operator_function"}, "食い違"),
    )
    for broken, message in cases:
        with pytest.raises(ValueError, match=message):
            GateEntry.from_json(broken)
