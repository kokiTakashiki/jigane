"""runs.json の形の版: 読む境で古い版を今の版へ移し、書く境で今の版を刻む。"""

import json
from pathlib import Path
from typing import Any

import pytest
from store_format_support import check_upgrades

from jigane.llm_runs.cost import Measured
from jigane.llm_runs.launch_failure import LEGACY_FULL_SHAPE_ROLE, DiscoveryLaunchFailed
from jigane.skills import pipeline
from jigane.skills.pipeline import (
    _UPGRADES,
    RUNS_FORMAT,
    dump_runs,
    load_runs,
    read_runs,
    write_runs,
)
from jigane.storage.review_error import ReviewError

STORE = Path(__file__).parent / "fixtures" / "store"

ROW_V1: dict[str, Any] = {
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
    "cost_usd": 0.25,
}
FAILURE_V1: dict[str, Any] = {
    "stage": "discovery",
    "index": 0,
    "role": None,
    "kind": "timeout",
    "reason": "r",
    "attempt": 1,
    "disposition": "not_rerun",
    "cost_usd": 0.0,
}
MISSING_V1: dict[str, Any] = {
    **FAILURE_V1,
    "stage": "verification",
    "role": "judge",
    "scope": "missing_candidates",
}

MINIMAL_V1_SHAPES: dict[str, dict[str, Any]] = {
    "macro": {"macro": {"discovered": 1, "invocations": [ROW_V1]}},
    "micro": {
        "micro": {"discovered": 1, "invocations": [ROW_V1], "launch_failures": [FAILURE_V1]}
    },
    "security": {"security": {"launch_failures": [MISSING_V1]}},
    "empty": {},
}


def _keys(value: object) -> set[str]:
    """入れ子を含む鍵の道筋の集合。配列の添字は区別しない。"""
    found: set[str] = set()

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                found.add(f"{path}/{key}")
                walk(item, f"{path}/{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, f"{path}[]")

    walk(value, "")
    return found


@pytest.mark.parametrize("shape", MINIMAL_V1_SHAPES)
def test_v1_shape_upgrades_to_the_keys_the_current_writer_writes(shape: str) -> None:
    raw = MINIMAL_V1_SHAPES[shape]
    upgraded = pipeline._UPGRADES[1](raw)
    written = dump_runs(load_runs(raw).groups)
    del written["format"]
    assert _keys(upgraded) == _keys(written)


def test_upgrade_fills_defaults_and_does_not_touch_the_input() -> None:
    raw = MINIMAL_V1_SHAPES["micro"]
    upgraded = pipeline._UPGRADES[1](raw)
    assert "cost_source" not in raw["micro"]["invocations"][0]
    assert upgraded["micro"]["invocations"][0]["cost_source"] == "measured"
    failure = upgraded["micro"]["launch_failures"][0]
    assert (failure["scope"], failure["round"], failure["role"]) == (
        "launch",
        "current",
        LEGACY_FULL_SHAPE_ROLE,
    )


def test_old_over_budget_flag_becomes_the_outcome() -> None:
    row = {**ROW_V1, "over_budget": True}
    upgraded = pipeline._UPGRADES[1]({"micro": {"invocations": [row]}})
    migrated = upgraded["micro"]["invocations"][0]
    assert "over_budget" not in migrated
    assert migrated["budget_outcome"] == "exceeded_after_success"


def test_old_over_budget_flag_leaves_a_row_with_a_limit_alone() -> None:
    row = {**ROW_V1, "over_budget": True, "max_budget_usd": 1.0}
    upgraded = pipeline._UPGRADES[1]({"micro": {"invocations": [row]}})
    migrated = upgraded["micro"]["invocations"][0]
    assert "over_budget" not in migrated
    assert "budget_outcome" not in migrated


def test_load_reads_the_upgraded_values() -> None:
    (micro,) = load_runs(MINIMAL_V1_SHAPES["micro"]).groups.values()
    assert isinstance(micro, pipeline.ShardedGroupRun)
    assert micro.invocations is not None
    assert micro.invocations[0].record.cost == Measured(0.25)
    assert micro.launch_failures is not None
    (failure,) = micro.launch_failures
    assert isinstance(failure, DiscoveryLaunchFailed)
    assert failure.role == LEGACY_FULL_SHAPE_ROLE


@pytest.mark.parametrize("shape", MINIMAL_V1_SHAPES)
def test_write_then_read_round_trips(shape: str, tmp_path: Path) -> None:
    runs = dict(load_runs(MINIMAL_V1_SHAPES[shape]).groups)
    write_runs(tmp_path, runs)
    stored = json.loads((tmp_path / "runs.json").read_text("utf-8"))
    assert stored["format"] == RUNS_FORMAT
    assert read_runs(tmp_path) == runs
    # 移した結果は書き戻さないので、読み直して書いても同じになる
    assert dump_runs(read_runs(tmp_path)) == stored


def test_missing_format_is_version_1_and_current_format_is_not_upgraded() -> None:
    current = {"format": RUNS_FORMAT, "micro": {"discovered": 1}}
    assert load_runs(current).groups == load_runs({"micro": {"discovered": 1}}).groups


@pytest.mark.parametrize("version", ["2", 2.0, True, None, 0, -1, RUNS_FORMAT + 1])
def test_invalid_version_stops_with_a_review_error(version: object) -> None:
    with pytest.raises(ReviewError, match=r"runs\.json"):
        load_runs({"format": version, "micro": {}})


def test_every_stored_fixture_with_runs_loads() -> None:
    # 読めないと分けた素材は、読めることを縛る対象ではない
    sources = [
        source
        for source in STORE.glob("*/*/*/*/*/runs.json")
        if source.relative_to(STORE).parts[0] != "unreadable"
    ]
    assert sources, f"{STORE} に runs.json の素材が無い"
    for source in sources:
        load_runs(json.loads(source.read_text("utf-8")))


def test_upgrade_table_has_every_step() -> None:
    check_upgrades("runs.json", RUNS_FORMAT, _UPGRADES)


ROW_V2: dict[str, Any] = {**ROW_V1, "cost_source": "measured"}
FAILURE_V2: dict[str, Any] = {
    **FAILURE_V1,
    "scope": "launch",
    "round": "current",
    "ran_at": None,
    "cost_source": "measured",
    "invocation": {**ROW_V1, "prompt_version": "v", "prompt_chars": 10},
}


def test_v2_upgrade_leaves_the_rows_as_they_are() -> None:
    raw = {"micro": {"invocations": [ROW_V2], "launch_failures": [FAILURE_V2]}}
    assert pipeline._UPGRADES[2](raw) == raw


def test_v2_runs_json_loads_and_is_written_as_the_current_version(tmp_path: Path) -> None:
    raw = {"format": 2, "micro": {"invocations": [ROW_V2], "launch_failures": [FAILURE_V2]}}
    (micro,) = load_runs(raw).groups.values()
    assert isinstance(micro, pipeline.ShardedGroupRun)
    assert micro.invocations is not None
    write_runs(tmp_path, dict(load_runs(raw).groups))
    stored = json.loads((tmp_path / "runs.json").read_text("utf-8"))
    assert stored["format"] == RUNS_FORMAT
    assert "session_id" not in stored["micro"]["invocations"][0]


def test_session_id_in_a_stored_row_is_discarded_on_the_next_write(tmp_path: Path) -> None:
    raw = {
        "format": 3,
        "micro": {"invocations": [{**ROW_V2, "session_id": "abc"}]},
    }
    write_runs(tmp_path, dict(load_runs(raw).groups))
    stored = json.loads((tmp_path / "runs.json").read_text("utf-8"))
    assert "session_id" not in stored["micro"]["invocations"][0]
