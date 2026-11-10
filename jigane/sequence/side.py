"""図や変更の集合を取る木の側（変更前、変更後、両方）。"""

from enum import StrEnum
from typing import Literal


class Side(StrEnum):
    """図や変更の集合をどちらの木から取るか。値は CLI の --side の文字列と JSON の side に出る。"""

    BEFORE = "before"
    AFTER = "after"
    BOTH = "both"


# 図を作る木は変更前か変更後の一つ。BOTH は二つの図を並べる指定で、一つの木を指さない
TreeSide = Literal[Side.BEFORE, Side.AFTER]
