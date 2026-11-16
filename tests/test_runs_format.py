"""runs.json の形の版: 読む境で今の版以外を止め、書く境で今の版を刻む。"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jigane.llm_runs import claude
from jigane.llm_runs.cost import Measured
from jigane.llm_runs.launch_failure import DiscoveryLaunchFailed
from jigane.skills import pipeline
from jigane.skills.pipeline import RUNS_FORMAT, dump_runs, load_runs, read_runs, write_runs
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import OlderFormatError

STORE = Path(__file__).parent / "fixtures" / "store"

ROW: dict[str, Any] = {
    "role": "discovery",
    "split_index": 0,
    "input_tokens": 1,
    "output_tokens": 1,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "duration_ms": 1,
    "num_turns": 1,
    "is_error": False,
    "terminal_reason": None,
    "permission_denials": 0,
    "started_at": "2026-11-01T00:00:00+00:00",
    "cost_usd": 0.25,
    "cost_source": "measured",
}
FAILURE: dict[str, Any] = {
    "stage": "discovery",
    "index": 0,
    "role": "discovery",
    "kind": "timeout",
    "reason": "r",
    "attempt": 1,
    "disposition": "not_rerun",
    "cost_usd": 0.0,
    "cost_source": "measured",
    "scope": "launch",
    "round": "current",
    "ran_at": None,
}
MISSING: dict[str, Any] = {
    **FAILURE,
    "stage": "verification",
    "role": "judge",
    "scope": "missing_candidates",
    "candidates": [],
}

CURRENT_SHAPES: dict[str, dict[str, Any]] = {
    "macro": {
        "macro": {
            "record_kind": "succeeded",
            "discovered": 1,
            "invocations": [ROW],
        }
    },
    "micro": {"micro": {"discovered": 1, "invocations": [ROW], "launch_failures": [FAILURE]}},
    "security": {"security": {"launch_failures": [MISSING]}},
    "empty": {},
}


def _stamped(shape: str) -> dict[str, Any]:
    return {"format": RUNS_FORMAT, **CURRENT_SHAPES[shape]}


def test_load_reads_the_stored_values() -> None:
    (micro,) = load_runs(_stamped("micro")).groups.values()
    assert isinstance(micro, pipeline.ShardedGroupRun)
    assert micro.invocations is not None
    assert micro.invocations[0].record.cost == Measured(0.25)
    assert micro.launch_failures is not None
    (failure,) = micro.launch_failures
    assert isinstance(failure, DiscoveryLaunchFailed)


@pytest.mark.parametrize("shape", CURRENT_SHAPES)
def test_write_then_read_round_trips(shape: str, tmp_path: Path) -> None:
    runs = dict(load_runs(_stamped(shape)).groups)
    write_runs(tmp_path, runs)
    stored = json.loads((tmp_path / "runs.json").read_text("utf-8"))
    assert stored["format"] == RUNS_FORMAT
    assert read_runs(tmp_path) == runs
    assert dump_runs(read_runs(tmp_path)) == stored


@pytest.mark.parametrize("raw", [{}, {"micro": {"discovered": 1}}, {"format": 1}, {"format": 2}])
def test_older_format_stops_with_the_rebuild_hint(raw: dict[str, Any]) -> None:
    with pytest.raises(OlderFormatError, match="jigane new") as error:
        load_runs(raw)
    assert "引き継がれません" in str(error.value)
    assert "runs.json" in str(error.value)


def test_newer_format_still_asks_for_an_update() -> None:
    with pytest.raises(ReviewError, match="より新しいバージョンの jigane") as error:
        load_runs({"format": RUNS_FORMAT + 1})
    assert not isinstance(error.value, OlderFormatError)


@pytest.mark.parametrize("version", ["2", 2.0, True, None, 0, -1])
def test_invalid_version_stops_with_a_review_error(version: object) -> None:
    with pytest.raises(ReviewError, match=r"runs\.json") as error:
        load_runs({"format": version, "micro": {}})
    assert not isinstance(error.value, OlderFormatError)


def test_every_stored_fixture_with_runs_is_an_older_format() -> None:
    # 読めないと分けた素材は、止まる版の素材ではない
    sources = [
        source
        for source in STORE.glob("*/*/*/*/*/runs.json")
        if source.relative_to(STORE).parts[0] != "unreadable"
    ]
    assert sources, f"{STORE} に runs.json の素材が無い"
    for source in sources:
        with pytest.raises(OlderFormatError, match="jigane new"):
            load_runs(json.loads(source.read_text("utf-8")))


def test_session_id_in_a_stored_row_is_discarded_on_the_next_write(tmp_path: Path) -> None:
    raw = {"format": RUNS_FORMAT, "micro": {"invocations": [{**ROW, "session_id": "abc"}]}}
    write_runs(tmp_path, dict(load_runs(raw).groups))
    stored = json.loads((tmp_path / "runs.json").read_text("utf-8"))
    assert "session_id" not in stored["micro"]["invocations"][0]


def test_started_at_is_written_and_read_back(tmp_path: Path) -> None:
    runs = dict(load_runs(_stamped("micro")).groups)
    write_runs(tmp_path, runs)
    stored = json.loads((tmp_path / "runs.json").read_text("utf-8"))
    assert stored["micro"]["invocations"][0]["started_at"] == ROW["started_at"]
    (micro,) = read_runs(tmp_path).values()
    assert isinstance(micro, pipeline.ShardedGroupRun)
    assert micro.invocations is not None
    assert micro.invocations[0].record.started_at == ROW["started_at"]


def test_now_iso_is_utc_seconds() -> None:
    started = datetime.fromisoformat(claude.now_iso())
    assert started.utcoffset() == timedelta(0)
    assert started.microsecond == 0


def test_row_without_started_at_is_unreadable() -> None:
    row = {key: value for key, value in ROW.items() if key != "started_at"}
    with pytest.raises(KeyError, match="started_at"):
        load_runs({"format": RUNS_FORMAT, "micro": {"invocations": [row]}})


@pytest.mark.parametrize("value", ["yesterday", "", None])
def test_row_with_invalid_started_at_is_unreadable(value: object) -> None:
    row = {**ROW, "started_at": value}
    with pytest.raises((ValueError, TypeError)):
        load_runs({"format": RUNS_FORMAT, "micro": {"invocations": [row]}})


def test_format_3_runs_json_stops_with_the_rebuild_hint() -> None:
    row = {key: value for key, value in ROW.items() if key != "started_at"}
    with pytest.raises(OlderFormatError, match="jigane new"):
        load_runs({"format": 3, "micro": {"invocations": [row]}})
