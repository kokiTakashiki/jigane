"""実行の記録の試験の支え。実行の設定、死んだプロセスの番号、記録の読み出しを持つ。"""

import json
import sys
from typing import Any

from dev_launch import run_tool

from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review


def settings(invoke: claude.Invoke | None = None) -> claude.RunSettings:
    def refuse(_invocation: claude.Invocation) -> claude.InvocationResult:
        raise AssertionError("LLM は呼ばない")

    return claude.RunSettings(
        budget=claude.RunBudget("m", 1.0, reserve_margin=True),
        effort=None,
        invoke=invoke or refuse,
    )


def dead_pid() -> int:
    # 終わったプロセスの番号。直後に別のプロセスが同じ番号を得る可能性は無視できる
    return int(run_tool([sys.executable, "-c", "import os; print(os.getpid())"]))


def read(review: Review, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((review.id.directory() / name).read_text("utf-8"))
    return data
