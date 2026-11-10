"""回を重ねたレビューで、前の回の指摘の採否を記録できることを確かめる。"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from test_selection import finding

from jigane.findings.discard import DiscardState, DiscardSummary
from jigane.findings.records import (
    FINDINGS_FILE,
    FINDINGS_FORMAT,
    HISTORY_FILE,
    HistoryLoad,
    HistoryRound,
    finding_dicts,
    load_history_checked,
    read_stored_state,
)
from jigane.groups.definitions import GroupId
from jigane.reviews import selection as selection_module
from jigane.reviews.lifecycle import Review, ReviewError, create_feature_review
from jigane.reviews.selection import (
    NO_RAN_AT,
    UNKNOWN_ORIGIN,
    Held,
    Objected,
    PastRoundInputs,
    PastRoundReader,
    Picked,
    RoundOrigin,
    dump_selection,
    hold,
    load_selection,
    object_to,
    parse_selection,
    pick,
    unhold,
    unobject,
    unpick,
)
from jigane.storage.locking import review_lock

PAST_RAN_AT = "2026-01-01T00:00:00+00:00"
NOW_RAN_AT = "2026-02-02T00:00:00+00:00"
NO_RUNS: dict[GroupId, str | None] = {}


def stored_finding(directory: Path, number: int) -> dict[str, Any]:
    """今の結果の指摘を、履歴に置ける保存の形で、指定の番号に付け直して返す。"""
    [template, *_] = finding_dicts(read_stored_state(directory).groups[GroupId.MICRO])
    return {**template, "id": f"F{number}"}


def past_round(ran_at: str | None, findings: list[dict[str, Any]]) -> dict[str, Any]:
    unrecorded = DiscardSummary(DiscardState.UNRECORDED)
    return HistoryRound.of_findings(ran_at, findings, unrecorded).to_json()


def write_history(directory: Path, rounds: list[dict[str, Any]]) -> None:
    stored = {"format": FINDINGS_FORMAT, "groups": {"micro": rounds}}
    (directory / HISTORY_FILE).write_text(json.dumps(stored), encoding="utf-8")


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n" + "x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    created = create_feature_review(root, ["src"], "p")
    state = {
        "last_number": 6,
        "last_theme_number": 0,
        "stale_groups": [],
        "groups": {"micro": [finding(3), finding(4)]},
        "themes": [],
    }
    directory = created.id.directory()
    (directory / FINDINGS_FILE).write_text(json.dumps(state), encoding="utf-8")
    write_history(
        directory,
        [
            past_round(PAST_RAN_AT, [stored_finding(directory, 1), stored_finding(directory, 2)]),
            past_round(None, [stored_finding(directory, 5)]),
        ],
    )
    return created


def history_of(review: Review) -> HistoryLoad:
    return load_history_checked(review.id.directory())


def reader(review: Review, ran_at: dict[GroupId, str | None] = NO_RUNS) -> PastRoundReader:
    def read() -> PastRoundInputs:
        return PastRoundInputs(history_of(review), ran_at)

    return read


def test_all_six_operations_accept_a_past_round_number(review: Review) -> None:
    directory, read = review.id.directory(), reader(review)
    pick(directory, ["F1"], read)
    hold(directory, ["F2"], read)
    object_to(directory, "F5", "理由", read)
    assert load_selection(directory).verdicts == {
        "F1": Picked(RoundOrigin(GroupId.MICRO, PAST_RAN_AT)),
        "F2": Held(RoundOrigin(GroupId.MICRO, PAST_RAN_AT)),
        "F5": Objected("理由", RoundOrigin(GroupId.MICRO, NO_RAN_AT)),
    }
    unpick(directory, ["F1"], read)
    unhold(directory, ["F2"], read)
    unobject(directory, "F5", read)
    assert load_selection(directory).verdicts == {}


def test_current_number_records_the_origin_of_the_current_run(review: Review) -> None:
    directory = review.id.directory()
    pick(directory, ["F3"], reader(review, {GroupId.MICRO: NOW_RAN_AT}))
    stored = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    assert stored["format"] == 4
    assert stored["verdicts"]["F3"] == {
        "kind": "picked",
        "round": {"group": "micro", "ran_at": NOW_RAN_AT},
    }


def test_current_number_without_a_run_record_has_a_null_time(review: Review) -> None:
    directory = review.id.directory()
    hold(directory, ["F4"], reader(review))
    assert load_selection(directory).verdicts["F4"] == Held(RoundOrigin(GroupId.MICRO, NO_RAN_AT))


def test_version_3_selection_keeps_verdicts_and_has_no_origin() -> None:
    v3 = {
        "format": 3,
        "verdicts": {
            "F1": {"kind": "picked"},
            "F2": {"kind": "held"},
            "F3": {"kind": "objected", "reason": "理由"},
        },
    }
    verdicts = parse_selection(v3).verdicts
    assert verdicts == {"F1": Picked(), "F2": Held(), "F3": Objected("理由")}
    assert all(v.origin is UNKNOWN_ORIGIN for v in verdicts.values())
    assert dump_selection(parse_selection(v3))["verdicts"]["F1"]["round"] is None


def test_round_without_recorded_time_maps_to_no_ran_at_and_back_to_null() -> None:
    raw = {
        "format": 4,
        "verdicts": {"F1": {"kind": "picked", "round": {"group": "micro", "ran_at": None}}},
    }
    selection = parse_selection(raw)
    assert selection.verdicts["F1"] == Picked(RoundOrigin(GroupId.MICRO, NO_RAN_AT))
    assert dump_selection(selection)["verdicts"]["F1"]["round"] == {
        "group": "micro",
        "ran_at": None,
    }


def test_number_in_current_and_history_stops_with_both_places(review: Review) -> None:
    directory = review.id.directory()
    write_history(directory, [past_round(PAST_RAN_AT, [stored_finding(directory, 3)])])
    with pytest.raises(ReviewError, match=r"F3.*今の結果.*履歴"):
        pick(directory, ["F3"], reader(review))
    assert not (directory / "selection.json").exists()


def test_number_in_two_history_rounds_stops(review: Review) -> None:
    directory = review.id.directory()
    write_history(
        directory,
        [
            past_round(PAST_RAN_AT, [stored_finding(directory, 1)]),
            past_round(NOW_RAN_AT, [stored_finding(directory, 1)]),
        ],
    )
    with pytest.raises(ReviewError, match=r"F1.*履歴.*履歴"):
        hold(directory, ["F1"], reader(review))


def test_overlap_of_a_number_not_named_does_not_stop_any_operation(review: Review) -> None:
    directory, read = review.id.directory(), reader(review)
    write_history(
        directory,
        [
            past_round(PAST_RAN_AT, [stored_finding(directory, n) for n in (1, 2, 3)]),
            past_round(None, [stored_finding(directory, 5)]),
        ],
    )
    pick(directory, ["F1"], read)
    hold(directory, ["F2"], read)
    object_to(directory, "F5", "理由", read)
    unpick(directory, ["F1"], read)
    unhold(directory, ["F2"], read)
    unobject(directory, "F5", read)
    assert load_selection(directory).verdicts == {}
    for operation in (
        lambda: unpick(directory, ["F3"], read),
        lambda: unhold(directory, ["F3"], read),
        lambda: unobject(directory, "F3", read),
        lambda: object_to(directory, "F3", "理由", read),
    ):
        with pytest.raises(ReviewError, match=r"F3.*今の結果.*履歴"):
            operation()


def test_unreadable_history_rejects_a_past_number_and_says_why(review: Review) -> None:
    directory = review.id.directory()
    (directory / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    read = reader(review)
    assert history_of(review).warnings
    with pytest.raises(ReviewError, match=r"知らない指摘の番号です: F1.*履歴が読めない"):
        pick(directory, ["F1"], read)
    with pytest.raises(ReviewError, match=r"履歴が読めない"):
        object_to(directory, "F2", "理由", read)
    pick(directory, ["F3"], read)
    assert set(load_selection(directory).verdicts) == {"F3"}


def test_inputs_are_read_after_the_review_lock_is_taken(
    review: Review, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_lock = review_lock

    @contextmanager
    def recording_lock(directory: Path) -> Iterator[None]:
        with real_lock(directory):
            events.append("locked")
            yield
        events.append("unlocked")

    monkeypatch.setattr(selection_module, "review_lock", recording_lock)

    def read() -> PastRoundInputs:
        events.append("read")
        return PastRoundInputs(history_of(review), NO_RUNS)

    pick(review.id.directory(), ["F1"], read)
    assert events == ["locked", "read", "unlocked"]


def test_theme_member_in_two_places_stops(review: Review) -> None:
    directory = review.id.directory()
    state = json.loads((directory / FINDINGS_FILE).read_text(encoding="utf-8"))
    state["last_theme_number"] = 1
    state["groups"]["micro"] = [finding(3, theme="B1"), finding(4, theme="B1")]
    state["themes"] = [
        {
            "id": "B1",
            "title": "t",
            "finding_ids": ["F3", "F4"],
            "severity": "warning",
            "triage": "must_read",
        }
    ]
    (directory / FINDINGS_FILE).write_text(json.dumps(state), encoding="utf-8")
    write_history(directory, [past_round(PAST_RAN_AT, [stored_finding(directory, 3)])])
    with pytest.raises(ReviewError, match=r"F3.*今の結果.*履歴"):
        pick(directory, ["B1"], reader(review))
    assert not (directory / "selection.json").exists()


@pytest.mark.parametrize("operation", [pick, hold])
def test_pick_and_hold_stop_on_a_number_in_two_places(
    review: Review, operation: Callable[..., object]
) -> None:
    directory = review.id.directory()
    write_history(directory, [past_round(PAST_RAN_AT, [stored_finding(directory, 3)])])
    with pytest.raises(ReviewError, match=r"F3.*今の結果.*履歴"):
        operation(directory, ["F3"], reader(review))
    assert not (directory / "selection.json").exists()
