"""`jigane estimate`: レビュー資料の大きさと費用を見積もる。"""

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, assert_never

from jigane.commands import failures
from jigane.commands.budget_argument import add_max_budget_argument
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.group_argument import add_group_argument
from jigane.commands.mcp_argument import add_mcps_argument, load_mcp_selection
from jigane.commands.output import print_lines
from jigane.commands.run_guide import run_guide
from jigane.commands.unparsed_files import print_unparsed_notice
from jigane.findings.records import HistoryWarning
from jigane.groups.definitions import Effort, GroupId
from jigane.llm_runs import claude, codex, pricing
from jigane.llm_runs.codex_mcp import REFERENCE_NOTICE
from jigane.llm_runs.defaults import RunDefaults, resolve_run_defaults
from jigane.reviews.catalog import load_review
from jigane.reviews.lifecycle import Review
from jigane.reviews.resolution import resolve_review
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import (
    DesignPolicyEstimate,
    DiagramPolicyEstimate,
    Estimate,
    ImplementationEstimate,
    SecurityEstimate,
)
from jigane.skills.estimator import estimate_of
from jigane.skills.group_expectation import (
    CODEX_LOGIN_OUTPUT_TOKENS,
    GroupExpectation,
    codex_login_expectation,
    read_expectation,
)
from jigane.skills.launch_prompts import LaunchInput, LaunchPrompt, launch_prompts
from jigane.skills.mcp import NO_MCP, McpSelection
from jigane.skills.output_estimate import skipped_rows_message
from jigane.skills.review_docs import read_docs_for_review
from jigane.storage.agent_selection import Agent

CODEX_DESIGN_ESTIMATE_NOTE = (
    "設計書の形は 1 回の起動でレビューし、道具を渡しません。"
    "資料は宣言、設計書の全文と構造、付属の文書、名指しの照合の表、"
    "選んだファイル、いまの形の図、前の回の指摘です。"
    "Codex 用の境界文を含む初期プロンプトから入力トークンを概算し、"
    f"出力と推論は計 {CODEX_LOGIN_OUTPUT_TOKENS:,} トークンとします。"
    "公開 API 単価で換算した見込みを expected.total_usd に出します。"
    "実行後の換算額は Codex CLI が報告した使用量から求めます。"
)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    estimate = subparsers.add_parser(
        "estimate",
        help="レビュー資料の大きさと費用を見積もります",
        epilog="Codex CLI は保存済みの ChatGPT ログインを使います。",
    )
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
    cost = past_run["cost_usd"]
    amount = "金額不明" if cost is None else f"{cost:.4f} USD"
    return [
        (
            f"過去の実行の実測（キャッシュ込み、モデル {model}）{mismatch}: "
            f"入力 {past_run['input_tokens']} トークン、"
            f"{amount}、起動 {past_run['invocation_count']} 回"
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
    label = "入力と出力の見込みの費用の見積もり" if basis.counts_output else "入力の費用の見積もり"
    lines = [f"{label}: {cost.cost.usd:.4f} USD"]
    if cost.cost.assumed_price:
        lines.append(pricing.assumed_price_notice(model, cost.cost))
    warning = estimate.expected_budget_warning(basis)
    if warning is not None:
        lines.append(warning)
    stop_warning = estimate.budget_stop_warning(basis)
    if stop_warning is not None:
        sections = "、".join(entry.text() for entry in estimate.largest_section_entries())
        lines.append(stop_warning + (f"大きい節: {sections}" if sections else ""))
    return lines


def principle_lines(estimate: Estimate, basis: claude.RunBudget) -> list[str]:
    if not isinstance(estimate, DiagramPolicyEstimate) or basis.principles is None:
        # 原則の起動を持つのは図の形だけで、その組は図の形の既定が渡す
        return []
    runs = [basis.for_principle(number) for number, _ in estimate.principle_input_tokens]
    total = basis.max_budget_usd + sum(run.max_budget_usd for run in runs)
    lines = []
    if runs:
        groups = Counter((run.model, run.max_budget_usd) for run in runs)
        lines.append(
            "原則の起動: "
            + "、".join(
                f"{model} {count} 回（1 回の目安 {limit:g} USD）"
                for (model, limit), count in groups.items()
            )
        )
    lines.append(f"全起動の和の目安（超えることがある）: {round(total, 6):g} USD")
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
    report: EstimateReport,
    review: Review,
    group: GroupId,
    defaults: RunDefaults,
    expectation: GroupExpectation,
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
        **_with_group_expectation(report.estimate.input_cost_record(basis), expectation),
    }


def _with_group_expectation(
    record: dict[str, Any], expectation: GroupExpectation
) -> dict[str, Any]:
    """入力の費用の欄の `expected` に群の見込みを重ねる。設計書の形は既存の鍵を残す。"""
    return {**record, "expected": {**record.get("expected", {}), **expectation.as_record()}}


def print_estimate(
    report: EstimateReport,
    review: Review,
    group: GroupId,
    *,
    as_json: bool,
    expectation: GroupExpectation,
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
                estimate_record(report, review, group, defaults, expectation),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print_lines(
        [
            *size_lines(estimate),
            f"入力トークンの見積もり（検査と見積もりに使う数）: {estimate.discovery_input_tokens}",
            *notice_lines(estimate),
            *input_cost_lines(estimate, basis),
            *skipped_rows_lines(estimate),
            *past_run_lines(estimate.past_run, model),
            f"モデルの既定値: {model}",
            f"effort: {defaults.effort or '渡さない'}",
            f"1 回の起動の目安（超えることがある）: {defaults.budget.max_budget_usd} USD",
            *principle_lines(estimate, basis),
            f"注記: {estimate.note}",
            *blocker_lines(blockers),
            *(f"警告: {warning.text()}" for warning in warnings),
        ]
    )


def print_codex_estimate(
    report: EstimateReport,
    review: Review,
    group: GroupId,
    prompts: tuple[LaunchPrompt, ...],
    *,
    as_json: bool,
    mcp_names: tuple[str, ...],
) -> None:
    boundary = codex.review_boundary(uses_mcp=bool(mcp_names))
    sizes = [launch.size + len(boundary.encode("utf-8")) for launch in prompts]
    sizes_text = ", ".join(map(str, sizes)) or "なし"
    launches = [
        codex_launch_estimate(launch, size, uses_mcp=bool(mcp_names))
        for launch, size in zip(prompts, sizes, strict=True)
    ]
    default = codex.LOGIN_MODEL
    expectation = (
        codex_login_expectation(prompts, has_verifier=report.estimate.has_verifier)
        if not mcp_names
        else None
    )
    if as_json:
        record = report.estimate.to_record()
        if isinstance(report.estimate, DesignPolicyEstimate):
            record["note"] = CODEX_DESIGN_ESTIMATE_NOTE
        print(
            json.dumps(
                {
                    "group": group.value,
                    **record,
                    "agent": Agent.CODEX,
                    "cli_auth": "chatgpt_login",
                    "model": default.name,
                    "effort": default.effort,
                    "max_budget_usd_per_call": None,
                    "run_guide": run_guide(
                        review,
                        group,
                        include_policy_only=isinstance(report.estimate, DiagramPolicyEstimate),
                    ).to_json(),
                    "history_warnings": [warning.to_json() for warning in report.warnings],
                    "input_cost_usd": None,
                    "expected": (
                        {"total_usd": None} if expectation is None else expectation.as_record()
                    ),
                    "planned_invocations": len(sizes),
                    "has_verifier": report.estimate.has_verifier,
                    "prompt_bytes": sizes,
                    "launches": launches,
                    "noninteractive_run_available": expectation is not None,
                    "noninteractive_requires": None if mcp_names else "codex login",
                    "noninteractive_model": None if mcp_names else default.name,
                    **(
                        {"mcp_servers": mcp_names, "noninteractive_unavailable_reason": "mcp"}
                        if mcp_names
                        else {}
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print_lines(
        [
            *size_lines(report.estimate),
            f"Codex の初期起動 {len(sizes)} 回（各プロンプト {sizes_text} バイト）",
            *(
                ["検証などの追加起動数は実行時に決まります"]
                if report.estimate.has_verifier
                else []
            ),
            *past_run_lines(report.estimate.past_run, default.name),
            "Codex CLI は保存された ChatGPT ログインを使います。",
            *(
                f"{row['principle'] or row['role']}: {row['model']}"
                f"（effort {row['effort'] or '既定'}、本文の入力概算 {row['input_usd']:.6f} USD）"
                for row in launches
            ),
            *([f"使う MCP: {', '.join(mcp_names)}", f"{REFERENCE_NOTICE}。"] if mcp_names else []),
            (
                "本文の入力概算はバイト数と公開 API 単価で換算し、"
                "スキーマ、キャッシュ、出力、推論を含みません"
                + ("。MCP から実行中に取得する資料も含みません。" if mcp_names else "。")
            ),
            *(
                [f"{codex.NO_DEBUG_MCP}。"]
                if mcp_names
                else codex_debug_estimate_lines(expectation)
            ),
            f"{codex.NO_SPENDING_LIMIT}。",
            *(f"警告: {warning.text()}" for warning in report.warnings),
        ]
    )


def codex_debug_estimate_lines(expectation: GroupExpectation | None) -> list[str]:
    if expectation is None:
        return ["群の費用を見積もれないため非対話の単群は実行できません。"]
    return [
        (
            f"群の見込み: {expectation.total_usd} USD"
            f"（初期起動の入力と出力・推論 {CODEX_LOGIN_OUTPUT_TOKENS:,} トークン/回の概算"
            f" × 検証役の倍率 {expectation.multiplier:g}）"
        )
    ]


def codex_launch_estimate(
    launch: LaunchPrompt, size: int, *, uses_mcp: bool = False
) -> dict[str, Any]:
    model = launch.codex_model()
    tokens = pricing.estimate_input_tokens(size)
    return {
        "role": launch.role,
        "principle": launch.principle,
        "model": model.name,
        "effort": model.effort,
        "input_tokens": tokens,
        "input_usd": tokens * model.input_price / codex.NANOS_PER_USD,
        "noninteractive_model": None if uses_mcp else model.name,
        "noninteractive_effort": None if uses_mcp else model.effort,
        "noninteractive_input_usd": (
            None if uses_mcp else tokens * model.input_price / codex.NANOS_PER_USD
        ),
    }


def estimate_and_print(
    text: str,
    group: GroupId,
    *,
    as_json: bool,
    effort: Effort | None = None,
    max_budget_usd: float | None = None,
    mcp: McpSelection = NO_MCP,
    agent: Agent | None = None,
) -> None:
    review = load_review(resolve_review(text))
    assembly = AssemblyInput.load(review, docs=read_docs_for_review(review, group))
    # 資料の組み立てはロガーに警告を流さない。警告を描く縁はここなので、両方の履歴の分を渡す
    warnings = (*assembly.source.own.warnings, *assembly.source.related_warnings)
    # シャードの数は実行と同じ上限の組から決まる（実行の入口と同じ resolve_run_defaults）
    budget = resolve_run_defaults(group, None, effort, max_budget_usd=max_budget_usd).budget
    if agent is Agent.CODEX:
        budget = codex.review_budget(budget)
    launch_input = LaunchInput(assembly, group, mcp, budget)
    estimate = estimate_of(launch_input)
    report = EstimateReport(estimate, warnings)
    print_unparsed_notice(review)
    if agent is Agent.CODEX:
        print_codex_estimate(
            report,
            review,
            group,
            launch_prompts(launch_input),
            as_json=as_json,
            mcp_names=mcp.names,
        )
        return
    print_estimate(
        report,
        review,
        group,
        as_json=as_json,
        expectation=read_expectation(estimate, budget, Path(review.repository_root), group),
        effort=effort,
        max_budget_usd=max_budget_usd,
    )


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 見積もりは LLM を呼ばない
    if args.agent == Agent.CODEX:
        codex.check_options(
            effort=args.effort,
            max_budget_usd=args.max_budget_usd_per_call,
        )
    mcp = load_mcp_selection(
        args.mcps, args.group, args.mcp_guide, codex_cli=args.agent == Agent.CODEX
    )
    estimate_and_print(
        args.review,
        args.group,
        as_json=args.json,
        effort=args.effort,
        max_budget_usd=args.max_budget_usd_per_call,
        mcp=mcp,
        agent=args.agent,
    )
    return EXIT_OK
