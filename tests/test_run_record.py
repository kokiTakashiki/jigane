import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from run_record_support import dead_pid, read, settings

from jigane.commands import main
from jigane.findings.records import IN_PROGRESS_FILE
from jigane.groups.definitions import MACRO, MICRO
from jigane.llm_runs import claude
from jigane.llm_runs.cost import Measured
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import run_record
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import OlderFormatError

pytestmark = pytest.mark.usefixtures("selected_agent")


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


def test_interrupt_keeps_stopped_record_and_previous_runs(review: Review) -> None:
    directory = review.id.directory()
    (directory / "runs.json").write_text('{"macro": {"ran_at": "before"}}', encoding="utf-8")
    with pytest.raises(KeyboardInterrupt), run_record.tracking(review, MACRO, settings()):
        raise KeyboardInterrupt
    stopped = read(review, run_record.STOPPED_FILE)["macro"]
    assert "cost_usd" not in stopped[0]
    assert stopped[0]["kind"] == "interrupt"
    assert stopped[0]["stopped_at"] is not None
    assert stopped[0]["pid"] == os.getpid()
    assert read(review, IN_PROGRESS_FILE) == {"format": 2}
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
        started_at="2026-11-01T00:00:00+00:00",
        max_budget_usd=0.5,
    )
    tracked_settings = settings(lambda _: claude.InvocationResult(None, record))

    def interrupted() -> None:
        with run_record.tracking(review, MICRO, tracked_settings) as tracked:
            tracked.invoke(None)  # type: ignore[arg-type]
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        interrupted()
    stopped = read(review, run_record.STOPPED_FILE)["micro"]
    assert stopped[0]["completed_invocation_costs"] == [0.25]
    assert "completed_cost_usd" not in stopped[0]


def test_normal_end_and_failure_clear_in_progress(review: Review) -> None:
    with run_record.tracking(review, MACRO, settings()):
        assert "macro" in read(review, IN_PROGRESS_FILE)
    assert read(review, IN_PROGRESS_FILE) == {"format": 2}
    with (
        pytest.raises(ValueError, match="failed"),
        run_record.tracking(review, MACRO, settings()),
    ):
        raise ValueError("failed")
    assert read(review, IN_PROGRESS_FILE) == {"format": 2}
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
        "pid": dead_pid(),
    }
    path = directory / IN_PROGRESS_FILE
    path.write_text(
        json.dumps({"format": run_record.IN_PROGRESS_FORMAT, "macro": started}), encoding="utf-8"
    )
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


def _older_stored() -> list[tuple[Callable[[Mapping[str, Any]], object], dict[str, Any]]]:
    return [
        (run_record.load_in_progress, {"macro": {"group": "macro", **V1_START, "cost_usd": None}}),
        (run_record.load_in_progress, {"format": 1, "macro": {"group": "macro", **V1_START}}),
        (run_record.load_stopped, {"macro": []}),
        (run_record.load_stopped, {"format": 1, "macro": []}),
        (run_record.load_stopped, {"format": 2, "macro": []}),
    ]


@pytest.mark.parametrize(("loader", "raw"), _older_stored())
def test_older_format_stops_with_the_rebuild_hint(
    loader: Callable[[Mapping[str, Any]], object], raw: dict[str, Any]
) -> None:
    with pytest.raises(OlderFormatError, match="jigane new") as error:
        loader(raw)
    assert "引き継がれません" in str(error.value)


@pytest.mark.parametrize("loader", [run_record.load_in_progress, run_record.load_stopped])
def test_newer_format_still_asks_for_an_update(
    loader: Callable[[Mapping[str, Any]], object],
) -> None:
    with pytest.raises(ReviewError, match="より新しいバージョンの jigane") as error:
        loader({"format": 99})
    assert not isinstance(error.value, OlderFormatError)


@pytest.mark.parametrize("loader", [run_record.load_in_progress, run_record.load_stopped])
@pytest.mark.parametrize("version", [0, -1, 4, "2", True, 1.5])
def test_bad_format_version_is_rejected(
    loader: Callable[[Mapping[str, Any]], object], version: object
) -> None:
    with pytest.raises(ReviewError, match=r"形の版|JSON の構造のバージョン"):
        loader({"format": version})
