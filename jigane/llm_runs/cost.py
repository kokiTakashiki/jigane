"""起動で使った額とその出所を一項目で持つ和の型。

claude.py（例外）、launch_failure.py（失敗の記録）、pipeline と usage（集計）が共有する。
どれからも import できるよう、他の jigane のモジュールを import しない小さなモジュールにした。
runs.json の保存の形からの読みは、この型を作る `cost_from_record` の一か所に閉じる。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, assert_never


@dataclass(frozen=True)
class Measured:
    """claude の結果の total_cost_usd。"""

    usd: float


@dataclass(frozen=True)
class LimitSubstitute:
    """金額が報告されず、起動の上限の額を代わりに置いた。使った額の上界で、過少に数えない。"""

    usd: float


@dataclass(frozen=True)
class Unreported:
    """結果が金額を持たなかった。額は分からない。額の欄は持たない。"""


@dataclass(frozen=True)
class NotCharged:
    """API を呼ぶ前に失敗した起動。課金は無く、額は 0 として数える。"""


Cost = Measured | LimitSubstitute | NotCharged | Unreported

# 引数と dataclass の既定値に置く不変の一つ。リンタが既定値での生成を禁じる
UNREPORTED = Unreported()


def cost_as_record(cost: Cost) -> dict[str, Any]:
    """runs.json の cost_usd と cost_source を書く唯一の口。

    額を持たない NotCharged と Unreported は 0 を書く。
    """
    match cost:
        case Measured(usd):
            return {"cost_usd": usd, "cost_source": "measured"}
        case LimitSubstitute(usd):
            return {"cost_usd": usd, "cost_source": "limit_substitute"}
        case NotCharged():
            return {"cost_usd": 0.0, "cost_source": "not_charged"}
        case Unreported():
            return {"cost_usd": 0.0, "cost_source": "unreported"}
        case _:
            assert_never(cost)


def cost_from_record(record: dict[str, Any]) -> Cost:
    """cost_usd と cost_source を持つ記録から読む。"""
    source = record["cost_source"]
    if source == "unreported":
        return Unreported()
    if source == "not_charged":
        return NotCharged()
    usd = float(record["cost_usd"])
    if source == "limit_substitute":
        return LimitSubstitute(usd)
    if source != "measured":
        raise ValueError(f"未知の金額の出所です: {source}")
    return Measured(usd)


def with_limit(cost: Cost, limit_usd: float) -> Cost:
    """報告の無かった金額を上限の額で代える。報告のあった金額はそのまま返す。"""
    if isinstance(cost, Unreported):
        return LimitSubstitute(limit_usd)
    return cost


class BudgetOutcome(StrEnum):
    """起動の行が上限について持つ結果。止めた理由と、超えた事実は別の値で持つ。"""

    # CLI が上限に達して止めた。起動は失敗で、部分の結果は使えない
    STOPPED_AT_LIMIT = "stopped_at_limit"
    # 起動は成功したが、費用が上限を超えた。CLI は上限を往復の合間にしか確かめない
    EXCEEDED_AFTER_SUCCESS = "exceeded_after_success"


@dataclass(frozen=True)
class CostTotals:
    measured: float
    limit_substitute: float

    @property
    def usd(self) -> float:
        return self.measured + self.limit_substitute


def total_by_source(costs: Iterable[Cost]) -> CostTotals:
    """Cost の並びを型ごとに分けて数える。合計はここで求める。"""
    measured = 0.0
    substitute = 0.0
    for cost in costs:
        match cost:
            case Measured(usd):
                measured += usd
            case LimitSubstitute(usd):
                substitute += usd
            case NotCharged() | Unreported():
                # 額が無いので合計に足すものが無い
                pass
            case _:
                assert_never(cost)
    return CostTotals(measured, substitute)


def cost_text(cost: Cost) -> str:
    """失敗の文に添える、額とその出所。"""
    match cost:
        case Measured(usd):
            return f"{usd:.4f} USD（使った額）"
        case LimitSubstitute(usd):
            return f"{usd:.4f} USD（推定。上限の額で代用し、実際に使った額は不明）"
        case NotCharged():
            return "課金なし（API を呼ぶ前に失敗）"
        case Unreported():
            return "使った額は不明"
        case _:
            assert_never(cost)
