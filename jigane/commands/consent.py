"""`jigane run` が Claude Code CLI を起動する前に取る、利用者の同意。"""

import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from jigane.commands.public_urls import installed_version, terms_urls
from jigane.git.process import CONSENT_PROMPT_END
from jigane.groups.definitions import GROUP_BY_ID, MACRO, Effort, GroupId
from jigane.judgment_map.map_kind import CodeMap
from jigane.languages import registry
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import DiagramPolicyEstimate, Estimate
from jigane.skills.estimator import estimate_of
from jigane.skills.mcp import ALL_TOOLS_NOTICE, NO_MCP, McpSelection
from jigane.text.terminal import one_line_text


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
    # 図の形の方針だけが持つ原則の起動。discovery_calls はこの本数を含む
    # 組み立てた原則ごとの上限。空は原則の組を持たない群
    principle_limits_usd: tuple[float, ...] = ()

    @property
    def principle_calls(self) -> int:
        return len(self.principle_limits_usd)

    @property
    def diagram_calls(self) -> int:
        return self.discovery_calls - self.principle_calls

    @property
    def known_usd(self) -> float:
        # 0.1 + 0.4 のような和の丸めの誤差を表示に出さない
        return round(
            self.diagram_calls * self.per_call_usd + sum(self.principle_limits_usd),
            6,
        )

    def calls_text(self) -> str:
        """起動の本数と目安の式。原則の組を持たなければ発見役の回数だけで表す。"""
        if self.principle_calls == 0:
            return (
                f"{self.discovery_calls} 回 × 1 回の目安 {self.per_call_usd:g} USD"
                f" = {self.known_usd:g} USD"
            )
        groups = Counter(self.principle_limits_usd)
        terms = [
            f"図の起動 {self.diagram_calls} 回 × 1 回の目安 {self.per_call_usd:g} USD",
            *(
                f"原則の起動 {count} 回 × 1 回の目安 {limit:g} USD"
                for limit, count in groups.items()
            ),
        ]
        return f"{' + '.join(terms)} = {self.known_usd:g} USD"


@dataclass(frozen=True)
class LaunchPlan:
    """この実行が最初に分かる範囲で起動する回数と、1 回あたりの上限の目安。

    回数と目安の出所は breakdown だけで、他の値はそこから導く。
    """

    breakdown: GroupBreakdown
    can_increase: bool
    effort: Effort | None
    # 出力と推論の分を含めた見込み。見込みを持つのは設計書の形の方針だけで、他は None
    expected_total_usd: float | None = None
    mcp: McpSelection = NO_MCP

    @property
    def group(self) -> GroupId:
        return self.breakdown.group

    @property
    def minimum_calls(self) -> int:
        return self.breakdown.discovery_calls

    @property
    def max_budget_usd_per_call(self) -> float:
        return self.breakdown.per_call_usd


def _breakdown_of(group: GroupId, estimate: Estimate, budget: claude.RunBudget) -> GroupBreakdown:
    past = estimate.past_run
    principle_limits = (
        tuple(
            budget.for_principle(number).max_budget_usd
            for number, _ in estimate.principle_input_tokens
        )
        if isinstance(estimate, DiagramPolicyEstimate)
        else ()
    )
    return GroupBreakdown(
        group,
        estimate.discovery_calls,
        budget.max_budget_usd,
        estimate.has_verifier,
        None if past is None else past["cost_usd"],
        principle_limits,
    )


def launch_plan(
    assembly: AssemblyInput,
    group: GroupId,
    settings: claude.RunSettings,
    mcp: McpSelection = NO_MCP,
) -> LaunchPlan:
    """見積もりと同じ関数から、確実に起動する回数を求める。"""
    estimate = estimate_of(assembly, group, mcp, settings.budget)
    # 方針は発見役の 1 回だけで検証役を持たない。他は発見役がシャードごとに 1 回で、
    # 検証役は指摘の数で決まり、数にも分割にも上限が無い
    # （finding_model.py の発見役の出力のモデルは findings の件数に上限を持たず、
    # split_candidates は 10 件ずつ割る）。
    # 検証役は最小の回数には入らない
    return LaunchPlan(
        _breakdown_of(group, estimate, settings.budget),
        group != MACRO,
        settings.effort,
        estimate.expected_total_usd(),
        mcp=NO_MCP if group == MACRO else mcp,
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
        f"  発見役: {plan.breakdown.calls_text()}",
    ]
    if plan.breakdown.has_verifier:
        lines.append("  検証役: 回数は見つかった指摘の数で決まり、事前には分かりません")
    else:
        lines.append("  検証役なし")
    if plan.breakdown.past_cost_usd is not None:
        lines.append(f"  前回の実績 {plan.breakdown.past_cost_usd:.4f} USD")
    if plan.expected_total_usd is not None:
        lines.append(
            f"出力と推論の分を含めた見込み: {plan.expected_total_usd:.2f} USD"
            + (
                "（1 回の起動の額に達する見込みです。警告して続けます）"
                if plan.expected_total_usd >= per_call
                else ""
            )
        )
    lines.append(
        "他の群の額は見積もっていません。`jigane estimate <レビュー> <群>` で確かめられます。"
    )
    lines.append(
        "実際の額は、検証役の回数と出力の量で変わります。この額は実額の予測ではありません。"
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


# 言語のソースでない文書とデータの形式。アダプタが無くても、宣言を読めない言語としては数えない
NON_SOURCE_SUFFIXES = frozenset(
    {
        ".md",
        ".markdown",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".txt",
        ".rst",
        ".csv",
        ".lock",
    }
)
# 拡張子ごとに示すファイルの数。残りは件数だけにする
_SHOWN_FILES_PER_SUFFIX = 5


def unadapted_files(review: Review) -> dict[str, list[str]]:
    """アダプタの無い言語のソースのファイルを、拡張子ごとにまとめる。無ければ空。"""
    # 設計書の地図の区間は参考のコードで、設計書のレビューは宣言を読む対象ではない
    if not isinstance(review.map_kind, CodeMap):
        return {}
    found: dict[str, list[str]] = {}
    for segment in review.map_kind.segments:
        path = segment.new_path or segment.old_path
        if path is None:
            # 新旧どちらの経路も無い区間は、数える対象のファイルを持たない
            continue
        suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
        if not suffix or suffix in NON_SOURCE_SUFFIXES:
            # 拡張子が無いと言語を決められず、文書とデータの形式は言語ではない
            continue
        if registry.adapter_for(path) is not None or registry.is_build_path(
            path, registry.adapters()
        ):
            # アダプタがあるか、ビルドのファイルで「重要」になるので、確認の対象にしない
            continue
        files = found.setdefault(suffix, [])
        if path not in files:
            files.append(path)
    return found


def unadapted_text(found: dict[str, list[str]]) -> list[str]:
    lines = ["アダプタの無い言語のファイルが、判断の地図の区間に含まれています。"]
    for suffix, files in sorted(found.items()):
        shown = ", ".join(one_line_text(name) for name in files[:_SHOWN_FILES_PER_SUFFIX])
        rest = len(files) - _SHOWN_FILES_PER_SUFFIX
        more = f" ほか {rest} 件" if rest > 0 else ""
        lines.append(f"  {one_line_text(suffix)}（{len(files)} 件）: {shown}{more}")
    lines.append("これらの区間は宣言を読まずに「確認」になります。")
    lines.append("アダプタは `jigane languages` で足せます。")
    return lines


def require_unadapted_consent(review: Review, *, read_answer: Callable[[str], str]) -> None:
    """アダプタの無い言語のファイルがあるとき、y か yes のときだけ返る。

    端末かどうかは費用の同意（require_consent）が先に確かめているので、ここでは確かめない。
    """
    found = unadapted_files(review)
    if not found:
        return
    for line in unadapted_text(found):
        print(line, file=sys.stderr)
    if not is_yes(read_answer(f"それでもレビューを実施しますか{CONSENT_PROMPT_END}")):
        raise ConsentDeclinedError("同意が得られなかったので起動しませんでした")
