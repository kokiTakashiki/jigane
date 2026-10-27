"""findings.json の版の判別と、版 1 から今の版への移しを確かめる。"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from store_format_support import check_upgrades

from jigane.findings.records import (
    _HISTORY_UPGRADES,
    _UPGRADES,
    FINDINGS_FILE,
    FINDINGS_FORMAT,
    HISTORY_FILE,
    _upgrade_finding_v1,
    _upgrade_finding_v2,
    _upgrade_findings_v1,
    _upgrade_findings_v2,
    _upgrade_history_v2,
    load_findings_state,
    read_findings_json,
    read_history_json,
    read_stored_state,
    save_findings_state,
)
from jigane.storage.review_error import ReviewError

TOP_KEYS = {"format", "last_number", "groups", "stale_groups", "last_theme_number", "themes"}
FINDING_KEYS = {"id", "severity", "viewpoint", "claim", "triage", "theme", "adjudication"}
THEME_KEYS = {"id", "title", "finding_ids", "severity", "triage"}

REBUTTAL: dict[str, Any] = {
    "kind": "rebuttal",
    "challenge": "c",
    "verdict": "kept",
    "reason": "r",
    "recheck": [],
}
V1_FINDINGS: dict[str, dict[str, Any]] = {
    "minimal": {"id": "F1"},
    "adjudication_null": {"id": "F1", "adjudication": None},
    "rebuttal": {"id": "F1", "adjudication": REBUTTAL},
    "rebuttal_with_tool_claim": {
        "id": "F1",
        "adjudication": {
            **REBUTTAL,
            "verified": True,
            "tool_claim": {"relies": False, "basis": "none", "version": ""},
        },
    },
    "alternative": {
        "id": "F1",
        "adjudication": {
            "kind": "alternative",
            "alternative": "a",
            "defense": "d",
            "verdict": "kept",
            "reason": "r",
        },
    },
    "discard_kind": {
        "id": "F1",
        "severity": "nit",
        "viewpoint": "discarded",
        "claim": "A",
        "discard_kind": "other",
        "concern": "C",
    },
}


def v1_state(finding: dict[str, Any], top: str = "full") -> dict[str, Any]:
    state: dict[str, Any] = {"groups": {"micro": [finding]}}
    if top != "groups_only":
        state["last_number"] = 1
    if top == "full":
        state |= {
            "stale_groups": [],
            "last_theme_number": 1,
            "themes": [{"id": "B1", "finding_ids": ["F1"]}],
        }
    return state


@pytest.mark.parametrize("name", V1_FINDINGS)
@pytest.mark.parametrize("top", ["groups_only", "with_last_number", "full"])
def test_v1_form_moves_to_the_key_set_the_writer_writes(name: str, top: str) -> None:
    upgraded = _upgrade_findings_v1(v1_state(V1_FINDINGS[name], top))
    assert set(upgraded) == TOP_KEYS
    assert upgraded["format"] == 2
    finding = upgraded["groups"]["micro"][0]
    assert set(finding) >= FINDING_KEYS
    assert not {"discard_kind", "concern"} & set(finding)
    if top == "full":
        assert set(upgraded["themes"][0]) == THEME_KEYS


def test_groups_only_form_takes_the_next_number_from_the_findings() -> None:
    state = {"groups": {"micro": [{"id": "F4"}], "macro": [{"id": "F9"}]}}
    assert _upgrade_findings_v1(state)["last_number"] == 9


def test_upgrade_does_not_touch_the_input() -> None:
    raw = v1_state(V1_FINDINGS["discard_kind"])
    before = copy.deepcopy(raw)
    _upgrade_findings_v1(raw)
    assert raw == before


@pytest.mark.parametrize("name", V1_FINDINGS)
def test_current_form_reads_and_writes_back_to_the_same_dict(name: str) -> None:
    current = _upgrade_findings_v2(_upgrade_findings_v1(v1_state(V1_FINDINGS[name])))
    assert json.loads(json.dumps(read_findings_json(current).to_json())) == current


def test_current_form_skips_the_upgrade() -> None:
    # 版 3 のファイルは、鍵が欠けていても補わず、欠けたまま失敗する
    with pytest.raises(KeyError):
        read_findings_json({"format": 3, "last_number": 0, "groups": {}})


@pytest.mark.parametrize("version", [0, 4, 99, "3", 3.0, True, None])
def test_an_unsupported_version_fails_with_a_clear_error(version: object) -> None:
    raw = {"format": version, "last_number": 0, "groups": {}}
    with pytest.raises(ReviewError, match=FINDINGS_FILE):
        read_findings_json(raw)


def test_saving_writes_the_current_version_and_reading_does_not_write_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / FINDINGS_FILE
    path.write_text(json.dumps(v1_state(V1_FINDINGS["minimal"])), encoding="utf-8")
    original = path.read_text(encoding="utf-8")
    state = read_stored_state(tmp_path)
    load_findings_state(tmp_path)
    assert path.read_text(encoding="utf-8") == original
    save_findings_state(tmp_path, state)
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == FINDINGS_FORMAT


def test_every_older_version_has_an_upgrade() -> None:
    assert set(_UPGRADES) == set(range(1, FINDINGS_FORMAT))


def test_upgrade_tables_have_every_step() -> None:
    check_upgrades(FINDINGS_FILE, FINDINGS_FORMAT, _UPGRADES)
    check_upgrades(HISTORY_FILE, FINDINGS_FORMAT, _HISTORY_UPGRADES)


def v2_reason(kind: str, concern: str | None) -> dict[str, Any]:
    return {"kind": kind, "text": "元の文", "concern": concern}


def v2_nit(reason: dict[str, Any] | None) -> dict[str, Any]:
    finding: dict[str, Any] = {"id": "F1", "severity": "nit", "viewpoint": "D1", "claim": "主張"}
    if reason is not None:
        finding["nit_reason"] = reason
    return finding


def test_v2_disclosed_limitation_splits_the_claim_and_the_places() -> None:
    reason = v2_reason("disclosed_limitation", "主張（補足）（開示の場所: a.md の節、b.md の節）")
    moved = _upgrade_finding_v2(v2_nit(reason))["nit_reason"]
    assert moved == {
        "kind": "disclosed_limitation",
        "text": "元の文",
        "concern": "主張（補足）",
        "places": ["a.md の節", "b.md の節"],
    }


@pytest.mark.parametrize(
    "concern",
    [
        None,
        "主張だけ",
        "主張（開示の場所: ）",
        "主張（開示の場所: 、）",
        "主張（開示の場所: a）後ろ",
    ],
)
def test_v2_disclosed_limitation_without_a_readable_place_keeps_its_kind_with_empty_places(
    concern: str | None,
) -> None:
    moved = _upgrade_finding_v2(v2_nit(v2_reason("disclosed_limitation", concern)))["nit_reason"]
    assert moved == {
        "kind": "disclosed_limitation",
        "text": "元の文",
        "concern": concern,
        "places": [],
    }


def test_v2_other_kinds_get_empty_places_and_a_missing_reason_is_left() -> None:
    reason = v2_reason("not_a_contract", "懸念（開示の場所: x）")
    moved = _upgrade_finding_v2(v2_nit(reason))["nit_reason"]
    assert moved == {**reason, "places": []}
    assert "nit_reason" not in _upgrade_finding_v2(v2_nit(None))


def test_v2_finding_upgrade_does_not_touch_the_input() -> None:
    finding = v2_nit(v2_reason("disclosed_limitation", "主張（開示の場所: a）"))
    before = copy.deepcopy(finding)
    _upgrade_finding_v2(finding)
    assert finding == before


V2_DISCLOSED = _upgrade_finding_v1(
    v2_nit(v2_reason("disclosed_limitation", "主張（開示の場所: a.md、b.md）"))
)
V3_REASON = {
    "kind": "disclosed_limitation",
    "text": "元の文",
    "concern": "主張",
    "places": ["a.md", "b.md"],
}


def test_findings_file_v2_moves_to_v3_through_the_same_finding_upgrade() -> None:
    raw = _upgrade_findings_v1({"groups": {"micro": [V2_DISCLOSED]}})
    stored = read_findings_json(raw)
    assert stored.to_json()["groups"]["micro"][0]["nit_reason"] == V3_REASON
    assert stored.to_json()["format"] == 3


def test_history_file_v2_moves_to_v3_through_the_same_finding_upgrade() -> None:
    entry = {"ran_at": "t", "findings": [V2_DISCLOSED], "discard": None}
    raw = {"format": 2, "groups": {"micro": [entry]}}
    moved = _upgrade_history_v2(raw)
    assert moved["format"] == 3
    assert moved["groups"]["micro"][0]["findings"][0]["nit_reason"] == V3_REASON
    assert (
        read_history_json(raw).to_json()["groups"]["micro"][0]["findings"][0]["nit_reason"]
        == V3_REASON
    )
