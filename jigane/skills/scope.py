"""範囲外の印。方針の群と、シャードに分ける群（実装、セキュリティ）が共有する。

役の出力の形に in_scope と scope_reason を足す処理と、それを読んで範囲外の記録を作る処理を持つ。
範囲外かの判定そのものは findings.judge_scope が持ち、ここは出力の形と指摘の辞書の間を繋ぐ。
"""

from dataclasses import dataclass
from typing import Any

from jigane.findings.records import (
    ConcludedInScope,
    ConcludedOutOfScope,
    NoConclusion,
    ScopeConclusion,
    judge_scope,
)
from jigane.llm_runs.finding_model import KEY_IN_SCOPE, KEY_SCOPE_REASON

_SCOPE_KEYS = (KEY_IN_SCOPE, KEY_SCOPE_REASON)


@dataclass(frozen=True)
class InScope:
    """範囲内の候補。in_scope と scope_reason は除いてある。"""

    candidate: dict[str, Any]

    def shaped(self) -> dict[str, Any]:
        return self.candidate


@dataclass(frozen=True)
class OutOfScope:
    """範囲外の候補と、その記録。candidate は in_scope と scope_reason を除いてある。"""

    candidate: dict[str, Any]
    record: dict[str, Any]

    def shaped(self) -> dict[str, Any]:
        """保存する指摘の形。記録は out_of_scope に入れる。"""
        shaped = dict(self.candidate)
        shaped["out_of_scope"] = self.record
        return shaped


@dataclass(frozen=True)
class ScopeSplit:
    in_scope: tuple[dict[str, Any], ...]
    out_of_scope: tuple[OutOfScope, ...]


def conclusion_of(candidate: dict[str, Any]) -> ScopeConclusion:
    """役の出力の in_scope と scope_reason を、結論の型へ直す。この読み替えはここだけで行う。"""
    in_scope = candidate.get(KEY_IN_SCOPE)
    if in_scope is None:
        return NoConclusion()
    reason = candidate.get(KEY_SCOPE_REASON, "")
    if not in_scope and reason:
        return ConcludedOutOfScope(reason)
    # 範囲外は関門を外れる経路なので、結論の役が理由を添えないときは範囲内として関門を塞ぐ側に倒す
    return ConcludedInScope()


def classify(candidate: dict[str, Any], scope: set[str]) -> InScope | OutOfScope:
    """候補を範囲の内外に分け、in_scope と scope_reason を除く。判定は一度だけ行う。"""
    record = judge_scope(
        [location["path"] for location in candidate["evidence"]], scope, conclusion_of(candidate)
    )
    stripped = {key: value for key, value in candidate.items() if key not in _SCOPE_KEYS}
    if record is None:
        return InScope(stripped)
    return OutOfScope(stripped, record)


def split_by_scope(candidates: list[dict[str, Any]], scope: set[str]) -> ScopeSplit:
    """候補の並びを一度だけ走査して、範囲内と範囲外に分ける。順序は保つ。"""
    in_scope: list[dict[str, Any]] = []
    out_of_scope: list[OutOfScope] = []
    for candidate in candidates:
        match classify(candidate, scope):
            case InScope(stripped):
                in_scope.append(stripped)
            case OutOfScope() as outside:
                out_of_scope.append(outside)
    return ScopeSplit(tuple(in_scope), tuple(out_of_scope))
