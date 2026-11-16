"""findings.json と findings_history.json の版の判別を確かめる。今の版以外は読まずに止める。"""

import json
from pathlib import Path
from typing import Any

import pytest

from jigane.findings.records import (
    FINDINGS_FILE,
    FINDINGS_FORMAT,
    HISTORY_FILE,
    load_findings_state,
    read_findings_json,
    read_history_json,
    read_stored_state,
    save_findings_state,
)
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import OlderFormatError

CURRENT_FINDING: dict[str, Any] = {
    "id": "F1",
    "severity": "minor",
    "viewpoint": "8ccf8da8-f298-4f5d-a524-f253c009dfdd",
    "claim": "主張",
    "triage": "remaining",
    "theme": None,
    "adjudication": None,
}


def current_state() -> dict[str, Any]:
    return {
        "format": FINDINGS_FORMAT,
        "last_number": 1,
        "groups": {"micro": [CURRENT_FINDING]},
        "stale_groups": [],
        "last_theme_number": 0,
        "themes": [],
    }


def test_current_form_reads_and_writes_back_to_the_same_dict() -> None:
    current = current_state()
    assert json.loads(json.dumps(read_findings_json(current).to_json())) == current


@pytest.mark.parametrize("version", [1, 2, 3])
def test_an_older_findings_file_stops_with_the_rebuild_hint(version: int) -> None:
    raw = {**current_state(), "format": version}
    with pytest.raises(OlderFormatError) as caught:
        read_findings_json(raw)
    message = str(caught.value)
    assert FINDINGS_FILE in message
    assert f"バージョンは {version}" in message
    assert "jigane new" in message
    assert "引き継がれません" in message


def test_a_findings_file_without_a_format_is_read_as_version_1_and_stops() -> None:
    with pytest.raises(OlderFormatError, match="バージョンは 1 "):
        read_findings_json({"groups": {"micro": [{"id": "F1"}]}})


@pytest.mark.parametrize("version", [1, 2, 3])
def test_an_older_history_file_stops_with_the_rebuild_hint(version: int) -> None:
    raw = {"format": version, "groups": {"micro": []}}
    with pytest.raises(OlderFormatError) as caught:
        read_history_json(raw)
    message = str(caught.value)
    assert HISTORY_FILE in message
    assert "jigane new" in message
    assert "引き継がれません" in message


def test_a_history_file_without_a_format_is_read_as_version_1_and_stops() -> None:
    with pytest.raises(OlderFormatError):
        read_history_json({"groups": {}})


def test_an_older_findings_file_on_disk_stops_through_the_stored_state_reader(
    tmp_path: Path,
) -> None:
    (tmp_path / FINDINGS_FILE).write_text(json.dumps({"groups": {}}), encoding="utf-8")
    with pytest.raises(OlderFormatError):
        read_stored_state(tmp_path)
    with pytest.raises(OlderFormatError):
        load_findings_state(tmp_path)


@pytest.mark.parametrize("version", [FINDINGS_FORMAT + 1, 99])
def test_a_newer_version_keeps_the_update_message_and_is_not_an_older_format_error(
    version: int,
) -> None:
    raw = {"format": version, "last_number": 0, "groups": {}}
    with pytest.raises(ReviewError, match="新しいバージョンの jigane") as caught:
        read_findings_json(raw)
    assert not isinstance(caught.value, OlderFormatError)


@pytest.mark.parametrize("version", [0, "3", 3.0, True, None])
def test_a_broken_version_keeps_its_message_and_is_not_an_older_format_error(
    version: object,
) -> None:
    raw = {"format": version, "last_number": 0, "groups": {}}
    with pytest.raises(ReviewError, match=FINDINGS_FILE) as caught:
        read_findings_json(raw)
    assert not isinstance(caught.value, OlderFormatError)


def test_current_form_skips_the_upgrade() -> None:
    # 今の版のファイルは、鍵が欠けていても補わず、欠けたまま失敗する
    with pytest.raises(KeyError):
        read_findings_json({"format": FINDINGS_FORMAT, "last_number": 0, "groups": {}})


def test_saving_writes_the_current_version_and_reading_does_not_write_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / FINDINGS_FILE
    path.write_text(json.dumps(current_state()), encoding="utf-8")
    original = path.read_text(encoding="utf-8")
    state = read_stored_state(tmp_path)
    load_findings_state(tmp_path)
    assert path.read_text(encoding="utf-8") == original
    save_findings_state(tmp_path, state)
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == FINDINGS_FORMAT
