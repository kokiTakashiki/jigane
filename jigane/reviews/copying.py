"""レビューの置き場を別の場所へ複製する。"""

import shutil
from pathlib import Path

from jigane.groups.definitions import GroupId
from jigane.reviews.group_state import clear_group_state
from jigane.reviews.lifecycle import read_review_at, set_repository_root


def copy_review_directory(
    source: Path, destination: Path, *, clear_group: GroupId | None = None
) -> None:
    """レビューの置き場を destination へ複製し、repository_root を複製先へ付け替える。

    clear_group を渡すと、複製先からその群の指摘と履歴、selection、runs、進行中の印を消す。
    前の回の結果が次の回に混ざらない。source は読むだけで書かない。
    """
    shutil.copytree(source, destination, symlinks=True)
    located = read_review_at(destination)
    root = located.review.repository_root
    set_repository_root(located, root.replace(str(source), str(destination)))
    if clear_group is None:
        # 他の群の結果も実行の記録も、そのまま引き継ぐ複製
        return
    clear_group_state(destination, clear_group)
