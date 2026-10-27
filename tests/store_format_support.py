"""保存物の版の移しの表を確かめる、試験の補助。"""

from collections.abc import Mapping

from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import Upgrade


def check_upgrades(file: str, current: int, upgrades: Mapping[int, Upgrade]) -> None:
    """移しの表の鍵が 1 から current - 1 まで揃っていることを確かめる。"""
    if set(upgrades) != set(range(1, current)):
        raise ReviewError(
            f"{file} の移しの表の鍵 {sorted(upgrades)} が、1 から {current - 1} までと一致しません"
        )
