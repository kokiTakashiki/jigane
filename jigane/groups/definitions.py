"""群（方針、実装、セキュリティ）の表。識別子、表示名、既定をここだけに置く。

skills にも cli にも依存しないため、どちらからも循環 import なしに引ける。
"""

from dataclasses import dataclass
from enum import StrEnum


class GroupId(StrEnum):
    """群の識別子の閉じた集合。値は保存の形と CLI の引数の文字列と同じ。"""

    MACRO = "macro"
    MICRO = "micro"
    SECURITY = "security"


class Effort(StrEnum):
    """Claude Code CLI の --effort の値。値は claude の引数と CLI の引数の文字列と同じ。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


# 呼び出し側は従来どおりこの名前で引く。StrEnum なので文字列としても比べられる
MACRO = GroupId.MACRO
MICRO = GroupId.MICRO
SECURITY = GroupId.SECURITY


@dataclass(frozen=True)
class RunShape:
    """起動の形。群ごとの既定をこの型で持つ。"""

    # None は --effort を渡さず、Claude Code CLI の既定に任せる
    effort: Effort | None
    max_budget_usd: float
    # 往復の上限。None は --max-turns を渡さない
    max_turns: int | None
    # 真なら、CLI に渡す上限から次の一往復の余裕を引く
    reserve_margin: bool
    # 出力の見込みのトークン数。値があれば、起動の前の検査と見積もりが課金に合わせた単価で数える。
    # reserve_margin が真なら、CLI に渡す上限からも最後の一往復の出力としてこの分を引く。
    # None は入力だけで数える
    output_allowance_tokens: int | None = None


# 実装とセキュリティの最後の一往復の出力の見込み。micro の最後の往復の実測の
# 90 パーセンタイルが 0.0396 USD（requests.csv、2026-11-02）で、出力 10 USD / 100 万トークンの
# 4,000 トークンが 0.04 USD に当たる
LAST_ROUND_TRIP_OUTPUT_TOKENS = 4_000


# 方針の図の形は、どの起動にも道具を渡さないので、応答は起動ごとに一回で終わる。
# 一回の再試行を許す（max_turns）。
# 往復を絞るため、次の一往復の余裕は引かない（reserve_margin）。
# 出力の見込み 3000 は、図の起動の測りの出力の最大 2,800 トークンに合わせる
DIAGRAM_SHAPE = RunShape(
    effort=Effort.HIGH,
    max_budget_usd=0.36,
    max_turns=2,
    reserve_margin=False,
    output_allowance_tokens=3000,
)


@dataclass(frozen=True)
class PrincipleRun:
    model: str
    max_budget_usd: float
    output_allowance_tokens: int | None = None


# 図の形の原則の起動のモデルと、1 本の上限。鍵は原則の番号で、図の起動とは別の値で走らせる。
# 1 本の上限 0.04 USD と出力の見込み 30000 は、0.1.6 の公開前のレビューで
# 出力が最大 27,728 トークンだったことに合わせる
PRINCIPLE_RUNS: dict[str, PrincipleRun] = {
    "P1": PrincipleRun("claude-haiku-5-5", 0.04, 30000),
    "P2": PrincipleRun("claude-haiku-5-5", 0.04, 30000),
    "P3": PrincipleRun("claude-haiku-5-5", 0.04, 30000),
    "P4": PrincipleRun("claude-haiku-5-5", 0.04, 30000),
}
# 設計書の形は道具を渡さない一回の起動で、往復と余裕の扱いは図の形と同じ。
# 上限と effort は形ごとに持つため、別の値として並べる
# 出力の見込み 20000 は、完走した設計書の形の起動 33 本で、最大の入力 62,493 トークンに足しても
# 上限 0.5 USD を下回り、どれも見送りにならない値
DESIGN_SHAPE = RunShape(
    effort=Effort.HIGH,
    max_budget_usd=0.5,
    max_turns=2,
    reserve_margin=False,
    output_allowance_tokens=20000,
)


@dataclass(frozen=True)
class Group:
    identifier: GroupId
    label: str
    model: str
    shape: RunShape


# 実装とセキュリティの形。シャードごとに起動し、次の一往復の余裕を引く
SHARDED_SHAPE = RunShape(
    effort=None,
    max_budget_usd=0.5,
    max_turns=None,
    reserve_margin=True,
    output_allowance_tokens=LAST_ROUND_TRIP_OUTPUT_TOKENS,
)

# 並びは方針、実装、セキュリティ
GROUPS = (
    Group(MACRO, "方針", "claude-sonnet-5-5", DIAGRAM_SHAPE),
    Group(
        MICRO,
        "実装",
        "claude-sonnet-5-5",
        SHARDED_SHAPE,
    ),
    Group(
        SECURITY,
        "セキュリティ",
        "claude-sonnet-5-5",
        SHARDED_SHAPE,
    ),
)
GROUP_IDS = tuple(group.identifier for group in GROUPS)
GROUP_BY_ID = {group.identifier: group for group in GROUPS}
