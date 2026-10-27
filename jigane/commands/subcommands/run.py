"""`jigane run`: スキルを実行する。"""

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, assert_never

from jigane.commands.budget_argument import add_max_budget_argument
from jigane.commands.consent import launch_plan, require_consent
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_INCOMPLETE_COVERAGE, EXIT_OK
from jigane.commands.group_argument import add_group_argument
from jigane.commands.mcp_argument import add_mcps_argument, load_mcp_selection
from jigane.commands.output import print_lines, warning_lines, warnings_json
from jigane.findings.records import (
    HistoryWarningJson,
    TriagedFinding,
    load_history_checked,
    triaged_dicts,
)
from jigane.groups.definitions import (
    GROUP_BY_ID,
    GROUPS,
    MACRO,
    MICRO,
    Effort,
    GroupId,
    RunShape,
)
from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude, launch_failure
from jigane.llm_runs.defaults import RunDefaults, resolve_run_defaults
from jigane.reviews.lifecycle import Review, load_review, resolve_review
from jigane.skills import macro, micro, order, pipeline, run_record, security
from jigane.skills.assembly import AssemblyInput, HistorySource
from jigane.skills.mcp import NO_MCP, McpSelection


def _model_defaults_text() -> str:
    return "、".join(f"{group.label}は {group.model}" for group in GROUPS)


def _effort_defaults_text() -> str:
    # 表で effort が None の群は --effort を渡さない
    efforts = {
        group: resolve_run_defaults(group.identifier, None, None).effort for group in GROUPS
    }
    return "、".join(
        f"{group.label}は {effort}" if effort else f"{group.label}は渡さない"
        for group, effort in efforts.items()
    )


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    run = subparsers.add_parser(
        "run",
        help="スキルを実行します",
        epilog=(
            "終了コード: 0 は正常終了、2 は処理の失敗、3 はシャードか検証の分割が失敗し、"
            "結果に未検証の候補を含むこと。"
        ),
    )
    run.add_argument("review")
    add_group_argument(run)
    run.add_argument(
        "--model",
        default=None,
        help=f"その回の全ての役のモデルです。省略すると群の既定（{_model_defaults_text()}）を使います",
    )
    run.add_argument(
        "--effort",
        type=Effort,
        choices=list(Effort),
        default=None,
        help=(
            "Claude Code CLI の --effort です。"
            f"省略すると群の既定（{_effort_defaults_text()}）を使います"
        ),
    )
    add_max_budget_argument(run)
    add_mcps_argument(run)
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=handle)


def print_run_json(
    findings: Sequence[TriagedFinding],
    run: pipeline.GroupRun,
    totals: object,
    policy_notice: str | None = None,
    history_warnings: Sequence[HistoryWarningJson] = (),
    *,
    notice: str | None = None,
) -> None:
    print(
        json.dumps(
            {
                "findings": triaged_dicts(findings),
                "run": run.to_json(),
                "totals": totals,
                "policy_notice": policy_notice,
                # 目安の超過の見込みなど、実行は続けるが利用者へ知らせる文
                "notice": notice,
                # 読めない履歴は前の回の指摘が資料に載らない理由になる。show --json と同じ欄の形
                "history_warnings": list(history_warnings),
                # 記録した失敗の種類と理由を、そのまま --json に載せる
                "failures": [
                    view.to_json()
                    for view in launch_failure.failure_views(run.launch_failures or ())
                ],
                "earlier_failures": launch_failure.earlier_failures_json(
                    run.launch_failures or ()
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _total_cost_line(totals: dict[str, Any], run: pipeline.GroupRun) -> str:
    return (
        f"実行全体の金額: {totals['cost_usd']:.4f} USD"
        f"（起動 {totals['invocation_count']} 回）、"
        f"所要 {pipeline.format_elapsed(run.elapsed_s or 0.0)}"
    )


def report_macro(
    result: macro.MacroRun,
    *,
    as_json: bool,
    history: "HistoryNotice",
) -> None:
    run = result.run_state
    totals = pipeline.summarize_invocations(run)
    # 標準出力は --json の本体に空ける
    if result.notice is not None:
        print(result.notice, file=sys.stderr)
    if as_json:
        print_run_json(
            result.findings,
            run,
            totals,
            None,
            history.warnings,
            notice=result.notice,
        )
    else:
        print_lines(
            history.lines,
            [
                (
                    f"方針の指摘: {len(result.findings)} 件"
                    f"（発見 {run.discovered} 件、"
                    f"根拠なしで捨てた {run.dropped_without_evidence} 件）"
                ),
                _total_cost_line(totals, run),
            ],
            pipeline.over_budget_lines(totals),
        )


def retry_lines(failures: Iterable[launch_failure.StoredFailure]) -> list[str]:
    """走らせ直した検証があれば、その件数の行。無ければ空。

    分割ごとの走らせ直し（一時の失敗）と、欠けた候補だけの走らせ直しは別の行にする。
    """
    split_failures: list[launch_failure.VerificationLaunchFailed] = []
    refill_failures: list[launch_failure.CandidatesUnverified] = []
    for failure in launch_failure.current_failures(failures):
        match failure:
            case launch_failure.DiscoveryLaunchFailed():
                # 発見役の失敗は検証の走らせ直しの件数に入らない
                continue
            case launch_failure.VerificationLaunchFailed():
                split_failures.append(failure)
            case launch_failure.CandidatesUnverified():
                refill_failures.append(failure)
            case _ as unreachable:
                assert_never(unreachable)
    retried = launch_failure.rerun_count(split_failures)
    refilled = launch_failure.rerun_count(refill_failures)
    return [
        *([f"走らせ直した検証の分割: {retried} 件（一時の失敗）"] if retried else []),
        *([f"欠けた候補だけ走らせ直した検証の役: {refilled} 件"] if refilled else []),
    ]


def failure_reason_lines(all_failures: Iterable[launch_failure.StoredFailure]) -> list[str]:
    """失敗した発見役と検証の分割の理由を一行ずつの文にする。走らせ直して通った失敗は含めない。"""
    failures = launch_failure.current_failures(all_failures)
    disposition = launch_failure.Disposition
    # 走らせ直しが入力の見積もりで停止した分割は NOT_RERUN を持たず、RERUN_STOPPED の
    # 失敗が理由を持つ。停止そのものは budget_stops の行に出る
    ended = {(f.stage, f.index) for f in failures if f.disposition is disposition.NOT_RERUN}
    lines = []
    for failure in failures:
        if failure.disposition is disposition.RECOVERED:
            continue
        match failure:
            case launch_failure.DiscoveryLaunchFailed():
                if (
                    failure.disposition is disposition.RERUN_FAILED
                    and (failure.stage, failure.index) in ended
                ):
                    # 走らせ直してまた失敗した最初の失敗は、最後の失敗が理由を持つので出さない
                    continue
                lines.append(f"  発見役 {failure.index}（{failure.kind}）: {failure.reason}")
            case launch_failure.VerificationLaunchFailed():
                if (
                    failure.disposition is disposition.RERUN_FAILED
                    and (failure.stage, failure.index) in ended
                ):
                    # 走らせ直してまた失敗した最初の失敗は、最後の失敗が理由を持つので出さない
                    continue
                lines.append(
                    f"  検証の分割 {failure.index}・{failure.role or '役なし'}"
                    f"（{failure.kind}、{failure.attempt} 回目）: {failure.reason}"
                )
            case launch_failure.CandidatesUnverified():
                lines.append(
                    f"  検証の分割 {failure.index}・{failure.role}"
                    f"（{failure.kind}、欠けた候補の走らせ直し）: {failure.reason}"
                )
            case _ as unreachable:
                assert_never(unreachable)
    return lines


def report_sharded(
    label: str,
    result: micro.MicroRun | security.SecurityRun,
    *,
    as_json: bool,
    policy_notice: str | None,
    history: "HistoryNotice",
) -> None:
    """実装とセキュリティの実行結果を出す。終了コードは GroupRunResult が決める。"""
    run = result.run_state
    totals = pipeline.summarize_invocations(run)
    # 標準出力は --json の本体に空ける
    if result.notice is not None:
        print(result.notice, file=sys.stderr)
    if as_json:
        print_run_json(
            result.findings,
            run,
            totals,
            policy_notice,
            history.warnings,
            notice=result.notice,
        )
    else:
        print_lines(
            history.lines,
            [
                (
                    f"{label}の指摘: {len(result.findings)} 件（発見 {run.discovered} 件、"
                    f"根拠なしで捨てた {run.dropped_without_evidence} 件、"
                    f"担当の外の根拠で捨てた {run.dropped_outside_shard} 件）"
                ),
                (
                    f"被覆: シャード {run.succeeded_shards}/{run.shards}、"
                    f"ファイル {run.reviewed_files}/{run.target_files}"
                ),
                *retry_lines(run.launch_failures or ()),
                _total_cost_line(totals, run),
            ],
            pipeline.over_budget_lines(totals),
        )
    if result.failed_shards or result.failed_splits:
        print(
            f"jigane: 発見役が失敗したシャード {result.failed_shards} 個、"
            f"検証の分割 {result.failed_splits} 件が失敗し、"
            f"候補 {run.unverified_candidates} 件が未検証です",
            file=sys.stderr,
        )
        for line in failure_reason_lines(run.launch_failures or ()):
            print(line, file=sys.stderr)


def group_defaults(group: GroupId) -> RunShape:
    """群の既定の起動の形。往復と余裕の扱いはここから読む。上限と effort は別の関数が決める。"""
    return GROUP_BY_ID[group].shape


def policy_shape_of(group: GroupId, review: Review) -> PolicyShape | None:
    """方針だけが持つ形。他の群は None。"""
    return macro.policy_shape(review) if group == MACRO else None


def run_settings(
    group: GroupId, invoke: claude.Invoke, defaults: RunDefaults
) -> claude.RunSettings:
    """決めた既定（resolve_run_defaults。引数の上限を含む）と群の既定の形で組む。"""
    shape = group_defaults(group)
    return claude.RunSettings(
        budget=claude.RunBudget(
            defaults.budget.model, defaults.budget.max_budget_usd, shape.reserve_margin
        ),
        effort=defaults.effort,
        invoke=invoke,
        max_turns=shape.max_turns,
    )


@dataclass(frozen=True)
class HistoryNotice:
    """履歴の警告。--json の欄と通常の出力の行を、同じ HistorySource から作る。"""

    warnings: tuple[HistoryWarningJson, ...]
    lines: tuple[str, ...]

    @classmethod
    def from_source(cls, source: HistorySource) -> "HistoryNotice":
        return cls(
            warnings=(
                *warnings_json(source.own.warnings),
                *(w.to_json() for w in source.related_warnings),
            ),
            lines=(
                *warning_lines(source.own.warnings),
                *(f"警告: {w.text()}" for w in source.related_warnings),
            ),
        )


@dataclass(frozen=True)
class PolicyGroupResult:
    """方針の群の結果。方針は起動前の注意を持たず、失敗したシャードも無い。"""

    outcome: macro.MacroRun
    history: HistoryNotice

    @property
    def exit_code(self) -> int:
        return EXIT_OK


@dataclass(frozen=True)
class ShardedGroupResult:
    """シャードに分ける群（実装とセキュリティ）の結果。"""

    label: str
    outcome: micro.MicroRun | security.SecurityRun
    policy_notice: str | None
    history: HistoryNotice

    @property
    def exit_code(self) -> int:
        if self.outcome.failed_shards or self.outcome.failed_splits:
            return EXIT_INCOMPLETE_COVERAGE
        return EXIT_OK


# 出力と終了コードへの写しは、呼ぶ側が print_group_result で一度だけ行う
type GroupRunResult = PolicyGroupResult | ShardedGroupResult


def print_group_result(result: GroupRunResult, *, as_json: bool) -> int:
    """結果を出力へ書き、終了コードを返す。"""
    match result:
        case PolicyGroupResult():
            report_macro(result.outcome, as_json=as_json, history=result.history)
        case ShardedGroupResult():
            report_sharded(
                result.label,
                result.outcome,
                as_json=as_json,
                policy_notice=result.policy_notice,
                history=result.history,
            )
        case unreachable:
            assert_never(unreachable)
    return result.exit_code


def run_group(
    assembly: AssemblyInput,
    group: GroupId,
    settings: claude.RunSettings,
    *,
    mcp: McpSelection = NO_MCP,
) -> GroupRunResult:
    """同意の後の処理。記録を取って群を走らせ、結果を返す。出力は呼ぶ側が行う。

    同意は呼ぶ側が取る。公開の CLI（handle）は端末の確認を済ませてから呼び、開発の道具は
    CLI を通さずにここを直接呼ぶ。
    """
    review = assembly.review
    history = HistoryNotice.from_source(assembly.source)
    label = GROUP_BY_ID[group].label
    # 実行の途中で止めても、起動したことと終わった起動の金額が残るようにする
    with run_record.tracking(review, group, settings) as tracked:
        if group == MACRO:
            return PolicyGroupResult(macro.run_macro(assembly, tracked), history)
        # 方針の状態は止める理由にせず、実行の前に知らせる。標準出力は --json の本体に空ける
        policy_notice = order.policy_notice(review)
        if policy_notice is not None:
            print(
                f"注: 方針のレビュー: {policy_notice}。{label}のレビューを続けます",
                file=sys.stderr,
            )
        outcome = (
            micro.run_micro(assembly, tracked)
            if group == MICRO
            else security.run_security(assembly, tracked, mcp)
        )
        return ShardedGroupResult(label, outcome, policy_notice, history)


def run_review(
    identifier: str,
    group: GroupId,
    *,
    invoke: claude.Invoke = claude.run,
    effort: Effort | None = None,
    as_json: bool = False,
    mcps: Iterable[str] = (),
    mcp_guide: Path | None = None,
) -> int:
    """同意を取らずに、群の既定の形でレビューを走らせる。開発の道具が CLI を通さずに呼ぶ入口。

    公開の CLI はここを通らない。同意の無い起動は、開発の道具の側が費用を確かめて引き受ける。
    """
    review = load_review(resolve_review(identifier))
    shape = group_defaults(group)
    policy_shape = macro.policy_shape(review) if group == MACRO else None
    defaults = resolve_run_defaults(group, policy_shape, effort)
    settings = claude.RunSettings(
        budget=claude.RunBudget(
            defaults.budget.model, defaults.budget.max_budget_usd, shape.reserve_margin
        ),
        effort=defaults.effort,
        invoke=invoke,
        max_turns=shape.max_turns,
    )
    history = load_history_checked(review.id.directory())
    assembly = AssemblyInput.from_loaded(review, history)
    mcp = load_mcp_selection(mcps, group, mcp_guide)
    return print_group_result(run_group(assembly, group, settings, mcp=mcp), as_json=as_json)


def handle(args: argparse.Namespace, context: Context) -> int:
    review = load_review(resolve_review(args.review))
    defaults = resolve_run_defaults(
        args.group,
        policy_shape_of(args.group, review),
        args.effort,
        args.model,
        args.max_budget_usd_per_call,
    )
    settings = run_settings(args.group, context.invoke, defaults)
    group: GroupId = args.group
    mcp = load_mcp_selection(args.mcps, group, args.mcp_guide)
    # 前の回の指摘を資料にする前に読めるかを見る。実行の中で壊れが直ることは想定しない。
    # 同意の見積もりも実行の資料も、ここで読んだ履歴から組む。読むだけで、何も書き始めない
    history = load_history_checked(review.id.directory())
    assembly = AssemblyInput.from_loaded(review, history)
    # 同意が無ければ、記録の開始も含めて何も始めない
    require_consent(
        launch_plan(assembly, group, settings, mcp),
        is_terminal=context.is_terminal,
        read_answer=context.read_answer,
    )
    return print_group_result(run_group(assembly, group, settings, mcp=mcp), as_json=args.json)
