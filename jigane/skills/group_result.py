"""群の実行の結果の五状態。判定をここだけに置き、show が同じ値を読む。

runs.json の群の記録と、保存した指摘の数から決める。画面側は値を描くだけで、条件を持たない。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from jigane.llm_runs.launch_failure import failure_views
from jigane.policy_materials.missing import MissingMaterial, missing_texts, ordered_missing
from jigane.skills.pipeline import GroupRun, PolicyGroupRun, ShardedGroupRun


class GroupResult(StrEnum):
    NOT_RUN = "not_run"
    FAILED = "failed"
    NO_FINDINGS = "no_findings"
    FINDINGS = "findings"
    NO_MATERIAL = "no_material"


def stored_missing(run: GroupRun) -> tuple[MissingMaterial, ...]:
    """runs.json の `no_material`（欠けの種類の値の配列）を列挙型へ戻す。

    列挙型に無い値は読み捨てる。読めた値が無ければ空の組で、材料なしの記録とは見なさない。
    """
    if not isinstance(run, PolicyGroupRun):
        # 材料なしを記録するのは方針の群だけ。シャードの群の記録は欠けを持たない
        return ()
    values = run.no_material or ()
    known = {kind.value: kind for kind in MissingMaterial}
    return ordered_missing(known[value] for value in values if value in known)


@dataclass(frozen=True)
class NotRun:
    result: ClassVar[GroupResult] = GroupResult.NOT_RUN

    def to_json(self) -> dict[str, Any]:
        return {"result": self.result.value, **result_fields(self)}


@dataclass(frozen=True)
class Failed:
    result: ClassVar[GroupResult] = GroupResult.FAILED
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {"result": self.result.value, "failure_reason": self.reason, **result_fields(self)}


@dataclass(frozen=True)
class NoFindings:
    """discarded が None の実行は、捨てた候補を保存する前の古い記録で、検討の内訳を言えない。"""

    result: ClassVar[GroupResult] = GroupResult.NO_FINDINGS
    discovered: int
    discarded: int | None
    # 根拠なしと形式不正で外した数。discarded が None のときだけ文に使う
    dropped: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "discovered": self.discovered,
            "discarded": self.discarded,
            "dropped": self.dropped,
            **result_fields(self),
        }


@dataclass(frozen=True)
class HasFindings:
    result: ClassVar[GroupResult] = GroupResult.FINDINGS

    def to_json(self) -> dict[str, Any]:
        return {"result": self.result.value}


@dataclass(frozen=True)
class NoMaterial:
    """資料が空で、モデルを呼ばなかった実行。指摘が 0 件の回（NoFindings）とは別の状態。

    missing は、何が無かったかの種類。runs.json の `no_material` には値を保存し、文は持たない。
    """

    result: ClassVar[GroupResult] = GroupResult.NO_MATERIAL
    missing: tuple[MissingMaterial, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "no_material": [kind.value for kind in self.missing],
            "no_material_texts": list(missing_texts(self.missing)),
            **result_fields(self),
        }


GroupOutcome = NotRun | Failed | NoFindings | HasFindings | NoMaterial

NO_FINDINGS_TEXT = "指摘なし"
NO_MATERIAL_TEXT = "材料なし"
NO_MATERIAL_SUFFIX = "。指摘が無かったとは言えません"
FAILED_TEXT = "実行は失敗しました"
NOT_RUN_TEXT = "未実行"
NOT_RUN_NOTE = "まだ走らせていません"
NOT_RUN_OUTCOME = NotRun()


def _sharded_failure_reason(run: ShardedGroupRun) -> str | None:
    shards = run.shards
    succeeded = run.succeeded_shards
    if shards and succeeded is not None and succeeded < shards:
        return f"{shards} シャードのうち {shards - succeeded} 個の発見役が失敗しました"
    failed_splits = run.failed_splits
    if failed_splits:
        # 記録した失敗の種類と理由があれば、件数よりそれを言う
        views = failure_views(run.launch_failures or ())
        if views:
            return views[0].text
        return f"検証役の {failed_splits} 件の起動が失敗しました"
    stops = run.budget_stops or ()
    if stops:
        return f"入力が大きく、{len(stops)} 件の起動を上限のために見送りました"
    return None


def _failure_reason(run: GroupRun) -> str | None:
    """指摘が 0 件の実行で、0 件を「無かった」と読めない理由。読める実行なら None。"""
    if isinstance(run, ShardedGroupRun):
        reason = _sharded_failure_reason(run)
        if reason is not None:
            return reason
    views = failure_views(run.launch_failures or ())
    if views:
        return views[0].text
    return None


def considered_text(outcome: NoFindings) -> str | None:
    """指摘なしに添える検討の件数の文。添えるものが無ければ None。"""
    if outcome.discarded is not None:
        # 捨てた判断は見せない。件数は nit の行が示す
        return f"検討 {outcome.discovered} 件"
    if not outcome.discovered:
        return None
    text = f"発見役の候補 {outcome.discovered} 件は検証を経て残りませんでした"
    dropped = outcome.dropped
    return text + (f"（根拠なしと形式不正で外した {dropped} 件を含む）" if dropped else "")


def failed_text(reason: str) -> str:
    return f"{FAILED_TEXT}: {reason}。指摘が無かったとは言えません"


def group_outcome(run: GroupRun | None, finding_count: int) -> GroupOutcome:
    """run は runs.json のその群の記録（未実行なら None）。

    finding_count は保存した指摘の数。
    """
    if run is None:
        return NOT_RUN_OUTCOME
    if finding_count > 0:
        return HasFindings()
    missing = stored_missing(run)
    if missing:
        return NoMaterial(missing)
    reason = _failure_reason(run)
    if reason is not None:
        return Failed(reason)
    considered = run.considered
    if considered is not None:
        return NoFindings(considered.discovered, len(considered.discarded))
    dropped = (run.dropped_without_evidence or 0) + (run.dropped_invalid_schema or 0)
    return NoFindings(run.discovered or 0, None, dropped)


def result_fields(outcome: GroupOutcome) -> dict[str, str]:
    """帯の文の三つ。文面はここだけが組む。指摘がある群は帯を出さないので空。

    result_title は帯の太字の部分、result_note は題に続く薄い注記（括弧を含み、無ければ空）、
    result_note_without_count は検討の件数を別の行で出す画面向けの注記。
    """
    match outcome:
        case NoFindings():
            note = considered_text(outcome)
            return {
                "result_title": NO_FINDINGS_TEXT,
                "result_note": f"（{note}）" if note else "",
                "result_note_without_count": "",
            }
        case Failed(reason):
            return {
                "result_title": failed_text(reason),
                "result_note": "",
                "result_note_without_count": "",
            }
        case NoMaterial(missing):
            # 件数ではなく欠けた材料を言う文なので、件数を別の行で出す画面にも同じ文を出す
            note = f"（{'、'.join(missing_texts(missing))}{NO_MATERIAL_SUFFIX}）"
            return {
                "result_title": NO_MATERIAL_TEXT,
                "result_note": note,
                "result_note_without_count": note,
            }
        case NotRun():
            note = f"（{NOT_RUN_NOTE}）"
            return {
                "result_title": NOT_RUN_TEXT,
                "result_note": note,
                "result_note_without_count": note,
            }
        case HasFindings():
            # 件数の見出しと一覧が状態を言うので、帯の文は無い
            return {}


def outcome_line(outcome: GroupOutcome) -> str | None:
    """show の文の一行。指摘がある群と未実行の群には足さない。"""
    match outcome:
        case NoFindings() | Failed() | NoMaterial():
            fields = result_fields(outcome)
            return fields["result_title"] + fields["result_note"]
        case NotRun() | HasFindings():
            # 指摘がある群は件数の見出しが、未実行の群は show_not_run の案内が状態を言う
            return None
