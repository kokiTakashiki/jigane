import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from review_fakes import make_review, with_map

from jigane.commands.subcommands import sequence as sequence_command
from jigane.figures.models import NoteText
from jigane.reviews.lifecycle import DiffSource, LocalRange, RangeOrigin, Review
from jigane.sequence.diagram import mark_differences, to_mermaid
from jigane.sequence.review_diagrams import (
    BEFORE_NO_ROOT_NOTE,
    NO_BASE_TREE_DISPLAY,
    build_model,
    review_sequences,
)
from jigane.sequence.roots import SequenceError, Side
from jigane.storage.store import ReviewKind

FIXTURES = Path(__file__).parent / "fixtures" / "sequence" / "before"
SWAP = Path(__file__).parent / "fixtures" / "sequence" / "swap"
WHOLE = {"start_line": 1, "end_line": 99}


def _segment(name: str, *, old: bool = True) -> dict[str, Any]:
    return {
        "old_path": "App.cs" if old else None,
        "old_span": WHOLE if old else None,
        "new_path": "App.cs",
        "new_span": WHOLE,
        "qualified_name": name,
        "declaration_kind": "method",
    }


def _Review(*, with_base: bool) -> Review:  # noqa: N802 Review を作る関数を、クラスの呼び出しに見立てて大文字で始める
    return make_review(
        FIXTURES / "head",
        ["App.cs"],
        base_tree=FIXTURES / "base" if with_base else None,
        judgment_map={
            "segments": [
                _segment("Service.Run"),
                _segment("Service.Extra", old=False),
                _segment("App.Main"),
                _segment("App.Quiet"),
                _segment("App.Added", old=False),
            ],
            "regions": [],
        },
        kind=ReviewKind.RANGE if with_base else ReviewKind.FEATURE,
    )


def _model(side: Side, *, with_base: bool = True) -> dict[str, Any]:
    return build_model(
        _Review(with_base=with_base), None, None, 4, full=False, include_tests=False, side=side
    )


def _by_root(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {d["root"]: d for d in model["diagrams"]}


def test_both_gives_two_diagrams_when_the_root_exists_on_both_sides() -> None:
    main = _by_root(_model(Side.BOTH))["App.Main"]
    assert main["before_note"] is None
    # Work は変更後の図の呼び先でも変更前の側の C でもないので、変更前の図に入らない
    assert [a["name"] for a in main["before"]["arrows"]] == ["Run"]
    assert [a["name"] for a in main["arrows"]] == ["Run", "Extra"]


def test_both_keeps_one_diagram_with_a_note_when_the_before_side_has_no_path_or_root() -> None:
    diagrams = _by_root(_model(Side.BOTH))
    assert diagrams["App.Added"]["before"] is None
    assert diagrams["App.Added"]["before_note"] == "変更前にこの入口がありません"
    assert diagrams["App.Quiet"]["before"] is None
    assert diagrams["App.Quiet"]["before_note"] == "変更前は変更した宣言へ届きません"


def test_after_has_no_before_keys_and_equals_the_diagrams_of_both() -> None:
    after = _model(Side.AFTER)
    both = _model(Side.BOTH)
    assert after["side"] == "after"
    assert all("before" not in d for d in after["diagrams"])
    # 対比の印 mark は、変更前の図がある入口の矢印にだけ付く
    stripped = [
        {
            **{k: v for k, v in d.items() if k not in ("before", "before_note")},
            "arrows": [{k: v for k, v in a.items() if k != "mark"} for a in d["arrows"]],
        }
        for d in both["diagrams"]
    ]
    assert stripped == after["diagrams"]


def test_before_keeps_only_roots_with_a_before_diagram() -> None:
    model = _model(Side.BEFORE)
    assert model["side"] == "before"
    assert [d["root"] for d in model["diagrams"]] == ["App.Main"]
    assert [a["name"] for a in model["diagrams"][0]["arrows"]] == ["Run"]


def test_before_fails_without_a_base_tree_and_both_notes_it() -> None:
    with pytest.raises(SequenceError, match="変更前のツリーがない") as error:
        _model(Side.BEFORE, with_base=False)
    assert "C#" not in str(error.value)
    model = _model(Side.BOTH, with_base=False)
    assert {d["before_note"] for d in model["diagrams"]} == {NO_BASE_TREE_DISPLAY}


def test_cli_side_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sequence_command, "load_review", lambda _id: _Review(with_base=True))
    monkeypatch.setattr(sequence_command, "resolve_review", lambda text: text)
    parser = argparse.ArgumentParser()
    sequence_command.register(parser.add_subparsers())
    outputs = {}
    for side in ("before", "after", "both"):
        args = parser.parse_args(["sequence", "r#1", "--side", side, "--format", "json"])
        assert args.handler(args, None) == 0
        outputs[side] = json.loads(capsys.readouterr().out)
    assert outputs["before"]["side"] == "before"
    assert outputs["after"]["side"] == "after"
    assert outputs["both"]["side"] == "both"
    args = parser.parse_args(["sequence", "r#1", "--format", "mermaid"])
    args.handler(args, None)
    assert "%% root: App.Main (before)" in capsys.readouterr().out


def test_before_diagram_keeps_existing_calls_when_the_before_changed_set_is_empty() -> None:
    review = _Review(with_base=True)
    # 入口 App.Main は変更前の側に変更した宣言を持たない（変更前の側の C が空）
    _set_segments(
        review,
        [
            _segment("Service.Run", old=False),
            _segment("Service.Extra", old=False),
            _segment("App.Main", old=False),
        ],
    )
    model = build_model(review, None, None, 4, full=False, include_tests=False, side=Side.BOTH)
    main = _by_root(model)["App.Main"]
    assert main["before_note"] is None
    assert [a["name"] for a in main["before"]["arrows"]] == ["Run"]


def test_root_with_no_after_arrows_gets_the_after_empty_note() -> None:
    review = _Review(with_base=True)
    _set_segments(review, [_segment("App.Quiet")])
    review.repository_root = str(FIXTURES / "base")
    quiet = _by_root(
        build_model(review, None, None, 4, full=False, include_tests=False, side=Side.BOTH)
    )["App.Quiet"]
    assert quiet["arrows"] == []
    assert quiet["before"] is None
    assert quiet["before_note"] == "変更後の図に呼び出しがありません"


def _set_segments(review: Review, segments: list[dict[str, Any]]) -> None:
    review.map_kind = with_map(review, {"segments": segments}).map_kind


def _arrows(*names: str, owner: str = "A") -> list[dict[str, Any]]:
    return [{"sender": owner, "receiver": "B", "name": n, "line": i} for i, n in enumerate(names)]


def _marks(arrows: list[dict[str, Any]]) -> list[str]:
    return [a["mark"] for a in arrows]


def test_mark_differences_marks_added_and_removed_arrows() -> None:
    before, after = _arrows("a", "b", "c"), _arrows("a", "c", "d")
    mark_differences(before, after)
    assert _marks(before) == ["same", "removed", "same"]
    assert _marks(after) == ["same", "same", "added"]


def test_mark_differences_treats_a_reordering_as_removal_and_addition() -> None:
    before, after = _arrows("a", "b"), _arrows("b", "a")
    mark_differences(before, after)
    assert sorted(_marks(before)) == ["removed", "same"]
    assert sorted(_marks(after)) == ["added", "same"]


def test_mark_differences_ignores_line_numbers() -> None:
    before = _arrows("a", "b")
    after = [{**a, "line": a["line"] + 100} for a in _arrows("x", "a", "b")]
    mark_differences(before, after)
    assert _marks(before) == ["same", "same"]
    assert _marks(after) == ["added", "same", "same"]


def test_mark_differences_distinguishes_sender_and_receiver() -> None:
    before, after = _arrows("a"), _arrows("a", owner="Z")
    mark_differences(before, after)
    assert _marks(before) == ["removed"]
    assert _marks(after) == ["added"]


def test_mermaid_labels_carry_the_marks_and_unmarked_arrows_are_unchanged() -> None:
    participants = [
        {"alias": "P0", "qualified_name": "A", "display": "A", "external": False, "regions": []},
        {"alias": "P1", "qualified_name": "B", "display": "B", "external": False, "regions": []},
    ]

    def diagram(arrows: list[dict[str, Any]]) -> dict[str, Any]:
        full = [{"status": "resolved", "region": None, **a} for a in arrows]
        return {"participants": participants, "arrows": full, "labels": [], "notes": []}

    plain = to_mermaid(diagram(_arrows("a", "b")))
    assert "（" not in plain
    before, after = _arrows("a", "b"), _arrows("a", "c")
    mark_differences(before, after)
    assert "    P0->>P1: b()（削除）\n" in to_mermaid(diagram(before))
    text = to_mermaid(diagram(after))
    assert "    P0->>P1: a()\n" in text
    assert "    P0->>P1: c()（追加）\n" in text


def test_sequences_of_a_diff_review_have_before_and_after_panels() -> None:
    review = _Review(with_base=True)
    review.repository_root = str(SWAP / "head")
    review.origin = RangeOrigin(
        LocalRange("b..h", review.repository_root),
        DiffSource("b", "h", "m", str(SWAP / "base")),
    )
    deleted = {**_segment("Service.Old"), "new_path": None, "new_span": None}
    _set_segments(
        review,
        [
            _segment("App.Main"),
            _segment("Service.New", old=False),
            deleted,
        ],
    )
    main = review_sequences(review).flows[0]
    assert main.note is None
    assert [p.title for p in main.panels] == ["変更前", "変更後"]
    before, after = (p.mermaid for p in main.panels)
    assert "Old()（削除）" in before
    assert "New()（追加）" in after


def test_sequence_without_a_before_diagram_has_one_panel_and_the_reason() -> None:
    sequences = review_sequences(_Review(with_base=True)).flows
    added = next(s for s in sequences if s.title.startswith("App.Added"))
    assert [p.title for p in added.panels] == ["変更後"]
    assert added.note == NoteText(BEFORE_NO_ROOT_NOTE, "変更前にこの入口がありません")


def test_sequence_without_a_base_tree_has_one_panel_and_no_note() -> None:
    sequences = review_sequences(_Review(with_base=False)).flows
    assert sequences
    assert all([p.title for p in s.panels] == ["シーケンス図"] for s in sequences)
    assert all(s.note is None for s in sequences)
