"""プロセスの有無を確かめられないとき（Windows）の表示。OS に依らず、判定を差し替えて確かめる。"""

import json
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.findings.records import IN_PROGRESS_FILE
from jigane.reviews.lifecycle import create_feature_review, load_review, resolve_review
from jigane.skills import run_record
from jigane.skills.run_record import PRESENCE_UNKNOWN_NOTE

STARTED = {
    "group": "macro",
    "started_at": "2026-10-20T10:00:00+00:00",
    "model": "m",
    "effort": None,
    "max_budget_usd": 1.0,
    "pid": 1,
}


@pytest.fixture
def review_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return str(create_feature_review(root, ["src"], "p").id)


def _unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run_record, "process_presence", lambda _pid: run_record.ProcessPresence.UNKNOWN
    )


def _record(review_id: str) -> None:
    directory = load_review(resolve_review(review_id)).id.directory()
    (directory / IN_PROGRESS_FILE).write_text(
        json.dumps({"format": run_record.IN_PROGRESS_FORMAT, "macro": STARTED}), encoding="utf-8"
    )


def test_show_says_presence_is_unknown_when_a_record_remains(
    review_id: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _record(review_id)
    _unknown(monkeypatch)
    capsys.readouterr()

    assert main(["show", review_id, "macro"]) == 0
    out = capsys.readouterr().out
    assert PRESENCE_UNKNOWN_NOTE in out
    assert "実行中（" not in out
    assert "止まった実行" not in out

    assert main(["show", review_id, "macro", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["in_progress"]["presence"] == "unknown"
    assert body["in_progress"]["note"] == PRESENCE_UNKNOWN_NOTE
    assert "presence_unknown" not in body
    assert body["stopped_runs"] == []


def test_no_record_leaves_in_progress_null(
    review_id: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _unknown(monkeypatch)
    capsys.readouterr()

    assert main(["show", review_id, "macro", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["in_progress"] is None
