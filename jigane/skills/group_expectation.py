"""群の見込み。発見役の見積もりに検証役の倍率を掛けた額を、すべての群で一つの定義で出す。

Claude Code の倍率は回の記録から引く。Codex CLI は担当モデルごとの入出力と既定の倍率を使う。
見積もり全体を走らせるのは組み立ての根（estimate と run の入口）で、ここでは走らせない。
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jigane.groups.definitions import GroupId
from jigane.llm_runs import claude, codex, pricing
from jigane.reviews.catalog import ARCHIVED_INCLUDE, list_review_summaries
from jigane.skills.estimate_result import EstimateCommon
from jigane.skills.launch_prompts import LaunchPrompt
from jigane.skills.pipeline import ESTIMATE_COUNTING_VERSION, read_runs

# 見込みの組み立てを変えたら上げる。jigane estimate --json の expected.version
EXPECTATION_VERSION = 1
# 検証役を持たない群（方針）の倍率。1 を明示の値として持つ
NO_VERIFIER_MULTIPLIER = 1.0
# 回の記録に実額が無いときの検証役の倍率の仮置き。測定で後から決め直す
DEFAULT_VERIFIER_MULTIPLIER = 2.5
# Codex CLI の初期起動 1 回あたりの出力・推論の見込み。CLI に渡す制限値ではない。
CODEX_LOGIN_OUTPUT_TOKENS = 8_192


class VerifierMultiplierSource(StrEnum):
    NO_VERIFIER = "no_verifier"
    RUN_RECORDS = "run_records"
    DEFAULT = "default"


@dataclass(frozen=True)
class GroupExpectation:
    discovery_usd: float
    multiplier: float
    source: VerifierMultiplierSource

    @property
    def total_usd(self) -> float:
        return self.discovery_usd * self.multiplier

    def as_record(self) -> dict[str, Any]:
        return {
            "total_usd": self.total_usd,
            "version": EXPECTATION_VERSION,
            "discovery_estimate_usd": self.discovery_usd,
            "verifier_multiplier": self.multiplier,
            "verifier_multiplier_source": self.source.value,
        }


def _read_group_multiplier(repository_root: Path, group: GroupId) -> float | None:
    """置き場にある同じリポジトリの全レビューの、その群の回の記録から引く倍率の平均。

    今の数え方の版の欄を持つ回だけを数え、群の実額を発見役の見積もりで割る。
    runs.json は群ごとに最新の回だけを持つので、同じレビューに限ると標本が増えない。
    """
    ratios: list[float] = []
    for review in list_review_summaries(ARCHIVED_INCLUDE):
        if review.repository_root != str(repository_root):
            continue
        run = read_runs(review.id.directory()).get(group)
        stamp = None if run is None else run.estimate_stamp
        if run is None or stamp is None or stamp.counting_version != ESTIMATE_COUNTING_VERSION:
            continue
        if stamp.discovery_usd > 0 and run.cost_usd > 0:
            ratios.append(run.cost_usd / stamp.discovery_usd)
    return sum(ratios) / len(ratios) if ratios else None


def discovery_estimate_usd(estimate: EstimateCommon, basis: claude.RunBudget) -> float:
    """jigane estimate と同じ見積もりから出す、発見役の見積もり。

    設計書の形は出力と推論の分を含めた見込みを発見役の見積もりとして持つので、それを使う。
    """
    expected = estimate.expected_output()
    if expected is not None:
        return expected.total_usd
    cost = estimate.input_cost(basis)
    return 0.0 if cost is None else cost.cost.usd


def read_expectation(
    estimate: EstimateCommon, basis: claude.RunBudget, repository_root: Path, group: GroupId
) -> GroupExpectation:
    """回の記録から倍率を引いて群の見込みを組む。組み立ての根で一度だけ呼ぶ。"""
    discovery_usd = discovery_estimate_usd(estimate, basis)
    if not estimate.has_verifier:
        return GroupExpectation(
            discovery_usd, NO_VERIFIER_MULTIPLIER, VerifierMultiplierSource.NO_VERIFIER
        )
    from_records = _read_group_multiplier(repository_root, group)
    if from_records is not None:
        return GroupExpectation(discovery_usd, from_records, VerifierMultiplierSource.RUN_RECORDS)
    return GroupExpectation(
        discovery_usd, DEFAULT_VERIFIER_MULTIPLIER, VerifierMultiplierSource.DEFAULT
    )


def codex_login_expectation(
    prompts: tuple[LaunchPrompt, ...], *, has_verifier: bool
) -> GroupExpectation | None:
    """初期起動ごとの入力・出力と、追加検証を含む単群の USD 換算見込み。"""
    if not prompts:
        return None
    prefix_size = len(codex.REVIEW_BOUNDARY.encode("utf-8"))
    nanos = sum(
        prompt.codex_model().estimate_nanos(
            pricing.estimate_input_tokens(prompt.size + prefix_size), CODEX_LOGIN_OUTPUT_TOKENS
        )
        for prompt in prompts
    )
    discovery_usd = nanos / codex.NANOS_PER_USD
    if has_verifier:
        return GroupExpectation(
            discovery_usd, DEFAULT_VERIFIER_MULTIPLIER, VerifierMultiplierSource.DEFAULT
        )
    return GroupExpectation(
        discovery_usd, NO_VERIFIER_MULTIPLIER, VerifierMultiplierSource.NO_VERIFIER
    )
