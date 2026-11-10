"""起動の失敗の表示。保存の型と選び方の事実は launch_failure が持つ。"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from jigane.llm_runs.claude import FailureKind
from jigane.llm_runs.launch_failure import (
    EarlierFailures,
    FailureRound,
    FailureScope,
    SelectedFailure,
    Stage,
    StoredFailure,
    earlier_failures,
    selected_failures,
)


@dataclass(frozen=True)
class FailureView:
    """保存した失敗を画面向けにした一件。show と実行の結果はこの型から引く。"""

    stage: Stage
    scope: FailureScope
    index: int
    role: str | None
    kind: FailureKind
    reason: str
    # 代表の失敗を言う一文。走らせ直しの回数は含めない
    line: str
    # 同じ起動単位で実際に起動した走らせ直しの回数
    reruns: int
    # 同じ起動単位で、入力の見積もりで止めて起動しなかった走らせ直しの回数
    stopped_reruns: int

    @property
    def text(self) -> str:
        """この失敗を言う一文。文の組み立てはここだけが持つ。"""
        launched = f"、走らせ直し {self.reruns} 回" if self.reruns else ""
        stopped = "、走らせ直しは見積もりで止めた" if self.stopped_reruns else ""
        return self.line + launched + stopped

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "scope": self.scope.value,
            "index": self.index,
            "role": self.role,
            "kind": self.kind.value,
            "reason": self.reason,
            "reruns": self.reruns,
            "stopped_reruns": self.stopped_reruns,
            "text": self.text,
        }


def failure_view_of(selected: SelectedFailure) -> FailureView:
    """選んだ失敗の項目から表示向けの一件を作る。文の元は代表の失敗が持つ。"""
    last = selected.failure
    return FailureView(
        last.stage,
        last.scope,
        last.index,
        last.role,
        last.kind,
        last.reason,
        last.line,
        selected.reruns,
        selected.stopped_reruns,
    )


def failure_views(
    failures: Iterable[StoredFailure], failure_round: FailureRound = FailureRound.CURRENT
) -> tuple[FailureView, ...]:
    """selected_failures の各件を画面向けの一件にする。選び方は selected_failures が持つ。"""
    return tuple(failure_view_of(item) for item in selected_failures(failures, failure_round))


@dataclass(frozen=True)
class EarlierFailuresView:
    """前の回の失敗の要約の表示。画面は一行に畳み、一件ずつは開けば見せる。

    文は四つの項を別々に言い、0 の項は出さない。通らなかった失敗の件数（views）、走らせ直して
    通った起動、額、額の記録なしの失敗である。
    """

    facts: EarlierFailures
    views: tuple[FailureView, ...]

    @property
    def text(self) -> str:
        """一行の文。0 の項は出さない。文の組み立てはここだけが持つ。"""
        facts = self.facts
        parts = []
        if self.views:
            parts.append(f"通らなかった失敗 {len(self.views)} 件")
        if facts.recovered_count:
            parts.append(f"回復した起動 {facts.recovered_count} 回")
        if facts.totals.usd:
            estimated = (
                f"（うち推定 {facts.totals.limit_substitute:.2f} USD）"
                if facts.totals.limit_substitute
                else ""
            )
            parts.append(f"計 {facts.totals.usd:.2f} USD{estimated}")
        if facts.unrecorded_cost_count:
            parts.append(f"額の記録なし {facts.unrecorded_cost_count} 件")
        return "前の回: " + "、".join(parts)

    def to_json(self) -> dict[str, Any]:
        facts = self.facts
        return {
            # 通らなかった失敗の起動単位の数。意味は従来のまま
            "count": len(self.views),
            "recovered_count": facts.recovered_count,
            "unrecorded_cost_count": facts.unrecorded_cost_count,
            # 課金のある起動ではなく、起動の記録を持つ全失敗の数。意味は従来のまま
            "invocation_count": facts.invocation_count,
            "cost_usd": facts.totals.usd,
            "substituted_cost_usd": facts.totals.limit_substitute,
            "text": self.text,
            "failures": [view.to_json() for view in self.views],
        }


def earlier_failures_view(failures: Iterable[StoredFailure]) -> EarlierFailuresView | None:
    """前の回の失敗の要約の表示。事実が無ければ None。"""
    facts = earlier_failures(failures)
    if facts is None:
        return None
    return EarlierFailuresView(facts, tuple(failure_view_of(item) for item in facts.selected))


def earlier_failures_json(failures: Iterable[StoredFailure]) -> dict[str, Any] | None:
    """run --json と show --json が共有する、前の回の失敗の欄。"""
    earlier = earlier_failures_view(failures)
    return None if earlier is None else earlier.to_json()
