"""群の保存した状態（指摘と runs.json）を、LLM を呼ばずに置く。

試験と、画面を目で確かめる手順の両方が使う。JIGANE_HOME を一時の場所に向けて呼ぶこと。
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from review_fakes import group_result

from jigane.findings.records import RUNS_FILE, ThemeUpdate, merge_group
from jigane.groups.definitions import GroupId
from jigane.reviews.lifecycle import Review
from jigane.skills.pipeline import write_json

# 実行が成功した記録の最小の形。invocations は 1 回の起動（14 秒、0.02 USD）
SUCCESSFUL_RUN: dict[str, Any] = {
    "ran_at": "2026-10-20T10:00:00+00:00",
    "elapsed_s": 14.0,
    "model": "claude-sonnet-5-5",
    "effort": None,
    "invocations": [
        {
            "role": "discovery",
            "split_index": 0,
            "cost_usd": 0.02,
            "input_tokens": 1,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": 1,
            "duration_ms": 1,
            "num_turns": 1,
            "is_error": False,
            "terminal_reason": None,
            "permission_denials": 0,
        }
    ],
    "discovered": 0,
    "dropped_without_evidence": 0,
    "dropped_invalid_schema": 0,
    "shards": 1,
    "succeeded_shards": 1,
    "failed_splits": 0,
    "budget_stops": [],
}


def rewrite_raw_findings(directory: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    """findings.json を型を通さず直接書き換える。旧形式や壊れた値を置くテストの入口。

    版を外すので、書き換えた結果は版 1 として読まれる。
    """
    path = directory / "findings.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["format"]
    edit(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")


def seed_group(
    review: Review,
    group: GroupId,
    findings: list[dict[str, Any]],
    run_state: dict[str, Any],
) -> None:
    """群の指摘と実行の記録を置く。run_state は SUCCESSFUL_RUN に上書きする項目。"""
    directory = review.id.directory()
    update = ThemeUpdate((), 0) if group == GroupId.MICRO else None
    merge_group(directory, group, group_result(findings, update), None)
    path = directory / RUNS_FILE
    runs = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    runs[group] = {**SUCCESSFUL_RUN, **run_state}
    write_json(path, runs)
