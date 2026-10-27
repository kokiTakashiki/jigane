import json
import logging
from pathlib import Path
from typing import Any

import pytest
from review_fakes import history_rounds

from jigane.findings.records import (
    FINDINGS_FILE,
    HISTORY_FILE,
    load_findings_state,
    read_stored_state,
    save_findings_state,
    triaged_dicts,
)
from jigane.groups.definitions import MACRO, GroupId
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput


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


def _settled_nit(number: int) -> dict[str, Any]:
    return {
        "id": f"F{number}",
        "severity": "nit",
        "group": "macro",
        "viewpoint": "discarded",
        "claim": "直った論点",
        "body": "",
        "evidence": [],
        "adjudication": None,
        "theme": None,
        "triage": "remaining",
        "judgment_region": None,
        "nit_reason": {"kind": "settled_earlier", "text": "直った論点", "concern": None},
    }


def _history_raw() -> dict[str, Any]:
    return {"groups": {"macro": [{"ran_at": "t1", "findings": [_settled_nit(1), _finding(2)]}]}}


def test_history_with_settled_earlier_nit_is_not_corrupt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / HISTORY_FILE).write_text(json.dumps(_history_raw()), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        rounds = history_rounds(tmp_path, MACRO)
    assert "壊れている" not in caplog.text
    assert [f.body.id for f in rounds[0].findings] == ["F1", "F2"]
    assert rounds[0].findings[0].body.nit_reason is None
    rounds[0].to_json()


def test_findings_json_with_settled_earlier_nit_round_trips(tmp_path: Path) -> None:
    state = {"last_number": 2, "groups": {"macro": [_settled_nit(1), _finding(2)]}}
    (tmp_path / FINDINGS_FILE).write_text(json.dumps(state), encoding="utf-8")
    assert (
        "nit_reason"
        not in triaged_dicts(load_findings_state(tmp_path).groups[GroupId("macro")])[0]
    )
    save_findings_state(tmp_path, read_stored_state(tmp_path))
    saved = json.loads((tmp_path / FINDINGS_FILE).read_text(encoding="utf-8"))
    assert all("nit_reason" not in f for f in saved["groups"]["macro"])
    assert triaged_dicts(load_findings_state(tmp_path).groups[GroupId("macro")])[1]["id"] == "F2"


def test_legacy_discard_kind_settled_earlier_loads(tmp_path: Path) -> None:
    legacy = _settled_nit(1)
    del legacy["nit_reason"]
    legacy.update({"discard_kind": "settled_earlier", "concern": None})
    (tmp_path / FINDINGS_FILE).write_text(
        json.dumps({"last_number": 1, "groups": {"macro": [legacy]}}), encoding="utf-8"
    )
    assert (
        "nit_reason"
        not in triaged_dicts(load_findings_state(tmp_path).groups[GroupId("macro")])[0]
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_history_text_lists_other_findings_beside_settled_earlier_nit(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    (review.id.directory() / HISTORY_FILE).write_text(json.dumps(_history_raw()), encoding="utf-8")
    assert "F2" in macro.history_text(AssemblyInput.load(review))
