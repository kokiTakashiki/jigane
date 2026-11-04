"""`--max-budget-usd-per-call` の引数の定義。`run` と `estimate` が同じものを使う。"""

import argparse
from typing import Any

from jigane.groups.definitions import DIAGRAM_SHAPE, GROUPS, PRINCIPLE_RUNS
from jigane.llm_runs.defaults import resolve_run_defaults


def _budget_defaults_text() -> str:
    budgets = {
        group: resolve_run_defaults(group.identifier, None, None).budget.max_budget_usd
        for group in GROUPS
    }
    return "、".join(f"{group.label}は {budget:g} USD" for group, budget in budgets.items())


def principle_numbers_by(attribute: str) -> dict[Any, str]:
    """原則の組の値ごとに、その値を持つ原則の番号を「P1〜P4」のようにまとめる。"""
    numbers_by_value: dict[Any, list[str]] = {}
    for number, run in PRINCIPLE_RUNS.items():
        numbers_by_value.setdefault(getattr(run, attribute), []).append(number)
    return {
        value: numbers[0] if len(numbers) == 1 else f"{numbers[0]}〜{numbers[-1]}"
        for value, numbers in numbers_by_value.items()
    }


def _principle_limits_text() -> str:
    return "か ".join(
        f"{limit:g} USD（{numbers}）"
        for limit, numbers in principle_numbers_by("max_budget_usd").items()
    )


def _policy_diagram_text() -> str:
    total = DIAGRAM_SHAPE.max_budget_usd + sum(
        run.max_budget_usd for run in PRINCIPLE_RUNS.values()
    )
    return (
        f"方針の図の形は、既定では図の起動に {DIAGRAM_SHAPE.max_budget_usd:g} USD、"
        f"原則の起動に 1 本 {_principle_limits_text()}で、和は {round(total, 6):g} USD です。"
        "原則の起動は、その原則を問える図の形があるときだけ走ります。"
        "この値を渡すと図と原則のすべての起動の上限になり、"
        "実行全体の目安は最大でこの値の "
        f"{len(PRINCIPLE_RUNS) + 1} 倍です。"
    )


def add_max_budget_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-budget-usd-per-call",
        type=float,
        default=None,
        help=(
            "Claude Code CLI の 1 回の起動あたりの上限の目安です（USD。超えることがあります）。"
            f"省略すると群の既定（{_budget_defaults_text()}）を使います。"
            f"{_policy_diagram_text()}"
            "設計書の形は起動が 1 回なので、この値が実行全体の目安になります。"
            "実装とセキュリティはシャードと役ごとに複数回起動するため、"
            "実行全体ではこの数倍を使うことがあります。"
            "見込みが目安以上のときは、警告して続けます。"
            "estimate では、この値で上限を決めた見積もりを出します"
        ),
    )
