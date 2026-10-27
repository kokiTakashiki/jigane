"""os.kill(pid, 0) と SIGTERM に頼る、実行の記録の試験。POSIX だけの置き場に置く。

既定の testpaths の外に置き、macOS と Linux のゲート（Makefile の test）だけが走らせる。
Windows のゲート（justfile の test）は走らせない。置き場の違いで走る OS が決まる。
"""

import json
import os
import signal

import pytest
from test_run_record import _dead_pid, _read, _settings

from jigane.commands.views.findings import stopped_lines
from jigane.findings.records import IN_PROGRESS_FILE
from jigane.groups.definitions import MACRO
from jigane.reviews.lifecycle import Review
from jigane.skills import run_record


def test_process_presence_reports_present_and_absent_by_signal_zero() -> None:
    assert run_record.process_presence(os.getpid()) is run_record.ProcessPresence.PRESENT
    assert run_record.process_presence(_dead_pid()) is run_record.ProcessPresence.ABSENT


def test_dead_process_in_progress_shows_as_stopped(review: Review) -> None:
    directory = review.id.directory()
    started = {
        "group": "macro",
        "started_at": "2026-10-20T10:00:00+00:00",
        "model": "m",
        "effort": None,
        "max_budget_usd": 1.0,
        "pid": _dead_pid(),
    }
    (directory / IN_PROGRESS_FILE).write_text(json.dumps({"macro": started}), encoding="utf-8")
    view = run_record.stopped_view(directory, MACRO)
    assert view.in_progress is None
    assert [(e.kind, e.stopped_at) for e in view.stopped_runs] == [
        (run_record.StopKind.VANISHED, None)
    ]
    assert stopped_lines(view) == [
        "止まった実行（強制終了、起動 2026-10-20T10:00:00+00:00、止めた 不明、金額は不明）"
    ]
    live = {**started, "pid": os.getpid()}
    (directory / IN_PROGRESS_FILE).write_text(json.dumps({"macro": live}), encoding="utf-8")
    alive_view = run_record.stopped_view(directory, MACRO)
    assert alive_view.stopped_runs == ()
    assert alive_view.in_progress is not None
    assert alive_view.in_progress.record.start.pid == os.getpid()
    assert alive_view.in_progress.presence is run_record.LivePresence.PRESENT


def test_sigterm_is_recorded_as_terminate(review: Review) -> None:
    def terminated() -> None:
        with run_record.tracking(review, MACRO, _settings()):
            os.kill(os.getpid(), signal.SIGTERM)

    with pytest.raises(KeyboardInterrupt):
        terminated()
    [stopped] = _read(review, run_record.STOPPED_FILE)["macro"]
    assert stopped["kind"] == "terminate"
    assert stopped["stopped_at"] is not None
