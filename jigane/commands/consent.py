"""`jigane run` が Claude Code CLI を起動する前に取る、利用者の同意。"""

import sys
from collections.abc import Callable
from dataclasses import dataclass

from jigane.commands.public_urls import installed_version, terms_urls
from jigane.git.process import CONSENT_PROMPT_END
from jigane.groups.definitions import GROUP_BY_ID, MACRO, MICRO, SECURITY, Effort, GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.defaults import resolve_run_defaults
from jigane.reviews.lifecycle import ReviewError
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import Estimate
from jigane.skills.estimator import estimate_of
from jigane.skills.mcp import ALL_TOOLS_NOTICE, NO_MCP, McpSelection


class ConsentDeclinedError(Exception):
    """利用者が確認に y と答えなかった。"""


class ConsentUnavailableError(Exception):
    """標準入力が端末でないので、確認を取れなかった。"""


@dataclass(frozen=True)
class GroupBreakdown:
    """一つの群について、起動の前に分かる分の内訳。"""

    group: GroupId
    discovery_calls: int
    per_call_usd: float
    # 検証役を持つ群の回数は見つかった指摘の数で決まり、事前には分からない
    has_verifier: bool
    # 記録にある前回の実行の実績。記録が無ければ None
    past_cost_usd: float | None

    @property
    def known_usd(self) -> float:
        return self.discovery_calls * self.per_call_usd


@dataclass(frozen=True)
class LaunchPlan:
    """この実行が最初に分かる範囲で起動する回数と、1 回あたりの上限の目安。

    回数と目安の出所は groups だけで、他の値はそこから導く。
    """

    group: GroupId
    # 同じレビューの群の内訳。この実行の群を必ず含む
    groups: tuple[GroupBreakdown, ...]
    can_increase: bool
    effort: Effort | None
    # 出力と推論の分を含めた見込み。見込みを持つのは設計書の形の方針だけで、他は None
    expected_total_usd: float | None = None
    mcp: McpSelection = NO_MCP

    def __post_init__(self) -> None:
        if sum(item.group == self.group for item in self.groups) != 1:
            raise ValueError(f"groups に {self.group} の内訳がちょうど 1 つ無い")

    @property
    def breakdown(self) -> GroupBreakdown:
        return next(item for item in self.groups if item.group == self.group)

    @property
    def minimum_calls(self) -> int:
        return self.breakdown.discovery_calls

    @property
    def max_budget_usd_per_call(self) -> float:
        return self.breakdown.per_call_usd

    @property
    def known_usd(self) -> float:
        return sum(item.known_usd for item in self.groups)


def _breakdown_of(group: GroupId, estimate: Estimate, per_call_usd: float) -> GroupBreakdown:
    past = estimate.past_run
    return GroupBreakdown(
        group,
        estimate.discovery_calls,
        per_call_usd,
        estimate.has_verifier,
        None if past is None else past["cost_usd"],
    )


def launch_plan(
    assembly: AssemblyInput,
    group: GroupId,
    settings: claude.RunSettings,
    mcp: McpSelection = NO_MCP,
) -> LaunchPlan:
    """見積もりと同じ関数から、確実に起動する回数を求める。"""
    budget = settings.budget.max_budget_usd
    estimates = {group: estimate_of(assembly, group, mcp)}
    for each in (MACRO, MICRO, SECURITY):
        if each in estimates:
            continue
        try:
            estimates[each] = estimate_of(assembly, each)
        except ReviewError:
            # 他の群の材料が欠けていても、この群の実行は止めない。その群は表から外す
            continue
    breakdowns = tuple(
        _breakdown_of(
            each,
            estimates[each],
            budget
            if each == group
            else resolve_run_defaults(
                each, estimates[each].policy_shape(), None
            ).budget.max_budget_usd,
        )
        for each in (MACRO, MICRO, SECURITY)
        if each in estimates
    )
    # 方針は発見役の 1 回だけで検証役を持たない。他は発見役がシャードごとに 1 回で、
    # 検証役は指摘の数で決まり、数にも分割にも上限が無い
    # （finding_model.py の発見役の出力のモデルは findings の件数に上限を持たず、
    # split_candidates は 10 件ずつ割る）。
    # 検証役は最小の回数には入らない
    return LaunchPlan(
        group,
        breakdowns,
        group != MACRO,
        settings.effort,
        estimates[group].expected_total_usd(),
        mcp=NO_MCP if group == MACRO else mcp,
    )


def _group_row(item: GroupBreakdown) -> str:
    verifier = "検証役は回数が未定" if item.has_verifier else "検証役なし"
    past = "" if item.past_cost_usd is None else f"、前回の実績 {item.past_cost_usd:.4f} USD"
    return (
        f"  {GROUP_BY_ID[item.group].label}: 発見役 {item.discovery_calls} 回 × 1 回の目安 "
        f"{item.per_call_usd:g} USD = {item.known_usd:g} USD、{verifier}{past}"
    )


def consent_text(
    plan: LaunchPlan, version_lookup: Callable[[], str | None] = installed_version
) -> list[str]:
    label = GROUP_BY_ID[plan.group].label
    calls = (
        f"少なくとも {plan.minimum_calls} 回。検証役の回数に上限はありません"
        "（見つかった指摘の数で決まります）"
        if plan.can_increase
        else f"{plan.minimum_calls} 回"
    )
    per_call = plan.max_budget_usd_per_call
    mcp = (
        [f"使う MCP: {', '.join(plan.mcp.names)}", f"{ALL_TOOLS_NOTICE}。"]
        if plan.mcp.servers
        else ["MCP は使いません"]
    )
    lines = [
        f"{label}の実行は Claude Code CLI（claude -p）を起動します。",
        *mcp,
        f"起動の回数: {calls}",
        f"effort: {plan.effort or '渡さない（Claude Code CLI の既定）'}",
        f"この群の内訳（{label}）:",
        (
            f"  発見役: {plan.minimum_calls} 回 × 1 回の目安 {per_call:g} USD"
            f" = {plan.minimum_calls * per_call:g} USD"
        ),
    ]
    if plan.can_increase:
        lines.append("  検証役: 回数は見つかった指摘の数で決まり、事前には分かりません")
    if plan.expected_total_usd is not None:
        lines.append(
            f"出力と推論の分を含めた見込み: {plan.expected_total_usd:.2f} USD"
            + (
                "（1 回の起動の額に達する見込みです。警告して続けます）"
                if plan.expected_total_usd >= per_call
                else ""
            )
        )
    lines.append("同じレビューの群ごとの内訳:")
    lines.extend(_group_row(item) for item in plan.groups)
    lines.append(
        f"  発見役の回数 × 1 回の目安の和（検証役の分を含まない）: {plan.known_usd:g} USD"
    )
    lines.append(
        "実際の額は、検証役の回数と出力の量で変わり、この額より少ないことも多いこともあります。"
    )
    lines.append("1 回の目安は往復の合間にしか確かめられないので、超えることがあります。")
    security = terms_urls(version_lookup()).security
    lines.append(f"渡す情報の一覧: {security} の「レビューの入力と Claude Code」の節")
    return lines


def is_yes(answer: str) -> bool:
    """確認への答えが y か yes（大文字小文字と前後の空白を問わない）か。"""
    return answer.strip().lower() in ("y", "yes")


def require_consent(
    plan: LaunchPlan,
    *,
    is_terminal: Callable[[], bool],
    read_answer: Callable[[str], str],
) -> None:
    """y か yes のときだけ返る。

    それ以外の答えは ConsentDeclinedError、端末でないときは ConsentUnavailableError。
    """
    if not is_terminal():
        raise ConsentUnavailableError("同意を得られないので起動しません。端末から実行してください")
    for line in consent_text(plan):
        print(line, file=sys.stderr)
    if not is_yes(read_answer(f"続けますか{CONSENT_PROMPT_END}")):
        raise ConsentDeclinedError("同意が得られなかったので起動しませんでした")
