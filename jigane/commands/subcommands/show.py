"""`jigane show`: レビューを表示する。"""

import argparse
import json
from collections.abc import Sequence
from typing import Any, Final, Literal

from jigane.commands import output
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.group_argument import parse_group
from jigane.commands.output import print_group_json, print_lines
from jigane.commands.views.findings import (
    macro_view_lines,
    micro_view_lines,
    received_concern_lines,
    security_view_lines,
    selection_marks,
    stopped_lines,
)
from jigane.commands.views.review import (
    background_lines,
    bot_lines,
    declaration_lines,
    gate_lines,
    map_lines,
    selection_lines,
    source_lines,
    static_finding_lines,
    static_findings_json,
    warning_lines,
)
from jigane.findings.discard import NO_CONCERN_CLAIM, concern_with_places, places_line
from jigane.findings.discarded_nits import is_legacy_nit_id, selection_note_of
from jigane.findings.records import (
    TriagedFinding,
    display_category_of,
    load_findings_state,
    load_history_checked,
    triage_sort_key,
)
from jigane.groups.definitions import MACRO, MICRO, SECURITY, GroupId
from jigane.llm_runs import launch_failure
from jigane.reviews import selection as selection_module
from jigane.reviews.lifecycle import Review, load_review, resolve_review
from jigane.skills import macro, micro, pipeline, security
from jigane.skills import run_record as run_record_module
from jigane.skills.group_result import (
    NOT_RUN_OUTCOME,
    Failed,
    GroupOutcome,
    HasFindings,
    NoFindings,
    NotRun,
    group_outcome,
    outcome_line,
)
from jigane.skills.legacy_nits import legacy_nits_of
from jigane.skills.received_concerns import NotRecorded, received_concerns

OVERVIEW: Final = "overview"
View = GroupId | Literal["overview"]


def parse_view(text: str) -> View:
    """副コマンドの画面。群の画面か、群を選ばない概要。"""
    if text == OVERVIEW:
        return OVERVIEW
    return parse_group(text)


STALE_NOTE = "注: refresh の後に再実行していない結果です"


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    show = subparsers.add_parser("show", help="レビューを表示します")
    show.add_argument("review")
    show.add_argument(
        "view",
        nargs="?",
        type=parse_view,
        choices=(OVERVIEW, *GroupId),
        default=OVERVIEW,
    )
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=run)


def group_run(review: Review, group: GroupId) -> pipeline.GroupRun | None:
    """runs.json のその群の記録。未実行なら None。"""
    return pipeline.read_runs(review.id.directory()).get(group)


def run_record(review: Review, group: GroupId) -> dict[str, Any] | None:
    """その群の実行の記録。未実行なら None。0 件で終わった実行も記録は残る。"""
    run = group_run(review, group)
    if run is None:
        return None
    totals = pipeline.summarize_invocations(run)
    return {
        "ran_at": run.ran_at,
        "elapsed_s": run.elapsed_s,
        "model": run.model,
        "invocation_count": totals["invocation_count"],
        "cost_usd": totals["cost_usd"],
        # cost_usd のうち、金額が報告されず上限の額で代えた分
        "substituted_cost_usd": totals["substituted_cost_usd"],
        "shards": run.shards if isinstance(run, pipeline.ShardedGroupRun) else None,
        "succeeded_shards": (
            run.succeeded_shards if isinstance(run, pipeline.ShardedGroupRun) else None
        ),
        "mcp_servers": (
            None if not isinstance(run, pipeline.ShardedGroupRun) else run.mcp.to_stored()
        ),
        # 検証の分割が失敗した数と、判定の付かなかった候補の数。
        # 実装とセキュリティの群の実行だけが持つ
        "failed_splits": run.failed_splits if isinstance(run, pipeline.ShardedGroupRun) else None,
        "unverified_candidates": (
            run.unverified_candidates if isinstance(run, pipeline.ShardedGroupRun) else None
        ),
        # 上限を超えた起動の数
        "over_budget": totals["over_budget"],
        # 最終的に通らなかった起動の失敗（上限で失敗を含む）。種類と理由、額とその出所を添えた文
        "failures": [
            view.to_json() for view in launch_failure.failure_views(run.launch_failures or ())
        ],
        # 今回の回の前に起きた失敗の件数と額。無ければ None
        "earlier_failures": launch_failure.earlier_failures_json(run.launch_failures or ()),
    }


def shown_failure_lines(run: pipeline.GroupRun | None, outcome: GroupOutcome) -> list[str]:
    """通らなかった起動の失敗の文。

    指摘がある群の帯は失敗を言わず、前の回の指摘が残るので、最新の実行の失敗はここで言う。
    """
    if run is None or isinstance(outcome, Failed):
        # Failed の帯が理由として失敗を言っている
        return []
    lines = [view.text for view in launch_failure.failure_views(run.launch_failures or ())]
    earlier = launch_failure.earlier_failures(run.launch_failures or ())
    if earlier is not None:
        lines += [earlier.text, *(f"  {view.text}" for view in earlier.views)]
    return lines


def outcome_of(review: Review, group: GroupId, findings: Sequence[TriagedFinding]) -> GroupOutcome:
    return group_outcome(group_run(review, group), len(findings))


def stopped_view(review: Review, group: GroupId) -> run_record_module.StoppedView:
    return run_record_module.stopped_view(review.id.directory(), group)


def outcome_lines(outcome: GroupOutcome) -> list[str]:
    line = outcome_line(outcome)
    return [] if line is None else [line]


def show_not_run(review: Review, group: GroupId, *, as_json: bool, extra: dict[str, Any]) -> None:
    # 未実行は失敗ではなく状態なので、終了コード 0 で知らせる
    view = stopped_view(review, group)
    if as_json:
        body = {
            "stale": False,
            "run": None,
            "findings": [],
            **NOT_RUN_OUTCOME.to_json(),
            **extra,
            **view.to_json(),
        }
        print_group_json(group, body, review)
        return
    print_lines(
        output.warning_lines(load_history_checked(review.id.directory()).warnings),
        [f"{group} は未実行です。`jigane run {review.id} {group}` で実行してください"],
        stopped_lines(view),
    )


def _with_derived_nit_reason(finding: TriagedFinding) -> dict[str, Any]:
    """nit_reason 欄に、実装のレビューへ渡したかの導出値を出力時だけ載せる。

    保存の形は変えない。
    """
    raw = finding.to_json()
    reason = finding.finding.nit_reason
    if reason is None:
        return raw
    derived: dict[str, Any] = {"sent_to_micro": reason.sent_to_micro}
    concern_text = concern_with_places(reason)
    if concern_text is not None:
        derived["concern_text"] = concern_text
    else:
        # 懸念の文が無い nit の claim は捨てた理由の文なので、代えて出さない
        raw = {**raw, "claim": NO_CONCERN_CLAIM}
    line = places_line(reason)
    if line is not None:
        derived["places_line"] = line
    return {**raw, "nit_reason": {**reason.to_json(), **derived}}


def with_in_reply(
    findings: Sequence[TriagedFinding], *, with_display_category: bool = False
) -> list[dict[str, Any]]:
    """返信の画面が描くだけで済むよう、各指摘に返信へ載るかと指摘の種類の欄を足す。"""
    return [
        {
            **_with_derived_nit_reason(f),
            # 古い記録から組み立てた nit は返信とレポートの候補に入れない
            "in_reply": not is_legacy_nit_id(f.finding.id),
            "selection_note": selection_note_of(f.finding.id),
            # 指摘の種類は from_json が一度だけ決める。
            # kind_mark は必読と分けて見せる種類の札で、通常の指摘は None
            "kind": f.finding.kind.value,
            "kind_mark": f.finding.kind.traits.mark,
            **(
                {"display_category": display_category_of(f).value} if with_display_category else {}
            ),
        }
        for f in findings
    ]


def show_macro(review: Review, *, as_json: bool) -> None:
    state = load_findings_state(review.id.directory())
    # 指摘が無くても、起動の失敗は runs.json に残る。未実行かは group_outcome が決める
    findings = state.groups.get(macro.GROUP, ())
    outcome = outcome_of(review, macro.GROUP, findings)
    if macro.GROUP not in state.groups and isinstance(outcome, NotRun):
        show_not_run(review, macro.GROUP, as_json=as_json, extra={})
        return
    stale = macro.GROUP in state.stale_groups
    legacy_nits = legacy_nits_of(review.id.directory(), findings)
    if legacy_nits and isinstance(outcome, NoFindings):
        # nit の行が並ぶ結果を「指摘なし」とは読ませない。
        # 保存物の outcome は変えず、見せ方だけ指摘のある群に揃える。
        outcome = HasFindings()
    shown = [*findings, *legacy_nits]
    ordered = sorted(shown, key=triage_sort_key)
    macro_run = group_run(review, macro.GROUP)
    considered = None if macro_run is None else macro_run.considered
    if as_json:
        run = run_record(review, macro.GROUP)
        if run is not None:
            run["considered"] = None if considered is None else considered.to_json()
        print_group_json(
            macro.GROUP,
            {
                "stale": stale,
                **outcome.to_json(),
                "run": run,
                "findings": with_in_reply(ordered),
                **stopped_view(review, macro.GROUP).to_json(),
            },
            review,
        )
        return
    print_lines(
        output.warning_lines(load_history_checked(review.id.directory()).warnings),
        [STALE_NOTE] if stale else [],
        stopped_lines(stopped_view(review, macro.GROUP)),
        outcome_lines(outcome),
        shown_failure_lines(macro_run, outcome),
        macro_view_lines(review, ordered, selection_marks(review), considered),
    )


def show_micro(review: Review, *, as_json: bool) -> None:
    state = load_findings_state(review.id.directory())
    if micro.GROUP not in state.groups:
        show_not_run(
            review,
            micro.GROUP,
            as_json=as_json,
            extra={"themes": [], "received_concerns": NotRecorded().to_json()},
        )
        return
    findings = state.groups[micro.GROUP]
    stale = micro.GROUP in state.stale_groups
    outcome = outcome_of(review, micro.GROUP, findings)
    received = received_concerns(group_run(review, micro.GROUP), findings)
    if as_json:
        print_group_json(
            micro.GROUP,
            {
                "stale": stale,
                **outcome.to_json(),
                "run": run_record(review, micro.GROUP),
                "findings": with_in_reply(findings, with_display_category=True),
                "display_categories": output.display_category_table(),
                "themes": [theme.to_json() for theme in state.themes],
                "received_concerns": received.to_json(),
                **stopped_view(review, micro.GROUP).to_json(),
            },
            review,
        )
        return
    print_lines(
        output.warning_lines(load_history_checked(review.id.directory()).warnings),
        [STALE_NOTE] if stale else [],
        stopped_lines(stopped_view(review, micro.GROUP)),
        outcome_lines(outcome),
        shown_failure_lines(group_run(review, micro.GROUP), outcome),
        micro_view_lines(findings, state.themes, selection_marks(review)),
        received_concern_lines(received),
    )


def show_security(review: Review, *, as_json: bool) -> None:
    state = load_findings_state(review.id.directory())
    if security.GROUP not in state.groups:
        show_not_run(review, security.GROUP, as_json=as_json, extra={})
        return
    findings = state.groups[security.GROUP]
    stale = security.GROUP in state.stale_groups
    outcome = outcome_of(review, security.GROUP, findings)
    if as_json:
        print_group_json(
            security.GROUP,
            {
                "stale": stale,
                **outcome.to_json(),
                "run": run_record(review, security.GROUP),
                "findings": with_in_reply(findings),
                **stopped_view(review, security.GROUP).to_json(),
            },
            review,
        )
        return
    security_run = group_run(review, security.GROUP)
    print_lines(
        output.warning_lines(load_history_checked(review.id.directory()).warnings),
        [STALE_NOTE] if stale else [],
        stopped_lines(stopped_view(review, security.GROUP)),
        outcome_lines(outcome),
        [] if security_run is None else [pipeline.mcp_label(security_run)],
        shown_failure_lines(security_run, outcome),
        security_view_lines(findings, selection_marks(review)),
    )


def show_and_print(text: str, view: View, *, as_json: bool) -> None:
    review = load_review(resolve_review(text))
    if view == MICRO:
        show_micro(review, as_json=as_json)
        return
    if view == SECURITY:
        show_security(review, as_json=as_json)
        return
    if view == MACRO:
        show_macro(review, as_json=as_json)
        return
    history = load_history_checked(review.id.directory())
    if as_json:
        overview = {
            **review.to_dict(),
            "selection": selection_module.summary(review.id.directory(), history),
            "history_warnings": output.warnings_json(history.warnings),
        }
        static_findings = static_findings_json(review)
        if static_findings is not None:
            overview["static_findings"] = static_findings
        print(json.dumps(overview, ensure_ascii=False, indent=2))
        return
    print_lines(
        [f"レビュー: {review.id}（{review.mode}、{review.created_at}）"],
        source_lines(review),
        gate_lines(review),
        declaration_lines(review),
        background_lines(review),
        map_lines(review),
        selection_lines(review, history),
        bot_lines(review),
        static_finding_lines(review),
        warning_lines(review),
        output.warning_lines(history.warnings),
    )


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    show_and_print(args.review, args.view, as_json=args.json)
    return EXIT_OK
