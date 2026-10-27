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


# 方針は図だけの形の一回の起動で、道具を渡さないので応答は一回で終わる。
# 一回の再試行を許す（max_turns）。
# 往復を絞るため、次の一往復の余裕は引かない（reserve_margin）
DIAGRAM_SHAPE = RunShape(effort=Effort.HIGH, max_budget_usd=0.5, max_turns=2, reserve_margin=False)
# 設計書の形も道具を渡さない一回の起動で、往復と余裕の扱いは図の形と同じ。
# 上限と effort は形ごとに持つため、別の値として並べる
DESIGN_SHAPE = RunShape(effort=Effort.HIGH, max_budget_usd=0.5, max_turns=2, reserve_margin=False)


@dataclass(frozen=True)
class Group:
    identifier: GroupId
    label: str
    model: str
    shape: RunShape


# 並びは方針、実装、セキュリティ
GROUPS = (
    Group(MACRO, "方針", "claude-sonnet-5-5", DIAGRAM_SHAPE),
    Group(
        MICRO,
        "実装",
        "claude-sonnet-5-5",
        RunShape(effort=None, max_budget_usd=0.5, max_turns=None, reserve_margin=True),
    ),
    Group(
        SECURITY,
        "セキュリティ",
        "claude-sonnet-5-5",
        RunShape(effort=None, max_budget_usd=0.5, max_turns=None, reserve_margin=True),
    ),
)
GROUP_IDS = tuple(group.identifier for group in GROUPS)
GROUP_BY_ID = {group.identifier: group for group in GROUPS}
