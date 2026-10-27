"""`--max-budget-usd-per-call` の引数の定義。`run` と `estimate` が同じものを使う。"""

import argparse

from jigane.groups.definitions import GROUPS
from jigane.llm_runs.defaults import resolve_run_defaults


def _budget_defaults_text() -> str:
    budgets = {
        group: resolve_run_defaults(group.identifier, None, None).budget.max_budget_usd
        for group in GROUPS
    }
    return "、".join(f"{group.label}は {budget:g} USD" for group, budget in budgets.items())


def add_max_budget_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-budget-usd-per-call",
        type=float,
        default=None,
        help=(
            "Claude Code CLI の 1 回の起動あたりの上限の目安です（USD。超えることがあります）。"
            f"省略すると群の既定（{_budget_defaults_text()}）を使います。"
            "方針は起動が 1 回なので、この値が実行全体の目安になります。"
            "実装とセキュリティはシャードと役ごとに複数回起動するため、"
            "実行全体ではこの数倍を使うことがあります。"
            "見込みが目安以上のときは、警告して続けます。"
            "estimate では、この値で上限を決めた見積もりを出します"
        ),
    )
