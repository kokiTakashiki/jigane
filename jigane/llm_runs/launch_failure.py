"""起動の失敗の記録。

発見役と検証役が共有する一つの不変の型で、runs.json の `launch_failures` に入る。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from typing import Any, assert_never

from jigane.findings.discard import PassedConcern, ShardConcerns
from jigane.llm_runs.claude import FailureKind, StoredInvocationRecord
from jigane.llm_runs.cost import (
    UNREPORTED,
    BudgetOutcome,
    Cost,
    CostTotals,
    cost_as_record,
    cost_from_record,
    cost_text,
    total_by_source,
)
from jigane.llm_runs.prompt_stamp import PromptStamp

# runs.json の起動の role のうち発見役の語。書く側と読む側がこれを引く
DISCOVERY_ROLE = "discovery"


class Stage(StrEnum):
    DISCOVERY = "discovery"
    VERIFICATION = "verification"


class Disposition(StrEnum):
    """失敗のあとの扱い。"""

    # 走らせ直して通った
    RECOVERED = "recovered"
    # 走らせ直したが、また失敗した
    RERUN_FAILED = "rerun_failed"
    # 契機の後の走らせ直しが入力の見積もりで止まり、起動しなかった。契機の失敗だけが残る
    RERUN_STOPPED = "rerun_stopped"
    # 走らせ直さなかった（一時でない失敗、走らせ直さない段、走らせ直しの上限）
    NOT_RERUN = "not_rerun"


class FailureScope(StrEnum):
    """失敗が何の失敗か。"""

    # 発見役のシャード、または検証の分割の起動そのもの
    LAUNCH = "launch"
    # 検証の役の出力が一部の候補の index を欠いた。欠けた候補だけを走らせ直す
    MISSING_CANDIDATES = "missing_candidates"


class FailureRound(StrEnum):
    """失敗がどの回のものか。画面は CURRENT だけを出し、額の集計は両方を数える。"""

    # 群の記録の最新の回で起きた失敗
    CURRENT = "current"
    # 前の回で起きた失敗。新しい回を保存したとき、前の記録の失敗がこの値になる
    EARLIER = "earlier"


class RerunKind(StrEnum):
    """役の起動が、最初の実行か、どの走らせ直しか。invocations の行の `rerun` 欄に保存する。"""

    INITIAL = "initial"
    # 一時の失敗のあとの、分割ごとの走らせ直し
    SPLIT = "split"
    # 出力が欠けた候補だけでの、役の走らせ直し
    MISSING_CANDIDATES = "missing_candidates"


class RunRecordKind(StrEnum):
    """runs.json の方針の記録が何を表すか。記録の `record_kind` 欄に保存する。"""

    # 成功した回だけの記録
    SUCCEEDED = "succeeded"
    # 成功した回（前の指摘を生んだ回）の記録に、その後の起動の失敗を足したもの
    SUCCEEDED_THEN_FAILED = "succeeded_then_failed"
    # 失敗だけの記録（指摘を生んだ回が無い。初回の失敗など）
    FAILED_ONLY = "failed_only"

    @classmethod
    def of_record(cls, state: dict[str, Any]) -> "RunRecordKind":
        """記録の種類を読む唯一の場所。欄は版 1 からの移しが埋めるので、必ずある。"""
        return cls(state["record_kind"])

    @property
    def has_previous_run(self) -> bool:
        return self is not RunRecordKind.FAILED_ONLY


_KEY_CONCERNS = "concerns"
_KEY_PROMPT_VERSION = "prompt_version"
_KEY_PROMPT_CHARS = "prompt_chars"
# 過去に書いていた欄。読んだ行の others に残さず捨てる
_KEY_RETIRED_SESSION_ID = "session_id"


@dataclass(frozen=True)
class StoredInvocation:
    """runs.json の invocations の一行。起動の記録に、役と分割の番号と上限の結果を足したもの。

    起動の行の保存の形を書くのは to_json だけで、実行時に組む行も読んだ行も、失敗から導く行も
    この型を通る。
    """

    role: str | None
    split_index: int | None
    record: StoredInvocationRecord
    # プロンプトの版と長さを持たない古い行は None。
    # 片方の鍵だけを持つ行も None にして、その鍵は others に残す（書き戻しで形を変えないため）
    stamp: PromptStamp | None
    # 最初の実行は None で、走らせ直しの起動だけが値を持つ
    rerun: RerunKind | None
    # 上限について結果が無い行は None。
    # 値は StoredInvocationRecord.budget_outcome から導いたものだけ
    budget_outcome: BudgetOutcome | None
    # 発見役の起動が渡した懸念（渡した文と、件数にまとめた分。空もありうる）。
    # 発見役でない起動と古い行は None で、欄を書かない
    concerns: ShardConcerns | None
    others: Mapping[str, Any]

    @classmethod
    def of(
        cls,
        role: str | None,
        split_index: int | None,
        record: StoredInvocationRecord,
        stamp: PromptStamp,
        *,
        rerun: RerunKind | None = None,
        failure_kind: FailureKind | None = None,
        concerns: ShardConcerns | None = None,
    ) -> "StoredInvocation":
        """実行時に起動の行を組む口。結果は記録と失敗の種類から導く。"""
        return cls(
            role,
            split_index,
            record,
            stamp,
            rerun,
            record.budget_outcome(failure_kind),
            concerns,
            {},
        )

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> "StoredInvocation":
        record = StoredInvocationRecord.from_row(row)
        # 保存された budget_outcome は信じず、記録から導き直す
        outcome = record.budget_outcome(None)
        if outcome is None and record.max_budget_usd is None and "budget_outcome" in row:
            # 上限を持たない古い行は導けない。移し（runs.json の版 1）が旧い旗から書いた値を読む
            outcome = BudgetOutcome(row["budget_outcome"])
        rerun = row.get("rerun")
        version = row.get("prompt_version")
        chars = row.get("prompt_chars")
        stamp = None if version is None or chars is None else PromptStamp(version, chars)
        own = {
            "role",
            "split_index",
            *(() if stamp is None else (_KEY_PROMPT_VERSION, _KEY_PROMPT_CHARS)),
            "rerun",
            "budget_outcome",
            "cost_usd",
            "cost_source",
            _KEY_CONCERNS,
            _KEY_RETIRED_SESSION_ID,
            *(item.name for item in fields(StoredInvocationRecord)),
        }
        stored_concerns = row.get(_KEY_CONCERNS)
        return cls(
            row["role"],
            row["split_index"],
            record,
            stamp,
            None if rerun is None else RerunKind(rerun),
            outcome,
            None
            if stored_concerns is None
            else ShardConcerns(
                tuple(
                    PassedConcern(item["id"], item["text"]) for item in stored_concerns["passed"]
                ),
                stored_concerns["unspecified"],
            ),
            {key: item for key, item in row.items() if key not in own},
        )

    def to_json(self) -> dict[str, Any]:
        stored: dict[str, Any] = {
            "role": self.role,
            "split_index": self.split_index,
            **self.record.as_row(),
        }
        if self.stamp is not None:
            stored[_KEY_PROMPT_VERSION] = self.stamp.version
            stored[_KEY_PROMPT_CHARS] = self.stamp.chars
        if self.rerun is not None:
            stored["rerun"] = self.rerun.value
        if self.budget_outcome is not None:
            stored["budget_outcome"] = self.budget_outcome.value
        if self.concerns is not None:
            stored[_KEY_CONCERNS] = {
                "passed": [
                    {"id": concern.id, "text": concern.text} for concern in self.concerns.passed
                ],
                "unspecified": self.concerns.unspecified,
            }
        return {**stored, **self.others}


BUDGET_FAILURE_TITLE = "上限で失敗"
LAUNCH_FAILURE_TITLE = "起動が失敗"
MISSING_CANDIDATES_TITLE = "候補の検証が欠けた"


@dataclass(frozen=True)
class FailedInvocation:
    """失敗した起動のトークンなどの記録と、渡したプロンプトの版と長さ。

    額と出所は record.cost が持つ。
    """

    # 書く側は InvocationRecord、読み込んだ古い記録は上限を欠きうる StoredInvocationRecord
    record: StoredInvocationRecord
    prompt_version: str
    prompt_chars: int


@dataclass(frozen=True)
class _FailureFields:
    """保存する失敗の型が共有する欄と、欄だけから決まる読み取り。

    段階（stage）と役の有無は種類ごとの型が持つ。role の型は各型が絞る。
    """

    # 発見役ではシャードの番号、検証役では分割の番号
    index: int
    role: str | None
    kind: FailureKind
    reason: str
    attempt: int
    disposition: Disposition
    # 失敗を記録した時刻。持たない失敗は None
    ran_at: str | None = None
    # 失敗した起動の記録。起動の記録が無い失敗は None
    invocation: FailedInvocation | None = None
    round: FailureRound = FailureRound.CURRENT

    @property
    def stage(self) -> Stage:
        raise NotImplementedError

    @property
    def cost(self) -> Cost:
        """失敗した起動で使った額とその出所。起動の記録が無い失敗は Unreported。"""
        return UNREPORTED if self.invocation is None else self.invocation.record.cost

    @property
    def rerun(self) -> bool:
        return self.disposition is not Disposition.NOT_RERUN

    def _common_record(self) -> dict[str, Any]:
        invocation = self.invocation
        return {
            "stage": self.stage.value,
            "index": self.index,
            "role": self.role,
            "kind": self.kind.value,
            "reason": self.reason,
            "attempt": self.attempt,
            "disposition": self.disposition.value,
            "round": self.round.value,
            **cost_as_record(self.cost),
            "ran_at": self.ran_at,
            # 起動の記録が無い失敗は欄ごと持たない
            **(
                {}
                if invocation is None
                else {
                    "invocation": {
                        **invocation.record.usage_fields(),
                        "prompt_version": invocation.prompt_version,
                        "prompt_chars": invocation.prompt_chars,
                    }
                }
            ),
        }

    def stored_invocation(self) -> StoredInvocation | None:
        """この失敗を invocations の行にしたもの。保存せず、読むときに導く。"""
        if self.invocation is None:
            return None
        return StoredInvocation.of(
            self.role,
            None,
            self.invocation.record,
            PromptStamp(self.invocation.prompt_version, self.invocation.prompt_chars),
            failure_kind=self.kind,
        )


@dataclass(frozen=True)
class _LaunchFailure(_FailureFields):
    """起動ごとの失敗。発見役のシャード、または検証の分割の起動そのもの。"""

    @property
    def scope(self) -> FailureScope:
        return FailureScope.LAUNCH

    @property
    def title(self) -> str:
        if self.kind is FailureKind.BUDGET_EXCEEDED:
            return BUDGET_FAILURE_TITLE
        return f"{LAUNCH_FAILURE_TITLE}（{self.kind}）"

    @property
    def line(self) -> str:
        """表示の文。show と結果の帯は failure_views を通してこの一文を使う。"""
        return _line(self.title, self)

    def as_record(self) -> dict[str, Any]:
        """runs.json の launch_failures に書く唯一の口。額と出所もここだけが書く。"""
        return {**self._common_record(), "scope": FailureScope.LAUNCH.value}


@dataclass(frozen=True)
class DiscoveryLaunchFailed(_LaunchFailure):
    """発見役の起動の失敗。発見役は必ず役の名前を持って起動する。"""

    role: str

    @property
    def stage(self) -> Stage:
        return Stage.DISCOVERY


@dataclass(frozen=True)
class VerificationLaunchFailed(_LaunchFailure):
    """検証の分割の起動の失敗。

    role は、役の出力を組み合わせる段の失敗のように、どの役の失敗とも言えないとき None。
    """

    role: str | None

    @property
    def stage(self) -> Stage:
        return Stage.VERIFICATION


LaunchFailed = DiscoveryLaunchFailed | VerificationLaunchFailed


@dataclass(frozen=True)
class CandidatesUnverified(_FailureFields):
    """候補ごとの未検証。起動は通ったが、検証の役の出力が一部の候補を欠いた。"""

    role: str
    # 走らせ直しても欠けたままの候補、または走らせ直して埋まった候補の、分割の中での番号。
    # プロンプトの index は役の入力の中の位置に振り直されるので、それとは違う。
    # 未検証の候補の数はこの長さを唯一の出所とする
    candidates: tuple[int, ...] = ()

    @property
    def stage(self) -> Stage:
        return Stage.VERIFICATION

    @property
    def scope(self) -> FailureScope:
        return FailureScope.MISSING_CANDIDATES

    @property
    def title(self) -> str:
        if self.kind is FailureKind.BUDGET_EXCEEDED:
            return BUDGET_FAILURE_TITLE
        # 起動は通っていて、出力が一部の候補を欠いた。どの分割のどの役かを題に添える
        return f"{MISSING_CANDIDATES_TITLE}（{self.kind}、分割 {self.index}・{self.role}）"

    @property
    def line(self) -> str:
        return _line(self.title, self)

    def as_record(self) -> dict[str, Any]:
        return {
            **self._common_record(),
            "scope": FailureScope.MISSING_CANDIDATES.value,
            "candidates": list(self.candidates),
        }


StoredFailure = DiscoveryLaunchFailed | VerificationLaunchFailed | CandidatesUnverified


def _line(title: str, failure: _FailureFields) -> str:
    when = f"、時刻 {failure.ran_at}" if failure.ran_at else ""
    return f"{title}、{cost_text(failure.cost)}、理由: {failure.reason}{when}"


# 廃止した四回の方針の形が、役なしで書いた発見の失敗を移すときの役の名
LEGACY_FULL_SHAPE_ROLE = "full"


def failure_from_record(record: dict[str, Any]) -> StoredFailure:
    """記録を stage、scope、role の値で和の型へ読む。scope は移しが埋めた値。

    段階と役の組が実在する型のどれにも当たらない記録は、
    読む側へ曖昧な値を渡さず ValueError にする。
    """
    stored = record.get("invocation")
    invocation = None
    if stored is not None:
        invocation = FailedInvocation(
            StoredInvocationRecord.from_row(
                {**stored, **cost_as_record(cost_from_record(record))}
            ),
            stored["prompt_version"],
            stored["prompt_chars"],
        )
    common = {
        "index": record["index"],
        "kind": FailureKind(record["kind"]),
        "reason": record["reason"],
        "attempt": record["attempt"],
        "disposition": Disposition(record["disposition"]),
        "ran_at": record["ran_at"],
        "invocation": invocation,
        "round": FailureRound(record["round"]),
    }
    stage = Stage(record["stage"])
    role = record["role"]
    match FailureScope(record["scope"]), stage:
        case FailureScope.LAUNCH, Stage.DISCOVERY:
            return DiscoveryLaunchFailed(**{**common, "role": role})
        case FailureScope.LAUNCH, Stage.VERIFICATION:
            return VerificationLaunchFailed(**{**common, "role": role})
        case FailureScope.MISSING_CANDIDATES, Stage.VERIFICATION if role is not None:
            return CandidatesUnverified(
                **{**common, "role": role}, candidates=tuple(record["candidates"])
            )
        case _:
            raise ValueError(
                f"失敗の記録の組が実在する型に当たりません: "
                f"stage={stage.value}, scope={record.get('scope')}, role={role!r}"
            )


def current_failures(failures: Iterable[StoredFailure]) -> list[StoredFailure]:
    """画面が出す失敗。前の回の失敗は含めない。"""
    return [failure for failure in failures if failure.round is FailureRound.CURRENT]


def as_earlier(failures: Iterable[StoredFailure]) -> tuple[StoredFailure, ...]:
    """失敗を前の回のものにした写し。新しい回を保存するとき前の記録の失敗に使う。"""
    return tuple(replace(failure, round=FailureRound.EARLIER) for failure in failures)


def rerun_count(failures: Iterable[StoredFailure]) -> int:
    """実際に起動した走らせ直しの回数。見積もりで止めた走らせ直しは含めない。"""
    return sum(
        1
        for failure in failures
        if failure.rerun and failure.disposition is not Disposition.RERUN_STOPPED
    )


def stopped_rerun_count(failures: Iterable[StoredFailure]) -> int:
    """入力の見積もりで止めて、起動しなかった走らせ直しの回数。"""
    return sum(1 for failure in failures if failure.disposition is Disposition.RERUN_STOPPED)


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


def failure_views(
    failures: Iterable[StoredFailure], failure_round: FailureRound = FailureRound.CURRENT
) -> tuple[FailureView, ...]:
    """指定した回で最終的に通らなかった失敗を、画面向けの一件ずつにする唯一の口。既定は今回の回。

    走らせ直して通った失敗（RECOVERED）は除く。同じ起動単位（段、範囲、番号、欠けた候補では
    役も）の失敗は一件にまとめる。走らせ直しの契機の失敗と最後の失敗が並ぶためで、代表は
    最後の失敗、起動した走らせ直しの回数は reruns、見積もりで止めた回数は stopped_reruns に持つ。
    """
    groups: dict[tuple[Stage, FailureScope, int, str | None], list[StoredFailure]] = {}
    for failure in (item for item in failures if item.round is failure_round):
        if failure.disposition is Disposition.RECOVERED:
            continue
        match failure:
            case DiscoveryLaunchFailed() | VerificationLaunchFailed():
                # 走らせ直しの契機の失敗と最後の失敗は役が違いうるので、役を起動単位に含めない
                role = None
            case CandidatesUnverified():
                role = failure.role
            case _ as unreachable:
                assert_never(unreachable)
        groups.setdefault((failure.stage, failure.scope, failure.index, role), []).append(failure)
    views = []
    for group in groups.values():
        last = group[-1]
        views.append(
            FailureView(
                last.stage,
                last.scope,
                last.index,
                last.role,
                last.kind,
                last.reason,
                last.line,
                rerun_count(group),
                stopped_rerun_count(group),
            )
        )
    return tuple(views)


@dataclass(frozen=True)
class EarlierFailures:
    """今回の回の前に起きた失敗の要約。画面は一行に畳み、一件ずつは開けば見せる。

    文は四つの項を別々に言い、0 の項は出さない。通らなかった失敗の件数（views）、走らせ直して
    通った起動（recovered_count）、額、額の記録なしの失敗（unrecorded_cost_count）である。額は
    課金された分を漏らさないため、走らせ直して通った失敗も含む前の回の全失敗が起動した分を数える。
    """

    views: tuple[FailureView, ...]
    # 前の回で走らせ直して通った失敗（RECOVERED）の数
    recovered_count: int
    # 前の回の全失敗のうち起動の記録を持つものの数。額が数える対象と同じ
    invocation_count: int
    # 前の回の全失敗が起動した分の額。課金なしは 0、推定は limit_substitute に分ける
    totals: CostTotals
    # 起動の記録を持たず、額にも起動の数にも入らない失敗の数
    unrecorded_cost_count: int

    @property
    def text(self) -> str:
        """一行の文。0 の項は出さない。文の組み立てはここだけが持つ。"""
        parts = []
        if self.views:
            parts.append(f"通らなかった失敗 {len(self.views)} 件")
        if self.recovered_count:
            parts.append(f"回復した起動 {self.recovered_count} 回")
        if self.totals.usd:
            estimated = (
                f"（うち推定 {self.totals.limit_substitute:.2f} USD）"
                if self.totals.limit_substitute
                else ""
            )
            parts.append(f"計 {self.totals.usd:.2f} USD{estimated}")
        if self.unrecorded_cost_count:
            parts.append(f"額の記録なし {self.unrecorded_cost_count} 件")
        return "前の回: " + "、".join(parts)

    def to_json(self) -> dict[str, Any]:
        return {
            # 通らなかった失敗の起動単位の数。意味は従来のまま
            "count": len(self.views),
            "recovered_count": self.recovered_count,
            "unrecorded_cost_count": self.unrecorded_cost_count,
            # 課金のある起動ではなく、起動の記録を持つ全失敗の数。意味は従来のまま
            "invocation_count": self.invocation_count,
            "cost_usd": self.totals.usd,
            "substituted_cost_usd": self.totals.limit_substitute,
            "text": self.text,
            "failures": [view.to_json() for view in self.views],
        }


def earlier_failures(failures: Iterable[StoredFailure]) -> EarlierFailures | None:
    """前の回の失敗の要約。通らなかった失敗も課金も額の記録なしの失敗も無ければ None。"""
    stored = list(failures)
    views = failure_views(stored, FailureRound.EARLIER)
    earlier = [failure for failure in stored if failure.round is FailureRound.EARLIER]
    costs = failed_invocation_costs(earlier)
    totals = total_by_source(costs)
    unrecorded = sum(1 for failure in earlier if failure.invocation is None)
    if not views and not totals.usd and not unrecorded:
        # 全て走らせ直して通り、課金も無いので、利用者へ知らせる事実が無い
        return None
    recovered = sum(1 for failure in earlier if failure.disposition is Disposition.RECOVERED)
    return EarlierFailures(views, recovered, len(costs), totals, unrecorded)


def earlier_failures_json(failures: Iterable[StoredFailure]) -> dict[str, Any] | None:
    """run --json と show --json が共有する、前の回の失敗の欄。"""
    earlier = earlier_failures(failures)
    return None if earlier is None else earlier.to_json()


def failed_invocations(failures: Iterable[StoredFailure]) -> list[StoredInvocation]:
    """launch_failures（前の回の分も含む）から導く、失敗した起動の invocations の行。"""
    rows = (failure.stored_invocation() for failure in failures)
    return [row for row in rows if row is not None]


def failed_invocation_costs(failures: Iterable[StoredFailure]) -> list[Cost]:
    """起動の記録を持つ失敗の額。成功した起動の額と同じ入口（total_by_source）で数える。"""
    return [failure.cost for failure in failures if failure.invocation is not None]
