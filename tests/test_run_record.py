import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from dev_launch import run_tool

from jigane.commands import main
from jigane.findings.records import IN_PROGRESS_FILE
from jigane.groups.definitions import MACRO, MICRO
from jigane.llm_runs import claude
from jigane.llm_runs.cost import Measured
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import run_record
from jigane.storage.review_error import ReviewError


def _settings(invoke: claude.Invoke | None = None) -> claude.RunSettings:
    def refuse(_invocation: claude.Invocation) -> claude.InvocationResult:
        raise AssertionError("LLM は呼ばない")

    return claude.RunSettings(
        budget=claude.RunBudget("m", 1.0, reserve_margin=True),
        effort=None,
        invoke=invoke or refuse,
    )


def _dead_pid() -> int:
    # 終わったプロセスの番号。直後に別のプロセスが同じ番号を得る可能性は無視できる
    return int(run_tool([sys.executable, "-c", "import os; print(os.getpid())"]))


@pytest.fixture
def review(tmp_path: Path) -> Review:
    class FakeId:
        def directory(self) -> Path:
            path = tmp_path / "review"
            path.mkdir(exist_ok=True)
            return path

    class FakeReview:
        id = FakeId()

    return FakeReview()  # type: ignore[return-value]


def _read(review: Review, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((review.id.directory() / name).read_text("utf-8"))
    return data


def test_interrupt_keeps_stopped_record_and_previous_runs(review: Review) -> None:
    directory = review.id.directory()
    (directory / "runs.json").write_text('{"macro": {"ran_at": "before"}}', encoding="utf-8")
    with pytest.raises(KeyboardInterrupt), run_record.tracking(review, MACRO, _settings()):
        raise KeyboardInterrupt
    stopped = _read(review, run_record.STOPPED_FILE)["macro"]
    assert "cost_usd" not in stopped[0]
    assert stopped[0]["kind"] == "interrupt"
    assert stopped[0]["stopped_at"] is not None
    assert stopped[0]["pid"] == os.getpid()
    assert _read(review, IN_PROGRESS_FILE) == {"format": 2}
    assert json.loads((directory / "runs.json").read_text("utf-8")) == {
        "macro": {"ran_at": "before"}
    }


def test_completed_invocation_costs_are_attached(review: Review) -> None:
    record = claude.InvocationRecord(
        1,
        1,
        0,
        0,
        Measured(0.25),
        1,
        1,
        is_error=False,
        terminal_reason=None,
        permission_denials=0,
        max_budget_usd=0.5,
    )
    settings = _settings(lambda _: claude.InvocationResult(None, record))

    def interrupted() -> None:
        with run_record.tracking(review, MICRO, settings) as tracked:
            tracked.invoke(None)  # type: ignore[arg-type]
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        interrupted()
    stopped = _read(review, run_record.STOPPED_FILE)["micro"]
    assert stopped[0]["completed_invocation_costs"] == [0.25]
    assert "completed_cost_usd" not in stopped[0]


def test_normal_end_and_failure_clear_in_progress(review: Review) -> None:
    with run_record.tracking(review, MACRO, _settings()):
        assert "macro" in _read(review, IN_PROGRESS_FILE)
    assert _read(review, IN_PROGRESS_FILE) == {"format": 2}
    with (
        pytest.raises(ValueError, match="failed"),
        run_record.tracking(review, MACRO, _settings()),
    ):
        raise ValueError("failed")
    assert _read(review, IN_PROGRESS_FILE) == {"format": 2}
    assert not (review.id.directory() / run_record.STOPPED_FILE).exists()


def test_process_presence_is_unknown_where_signal_zero_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    assert run_record.process_presence(os.getpid()) is run_record.ProcessPresence.UNKNOWN


def test_undeterminable_process_keeps_its_record_without_stopping_it(
    review: Review, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = review.id.directory()
    started = {
        "group": "macro",
        "started_at": "2026-10-20T10:00:00+00:00",
        "model": "m",
        "effort": None,
        "max_budget_usd": 1.0,
        "pid": _dead_pid(),
    }
    path = directory / IN_PROGRESS_FILE
    path.write_text(json.dumps({"macro": started}), encoding="utf-8")
    monkeypatch.setattr(
        run_record, "process_presence", lambda _pid: run_record.ProcessPresence.UNKNOWN
    )
    view = run_record.stopped_view(directory, MACRO)
    assert view.stopped_runs == ()
    assert view.in_progress is not None
    assert view.in_progress.presence is run_record.LivePresence.UNKNOWN
    run_record._settle_dead(directory, MACRO)
    assert "macro" in json.loads(path.read_text(encoding="utf-8"))
    assert not (directory / run_record.STOPPED_FILE).exists()


def test_stopped_at_is_none_only_for_vanished() -> None:
    start = run_record.RunStart(started_at="s", model="m", effort=None, max_budget_usd=1.0, pid=1)
    kwargs: dict[str, Any] = {"start": start, "completed_invocation_costs": ()}
    for kind in (run_record.StopKind.INTERRUPT, run_record.StopKind.TERMINATE):
        with pytest.raises(ValueError, match="vanished"):
            run_record.StoppedRun(kind=kind, stopped_at=None, **kwargs)
    with pytest.raises(ValueError, match="vanished"):
        run_record.StoppedRun(kind=run_record.StopKind.VANISHED, stopped_at="t", **kwargs)


def test_old_shape_stopped_runs_are_read(review: Review) -> None:
    directory = review.id.directory()
    base = {
        "started_at": "2026-10-20T10:00:00+00:00",
        "model": "m",
        "effort": None,
        "max_budget_usd": 1.0,
        "pid": 7,
        "cost_usd": None,
        "completed_invocation_costs": [0.5],
        "completed_cost_usd": 0.5,
    }
    old = [
        {**base, "stopped_at": "2026-10-20T10:05:00+00:00"},
        {**base, "stopped_at": None, "completed_invocation_costs": [], "completed_cost_usd": None},
    ]
    (directory / run_record.STOPPED_FILE).write_text(json.dumps({"macro": old}), encoding="utf-8")
    view = run_record.stopped_view(directory, MACRO)
    assert [run.kind for run in view.stopped_runs] == [
        run_record.StopKind.INTERRUPT,
        run_record.StopKind.VANISHED,
    ]
    assert view.stopped_runs[0].completed_cost_usd == 0.5
    assert view.to_json()["stopped_runs"][0]["kind"] == "interrupt"


def test_interrupted_cli_run_shows_up_in_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    review_id = str(create_feature_review(root, ["src"], "p").id)

    def interrupt(_invocation: claude.Invocation) -> claude.InvocationResult:
        raise KeyboardInterrupt

    # CLI は割り込みを受けて、シェルの慣習どおり 130 で終わる
    assert main(["run", review_id, "macro"], invoke=interrupt) == 130
    capsys.readouterr()
    assert main(["show", review_id, "macro"]) == 0
    assert "止まった実行（割り込み、起動 " in capsys.readouterr().out
    assert main(["show", review_id, "macro", "--json"]) == 0
    [stopped] = json.loads(capsys.readouterr().out)["stopped_runs"]
    assert stopped["kind"] == "interrupt"
    assert stopped["stopped_at"] is not None


V1_START = {
    "started_at": "2026-10-20T10:00:00+00:00",
    "model": "m",
    "effort": None,
    "max_budget_usd": 1.0,
    "pid": 7,
}


def test_in_progress_v1_upgrades_to_current_keys() -> None:
    old = {"macro": {"group": "macro", **V1_START, "cost_usd": None}}
    loaded = run_record.load_in_progress(old)
    dumped = run_record.dump_in_progress(loaded)
    assert dumped["format"] == run_record.IN_PROGRESS_FORMAT
    assert set(dumped["macro"]) == {"group", *V1_START}


def test_stopped_v1_upgrades_to_current_keys() -> None:
    old = {
        "macro": [
            {
                **V1_START,
                "stopped_at": "2026-10-20T10:05:00+00:00",
                "cost_usd": None,
                "completed_invocation_costs": [0.5],
                "completed_cost_usd": 0.5,
            }
        ]
    }
    dumped = run_record.dump_stopped(run_record.load_stopped(old))
    assert dumped["format"] == run_record.STOPPED_FORMAT
    assert set(dumped["macro"][0]) == {
        "kind",
        "stopped_at",
        "completed_invocation_costs",
        *V1_START,
    }
    assert run_record.load_stopped(dumped) == run_record.load_stopped(old)


@pytest.mark.parametrize("loader", [run_record.load_in_progress, run_record.load_stopped])
@pytest.mark.parametrize("version", [0, -1, 4, "2", True, 1.5])
def test_bad_format_version_is_rejected(
    loader: Callable[[Mapping[str, Any]], object], version: object
) -> None:
    with pytest.raises(ReviewError, match="形の版"):
        loader({"format": version})


def test_stopped_v2_drops_the_derivable_cost_sum() -> None:
    old: dict[str, Any] = {
        "macro": [
            {
                "kind": "interrupt",
                **V1_START,
                "stopped_at": "2026-10-20T10:05:00+00:00",
                "completed_invocation_costs": [0.5],
                "completed_cost_usd": 0.5,
            }
        ],
        "format": 2,
    }
    loaded = run_record.load_stopped(old)
    [run] = loaded[MACRO]
    assert "completed_cost_usd" not in run_record.dump_stopped(loaded)["macro"][0]
    assert run.completed_cost_usd == 0.5
    assert run.to_json()["completed_cost_usd"] == 0.5
    # 入力は書き換えない
    assert "completed_cost_usd" in old["macro"][0]
