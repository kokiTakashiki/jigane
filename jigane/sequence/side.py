"""図や変更の集合を取る木の側の選択（変更前、変更後、両方）。

一つの木の側は gates.tree_side.TreeSide で、Side は BOTH を含む選択を表す。
"""

from enum import StrEnum


class Side(StrEnum):
    """図や変更の集合をどちらの木から取るか。値は CLI の --side の文字列と JSON の side に出る。"""

    BEFORE = "before"
    AFTER = "after"
    BOTH = "both"
