"""`jigane estimate`: 入力の大きさを見積もる。"""

import argparse
import json
from dataclasses import dataclass
from typing import Any, assert_never

from jigane.commands import failures
from jigane.commands.budget_argument import add_max_budget_argument
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.group_argument import add_group_argument
from jigane.commands.mcp_argument import add_mcps_argument, load_mcp_selection
from jigane.commands.output import print_lines
from jigane.commands.run_guide import run_guide
from jigane.findings.records import HistoryWarning
from jigane.groups.definitions import Effort, GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.defaults import RunDefaults, resolve_run_defaults
from jigane.reviews.lifecycle import Review, load_review, resolve_review
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import (
    DesignPolicyEstimate,
    DiagramPolicyEstimate,
    Estimate,
    ImplementationEstimate,
    SecurityEstimate,
)
from jigane.skills.estimator import estimate_of
from jigane.skills.mcp import NO_MCP, McpSelection
from jigane.skills.output_estimate import skipped_rows_message


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    estimate = subparsers.add_parser("estimate", help="入力の大きさを見積もります")
    estimate.add_argument("review")
    add_group_argument(estimate)
    estimate.add_argument(
        "--effort",
        type=Effort,
        choices=list(Effort),
        default=None,
        help="effort です。省略すると群の既定を使います。見積もりに解決した effort を出します",
    )
    add_max_budget_argument(estimate)
    add_mcps_argument(estimate)
    estimate.add_argument("--json", action="store_true")
    estimate.set_defaults(handler=run)


def past_run_lines(past_run: dict[str, Any] | None, current_model: str) -> list[str]:
    if past_run is None:
        return []
    model = past_run["model"] or "不明"
    mismatch = "（モデルが違うため金額は目安になりません）" if model != current_model else ""
    return [
        (
            f"過去の実行の実測（キャッシュ込み、モデル {model}）{mismatch}: "
            f"入力 {past_run['input_tokens']} トークン、"
            f"{past_run['cost_usd']:.4f} USD、起動 {past_run['invocation_count']} 回"
        )
    ]


def notice_lines(estimate: Estimate) -> list[str]:
    notice = estimate.policy_notice_text()
    if not notice:
        return []
    return [f"注: 方針のレビュー: {notice}"]


def input_cost_lines(estimate: Estimate, basis: claude.RunBudget) -> list[str]:
    model = basis.model
    cost = estimate.input_cost(basis)
    if cost is None:
        return []
    lines = [f"入力の費用の見積もり: {cost.cost.usd:.4f} USD"]
    if cost.cost.assumed_price:
        lines.append(claude.assumed_price_notice(model, cost.cost))
    warning = estimate.expected_budget_warning(basis)
    if warning is not None:
        lines.append(warning)
    stop_warning = estimate.budget_stop_warning(basis)
    if stop_warning is not None:
        sections = "、".join(entry.text() for entry in estimate.largest_section_entries())
        lines.append(stop_warning + (f"大きい節: {sections}" if sections else ""))
    return lines


def skipped_rows_lines(estimate: Estimate) -> list[str]:
    expected = estimate.expected_output()
    if expected is None:
        return []
    message = skipped_rows_message(expected.skipped)
    return [] if message is None else [message]


def size_lines(estimate: Estimate) -> list[str]:
    match estimate:
        case DiagramPolicyEstimate() | DesignPolicyEstimate():
            return [f"プロンプト（宣言、背景、図と表） {estimate.materials_bytes} バイト"]
        case ImplementationEstimate() | SecurityEstimate():
            sizes = estimate.shard_bytes
            return [
                f"対象のファイル {estimate.target_files} 件、シャード {len(sizes)} 個",
                *(f"  シャード {i + 1}: {size} バイト" for i, size in enumerate(sizes)),
            ]
        case _:
            assert_never(estimate)


def blocker_lines(blockers: tuple[failures.Blocker, ...]) -> list[str]:
    return [
        line
        for blocker in blockers
        for line in (f"実行できません: {blocker.reason}", f"次の操作: {blocker.action}")
    ]


@dataclass(frozen=True)
class EstimateReport:
    """見積もりの結果と、その材料の履歴を読んだときの警告。"""

    estimate: Estimate
    warnings: tuple[HistoryWarning, ...]


def estimate_record(
    report: EstimateReport, review: Review, group: GroupId, defaults: RunDefaults
) -> dict[str, Any]:
    """`jigane estimate --json` の鍵と値を組む唯一の場所。

    型ごとの欄、実行の既定値、案内、履歴の警告、入力の費用の順に並べる。
    """
    basis = defaults.budget
    return {
        "group": group.value,
        **report.estimate.to_record(),
        "model": basis.model,
        "effort": defaults.effort,
        "max_budget_usd_per_call": basis.max_budget_usd,
        "run_guide": run_guide(review, group).to_json(),
        "history_warnings": [warning.to_json() for warning in report.warnings],
        **report.estimate.input_cost_record(basis),
    }


def print_estimate(
    report: EstimateReport,
    review: Review,
    group: GroupId,
    *,
    as_json: bool,
    effort: Effort | None = None,
    max_budget_usd: float | None = None,
) -> None:
    warnings = report.warnings
    estimate = report.estimate
    defaults = resolve_run_defaults(
        group, estimate.policy_shape(), effort, max_budget_usd=max_budget_usd
    )
    basis = defaults.budget
    model = basis.model
    blockers = failures.blockers()
    if as_json:
        print(
            json.dumps(
                estimate_record(report, review, group, defaults), ensure_ascii=False, indent=2
            )
        )
        return
    print_lines(
        [
            *size_lines(estimate),
            f"入力トークンの見積もり（素朴な推定）: {estimate.discovery_input_tokens}",
            *notice_lines(estimate),
            *input_cost_lines(estimate, basis),
            *skipped_rows_lines(estimate),
            *past_run_lines(estimate.past_run, model),
            f"モデルの既定値: {model}",
            f"effort: {defaults.effort or '渡さない'}",
            f"1 回の起動の目安（超えることがある）: {defaults.budget.max_budget_usd} USD",
            f"注記: {estimate.note}",
            *blocker_lines(blockers),
            *(f"警告: {warning.text()}" for warning in warnings),
        ]
    )


def estimate_and_print(
    text: str,
    group: GroupId,
    *,
    as_json: bool,
    effort: Effort | None = None,
    max_budget_usd: float | None = None,
    mcp: McpSelection = NO_MCP,
) -> None:
    review = load_review(resolve_review(text))
    assembly = AssemblyInput.load(review)
    # 資料の組み立てはロガーに警告を流さない。警告を描く縁はここなので、両方の履歴の分を渡す
    warnings = (*assembly.source.own.warnings, *assembly.source.related_warnings)
    estimate = estimate_of(assembly, group, mcp)
    print_estimate(
        EstimateReport(estimate, warnings),
        review,
        group,
        as_json=as_json,
        effort=effort,
        max_budget_usd=max_budget_usd,
    )


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 見積もりは LLM を呼ばない
    mcp = load_mcp_selection(args.mcps, args.group, args.mcp_guide)
    estimate_and_print(
        args.review,
        args.group,
        as_json=args.json,
        effort=args.effort,
        max_budget_usd=args.max_budget_usd_per_call,
        mcp=mcp,
    )
    return EXIT_OK
