"""観点の保存形（版 4 の UUID）の変換が、ファイル全体の形の口だけにあることを縛る。"""

import copy
from typing import Any

import pytest

from jigane.findings.records import read_findings_json, read_history_json
from jigane.groups.definitions import MACRO, MICRO
from jigane.groups.viewpoints import by_number


def _finding(viewpoint: str) -> dict[str, Any]:
    return {
        "id": "F1",
        "severity": "warning",
        "viewpoint": viewpoint,
        "claim": "主張",
        "triage": None,
        "theme": None,
        "adjudication": None,
    }


def _findings_file(version: int, viewpoint: str, group: str = "macro") -> dict[str, Any]:
    return {
        "format": version,
        "last_number": 1,
        "groups": {group: [_finding(viewpoint)]},
        "stale_groups": [],
        "last_theme_number": 0,
        "themes": [],
    }


def _history_file(version: int, viewpoint: str) -> dict[str, Any]:
    entry = {"ran_at": "t", "findings": [_finding(viewpoint)]}
    return {"format": version, "groups": {"macro": [entry]}}


def _uuid_of(number: str) -> str:
    return str(by_number(number).uuid)


def test_v3_findings_file_reads_as_numbers_and_writes_uuids() -> None:
    stored = read_findings_json(_findings_file(3, "M2"))
    assert stored.groups[MACRO][0].body.viewpoint == "M2"
    written = stored.to_json()
    assert written["format"] == 4
    assert written["groups"]["macro"][0]["viewpoint"] == _uuid_of("M2")


def test_v3_history_file_reads_as_numbers_and_writes_uuids() -> None:
    history = read_history_json(_history_file(3, "M2"))
    assert history.groups[MACRO][0].findings[0].body.viewpoint == "M2"
    written = history.to_json()
    assert written["format"] == 4
    assert written["groups"]["macro"][0]["findings"][0]["viewpoint"] == _uuid_of("M2")


def test_v4_files_round_trip_through_numbers() -> None:
    findings = _findings_file(4, _uuid_of("D1"), group="micro")
    stored = read_findings_json(findings)
    assert stored.groups[MICRO][0].body.viewpoint == "D1"
    assert stored.to_json()["groups"] == findings["groups"]
    history = _history_file(4, _uuid_of("M2"))
    assert read_history_json(history).to_json() == history


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize("viewpoint", ["discarded", ""])
def test_discarded_and_missing_viewpoints_pass_through_unchanged(
    version: int, viewpoint: str
) -> None:
    stored = read_findings_json(_findings_file(version, viewpoint))
    assert stored.groups[MACRO][0].body.viewpoint == viewpoint
    assert stored.to_json()["groups"]["macro"][0]["viewpoint"] == viewpoint
    history = read_history_json(_history_file(version, viewpoint))
    assert history.groups[MACRO][0].findings[0].body.viewpoint == viewpoint
    written = history.to_json()
    assert written["groups"]["macro"][0]["findings"][0]["viewpoint"] == viewpoint


def test_reading_does_not_touch_the_input() -> None:
    findings = _findings_file(4, _uuid_of("M2"))
    history = _history_file(4, _uuid_of("M2"))
    before = copy.deepcopy((findings, history))
    read_findings_json(findings)
    read_history_json(history)
    assert (findings, history) == before


def test_an_old_number_outside_the_table_fails_naming_the_value() -> None:
    with pytest.raises(ValueError, match="'X9'"):
        read_findings_json(_findings_file(3, "X9"))
    with pytest.raises(ValueError, match="'X9'"):
        read_history_json(_history_file(3, "X9"))


def test_an_unknown_uuid_fails_naming_the_value() -> None:
    unknown = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError, match=unknown):
        read_findings_json(_findings_file(4, unknown))
    with pytest.raises(ValueError, match=unknown):
        read_history_json(_history_file(4, unknown))


def test_a_finding_dict_keeps_the_display_number() -> None:
    stored = read_findings_json(_findings_file(4, _uuid_of("M2")))
    assert stored.groups[MACRO][0].to_json()["viewpoint"] == "M2"


def test_a_number_with_its_table_name_reads_as_that_number() -> None:
    stored = read_findings_json(_findings_file(3, "M4 利用者に向けたインターフェース"))
    assert stored.groups[MACRO][0].body.viewpoint == "M4"
    assert stored.to_json()["groups"]["macro"][0]["viewpoint"] == _uuid_of("M4")


def test_a_number_with_a_different_name_fails_naming_the_value() -> None:
    with pytest.raises(ValueError, match="M4 違う名前"):
        read_findings_json(_findings_file(3, "M4 違う名前"))


def test_v3_legacy_number_is_read_as_the_renumbered_viewpoint() -> None:
    stored = read_findings_json(_findings_file(3, "D6", group="micro"))
    assert stored.groups[MICRO][0].body.viewpoint == "D5"
    assert stored.to_json()["groups"]["micro"][0]["viewpoint"] == _uuid_of("D5")
