import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from ast_support import json_users_of_stored_file
from review_fakes import group_result, history_rounds

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.views.review import selection_lines
from jigane.findings.discard import DiscardState, DiscardSummary
from jigane.findings.records import (
    FINDINGS_FORMAT,
    HISTORY_FILE,
    GroupResult,
    HistoryCorruptError,
    HistoryFile,
    HistoryRound,
    ReplacedRun,
    ThemeUpdate,
    _upgrade_finding_v1,
    _upgrade_finding_v2,
    _upgrade_history_v1,
    _upgrade_history_v2,
    load_history_checked,
    merge_group,
    read_findings_json,
    read_history_json,
    read_stored_state,
)
from jigane.groups.definitions import MACRO, SECURITY
from jigane.publishing.rendering import build_context
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.reviews.selection import (
    Picked,
    load_selection,
    save_selection,
    stale_details,
    summary,
)
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput
from jigane.storage.review_error import ReviewError


def _finding(number: int) -> dict[str, Any]:
    return {
        "id": f"F{number}",
        "severity": "warning",
        "group": "macro",
        "viewpoint": "M2",
        "claim": f"claim{number}",
        "body": f"body{number}",
        "evidence": [{"path": "src/a.py", "start_line": 1, "end_line": 2}],
        "adjudication": None,
        "theme": None,
        "triage": "must_read",
        "judgment_region": None,
    }


def v1_history(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """format を持たない版 1 の履歴。"""
    return {"groups": {"macro": [{"ran_at": "t1", "findings": findings}]}}


def test_replaced_findings_move_to_the_history_with_the_run_time(tmp_path: Path) -> None:
    merge_group(tmp_path, MACRO, group_result([_finding(1)]), None)
    assert history_rounds(tmp_path, MACRO) == []
    merge_group(
        tmp_path,
        MACRO,
        GroupResult(()),
        ReplacedRun("2026-10-19T10:00:00+00:00", DiscardSummary(DiscardState.UNRECORDED)),
    )
    assert read_stored_state(tmp_path).groups[MACRO] == ()
    history = history_rounds(tmp_path, MACRO)
    assert [(e.ran_at, [f.body.id for f in e.findings]) for e in history] == [
        ("2026-10-19T10:00:00+00:00", ["F1"])
    ]


def test_history_is_newest_first_and_keeps_empty_rounds(tmp_path: Path) -> None:
    merge_group(tmp_path, MACRO, group_result([_finding(1)]), None)
    merge_group(tmp_path, MACRO, GroupResult(()), None)
    merge_group(tmp_path, MACRO, group_result([_finding(5)]), None)
    merge_group(tmp_path, MACRO, group_result([_finding(6)]), None)
    ids = [[f.body.id for f in e.findings] for e in history_rounds(tmp_path, MACRO)]
    assert ids == [["F5"], [], ["F1"]]


def test_the_same_number_is_not_added_twice(tmp_path: Path) -> None:
    merge_group(tmp_path, MACRO, group_result([_finding(1)]), None)
    merge_group(tmp_path, MACRO, group_result([_finding(1)]), None)
    merge_group(tmp_path, MACRO, group_result([_finding(1)]), None)
    ids = [f.body.id for e in history_rounds(tmp_path, MACRO) for f in e.findings]
    assert ids == ["F1"]


def test_history_is_kept_per_group(tmp_path: Path) -> None:
    merge_group(tmp_path, MACRO, group_result([_finding(1)]), None)
    merge_group(tmp_path, SECURITY, group_result([_finding(2)]), None)
    merge_group(tmp_path, MACRO, GroupResult(()), None)
    assert history_rounds(tmp_path, SECURITY) == []
    assert len(history_rounds(tmp_path, MACRO)) == 1


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n" + "x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    return create_feature_review(root, ["src"], "p")


def _pick_then_lose(review: Review) -> Path:
    directory = review.id.directory()
    merge_group(directory, MACRO, group_result([_finding(1)], ThemeUpdate((), 1)), None)
    selection = load_selection(directory)
    selection.verdicts = {"F1": Picked()}
    save_selection(directory, selection)
    merge_group(directory, MACRO, group_result([], ThemeUpdate((), 1)), None)
    return directory


def test_a_missing_picked_number_gets_its_body_from_the_history(review: Review) -> None:
    directory = _pick_then_lose(review)
    details = stale_details(directory, load_selection(directory), load_history_checked(directory))
    assert [
        (d.id, d.to_dict()["group"], d.to_dict()["claim"], d.to_dict()["body"]) for d in details
    ] == [("F1", "macro", "claim1", "body1")]
    assert summary(directory, load_history_checked(directory))["stale"] == ["F1"]
    _, warnings = build_context(review)
    assert len(warnings) == 1
    assert "F1 M2 claim1（前の回の指摘で、今回の結果にありません）" in warnings[0]
    assert "body1" in warnings[0]


def test_show_prints_the_body_of_a_missing_selection(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    _pick_then_lose(review)
    assert main(["show", str(review.id)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "古い選択" in out
    assert "前の回の指摘で、今回の結果にありません" in out
    assert "body1" in out


def test_a_number_missing_from_the_history_stays_a_bare_number(review: Review) -> None:
    directory = review.id.directory()
    selection = load_selection(directory)
    selection.verdicts = {"F7": Picked()}
    save_selection(directory, selection)
    assert [
        d.to_dict() for d in stale_details(directory, selection, load_history_checked(directory))
    ] == [{"id": "F7", "group": None}]


def test_delete_removes_the_history_with_the_review(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _pick_then_lose(review)
    assert (directory / HISTORY_FILE).is_file()
    assert main(["delete", str(review.id), "--yes", "--json"]) == EXIT_OK
    capsys.readouterr()
    assert not directory.exists()


_DISCARD_FIELDS: dict[str, Any] = {"ran_at": None, "findings": [], "discard": {"discovered": 3}}
CORRUPT_HISTORIES = {
    "not json": "{not json",
    "no groups": json.dumps({}),
    "rounds not a list": json.dumps({"groups": {"macro": 5}}),
    "no findings": json.dumps({"groups": {"macro": [{"ran_at": None}]}}),
    "no ran_at": json.dumps({"groups": {"macro": [{"findings": []}]}}),
    "incomplete discard": json.dumps({"groups": {"macro": [_DISCARD_FIELDS]}}),
}


@pytest.mark.parametrize("content", CORRUPT_HISTORIES.values(), ids=CORRUPT_HISTORIES.keys())
def test_a_corrupt_history_reads_as_empty_with_one_warning_and_no_log(
    tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / HISTORY_FILE).write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="jigane.findings.records"):
        loaded = load_history_checked(tmp_path)
    assert loaded.rounds == {}
    assert len(loaded.warnings) == 1
    assert HISTORY_FILE in loaded.warnings[0].description()
    assert caplog.records == []


@pytest.mark.parametrize("content", CORRUPT_HISTORIES.values(), ids=CORRUPT_HISTORIES.keys())
def test_a_corrupt_history_stops_the_write_and_stays_unchanged(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / HISTORY_FILE).write_text(content, encoding="utf-8")
    merge_group(tmp_path, MACRO, group_result([_finding(1)]), None)
    with pytest.raises(HistoryCorruptError, match=HISTORY_FILE):
        merge_group(tmp_path, MACRO, group_result([_finding(2)]), None)
    assert (tmp_path / HISTORY_FILE).read_text(encoding="utf-8") == content
    assert [f.body.id for f in read_stored_state(tmp_path).groups[MACRO]] == ["F1"]


def test_a_corrupt_history_is_not_logged_while_the_policy_materials_are_built(
    review: Review, caplog: pytest.LogCaptureFixture
) -> None:
    (review.id.directory() / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="jigane.findings.records"):
        macro.build_diagram_prompt(AssemblyInput.load(review))
    # 警告は run の縁が HistoryLoad から描く。資料の組み立てはロガーに流さない
    assert caplog.records == []


def test_a_corrupt_history_is_carried_in_the_load_not_logged_while_the_selection_is_shown(
    review: Review, caplog: pytest.LogCaptureFixture
) -> None:
    directory = review.id.directory()
    selection = load_selection(directory)
    selection.verdicts = {"F7": Picked()}
    save_selection(directory, selection)
    (directory / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="jigane.findings.records"):
        loaded = load_history_checked(directory)
        selection_lines(review, loaded)
    assert len(loaded.warnings) == 1
    assert caplog.records == []


def test_history_file_round_trips_through_json() -> None:
    raw = {
        "format": FINDINGS_FORMAT,
        "groups": {
            "macro": [
                {
                    "ran_at": "t1",
                    "findings": [
                        {
                            "id": "F1",
                            "severity": "warning",
                            "viewpoint": "V",
                            "claim": "A",
                            "triage": None,
                            "theme": None,
                            "adjudication": None,
                        }
                    ],
                },
                {
                    "ran_at": None,
                    "findings": [],
                    "discard": {"discovered": 3, "discarded": 1},
                },
            ]
        },
    }
    parsed = read_history_json(raw)
    assert isinstance(parsed.groups[MACRO][0], HistoryRound)
    assert parsed.to_json() == raw


def test_history_file_writes_only_the_current_nit_form() -> None:
    legacy = {
        "id": "F1",
        "severity": "nit",
        "viewpoint": "discarded",
        "claim": "A",
        "discard_kind": "other",
    }
    written = read_history_json(v1_history([legacy]))
    [finding] = written.groups[MACRO][0].to_json()["findings"]
    assert "discard_kind" not in finding
    assert finding["nit_reason"]["kind"] == "other"


def test_only_findings_module_reads_the_history_file() -> None:
    root = Path(__file__).parent.parent / "jigane"
    readers = json_users_of_stored_file(root, "HISTORY_FILE", "findings_history.json")
    assert readers == ["findings/records.py"]


def _legacy_nit() -> dict[str, Any]:
    return {
        "id": "F1",
        "viewpoint": "discarded",
        "claim": "A",
        "discard_kind": "other",
        "concern": "C",
    }


def test_from_json_migrates_legacy_nit_fields_without_touching_the_input() -> None:
    raw = v1_history([_legacy_nit()])
    before = copy.deepcopy(raw)
    parsed = read_history_json(raw)
    assert raw == before
    migrated = parsed.groups[MACRO][0].findings[0].to_json()
    assert "discard_kind" not in migrated
    assert "concern" not in migrated
    assert migrated["nit_reason"]["kind"] == "other"
    parsed.to_json()


def test_append_history_writes_legacy_findings_in_the_current_form(tmp_path: Path) -> None:
    merge_group(tmp_path, MACRO, group_result([_legacy_nit()]), None)
    merge_group(tmp_path, MACRO, GroupResult(()), None)
    written = json.loads((tmp_path / HISTORY_FILE).read_text(encoding="utf-8"))
    stored = written["groups"]["macro"][0]["findings"][0]
    assert "discard_kind" not in stored
    assert "concern" not in stored
    assert "nit_reason" in stored


def test_v1_history_moves_to_the_current_form() -> None:
    raw = v1_history([{"id": "F1", "adjudication": None}])
    moved = _upgrade_history_v2(_upgrade_history_v1(raw))
    assert moved["format"] == FINDINGS_FORMAT
    [finding] = moved["groups"]["macro"][0]["findings"]
    assert finding == _upgrade_finding_v2(_upgrade_finding_v1({"id": "F1", "adjudication": None}))
    assert HistoryFile.from_json(moved).to_json() == moved


def test_findings_json_and_history_share_the_finding_upgrade() -> None:
    legacy = _legacy_nit()
    in_history = read_history_json(v1_history([legacy])).groups[MACRO][0].findings[0]
    in_findings = read_findings_json({"groups": {"macro": [legacy]}}).groups[MACRO][0]
    assert in_history == in_findings


@pytest.mark.parametrize("version", [0, 4, 99, "3", 3.0, True, None])
def test_an_unsupported_history_version_fails_with_a_clear_error(version: object) -> None:
    raw = {**v1_history([]), "format": version}
    with pytest.raises(ReviewError, match=re.escape(HISTORY_FILE)):
        read_history_json(raw)


def test_a_history_of_an_unknown_version_is_a_warning_and_is_not_overwritten(
    tmp_path: Path,
) -> None:
    path = tmp_path / HISTORY_FILE
    path.write_text(json.dumps({"format": 99, "groups": {}}), encoding="utf-8")
    assert len(load_history_checked(tmp_path).warnings) == 1
    merge_group(tmp_path, MACRO, group_result([_finding(1)]), None)
    with pytest.raises(HistoryCorruptError):
        merge_group(tmp_path, MACRO, GroupResult(()), None)
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == 99


def test_reading_a_v1_history_does_not_write_back_and_saving_writes_the_current_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / HISTORY_FILE
    path.write_text(json.dumps(v1_history([{"id": "F1"}])), encoding="utf-8")
    original = path.read_text(encoding="utf-8")
    assert load_history_checked(tmp_path).warnings == ()
    assert path.read_text(encoding="utf-8") == original
    merge_group(tmp_path, MACRO, group_result([_finding(2)]), None)
    merge_group(tmp_path, MACRO, GroupResult(()), None)
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == FINDINGS_FORMAT


def test_a_version_1_history_with_an_unknown_group_reads_as_corrupt(tmp_path: Path) -> None:
    # 移しは群の名前を弾かない。型へ読む段で、現行に無い群の名前は壊れた履歴になる
    stored = {
        "groups": {"no_such_group": [{"ran_at": "2026-01-01T00:00:00+00:00", "findings": []}]}
    }
    (tmp_path / HISTORY_FILE).write_text(json.dumps(stored), encoding="utf-8")
    loaded = load_history_checked(tmp_path)
    assert loaded.rounds == {}
    assert len(loaded.warnings) == 1
    assert HISTORY_FILE in loaded.warnings[0].description()
