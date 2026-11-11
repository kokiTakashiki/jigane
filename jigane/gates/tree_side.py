"""記述やゲートの項目がある木の側（変更前か変更後）。

値の綴りは JSON の side と同じ。BOTH を持つ sequence.side.Side とは別の型で、二値だけを表す。
"""

from enum import StrEnum


class TreeSide(StrEnum):
    BEFORE = "before"
    AFTER = "after"
