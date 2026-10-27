"""保存物の形の版を読む境の共通部分。版の判別と、版から版への移しの回し方をここ一か所で定める。"""

from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, cast

from jigane.storage.review_error import ReviewError

type Upgrade = Callable[[Any], dict[str, Any]]


class FormatState(Enum):
    """保存された版を、この jigane の今の版に対して分けた結果。"""

    CURRENT = "current"
    OLDER = "older"
    NEWER = "newer"
    INVALID = "invalid"


def stored_version(raw: object) -> object:
    """生の保存物から版の値を読む。辞書でないもの、format を持たない辞書は版 1 と読む。"""
    if isinstance(raw, Mapping):
        return raw.get("format", 1)
    return 1


def classify_version(version: object, current: int) -> FormatState:
    """版の値を分類する。整数でない版と 1 未満の版は INVALID、current より大きい版は NEWER。"""
    # bool は int の下位型なので、True を版 1 と読まないよう先に外す
    if isinstance(version, bool) or not isinstance(version, int):
        return FormatState.INVALID
    if version > current:
        return FormatState.NEWER
    if version < 1:
        return FormatState.INVALID
    return FormatState.CURRENT if version == current else FormatState.OLDER


def upgrade_to_current(
    file: str, version: object, stored: object, current: int, upgrades: Mapping[int, Upgrade]
) -> dict[str, Any]:
    """保存された版から current まで、表 upgrades の移しを順に回す。

    版の値だけで判別し、鍵の有無では判別しない。壊れた版（整数でない、1 未満、未来）は止める。
    """
    state = classify_version(version, current)
    if state is FormatState.NEWER:
        raise ReviewError(
            f"{file} の形の版 {version} は、この jigane が読める版 {current} より新しいです。"
            "jigane を更新してください"
        )
    if state is FormatState.INVALID:
        if not isinstance(version, int) or isinstance(version, bool):
            raise ReviewError(f"{file} の形の版 {version!r} が整数ではありません")
        raise ReviewError(f"{file} の形の版 {version} は 1 以上でなければなりません")
    # CURRENT と OLDER は、版から current までの移しの回数が 0 回か 1 回以上かの違いだけ
    for step in range(cast("int", version), current):
        stored = upgrades[step](stored)
    if not isinstance(stored, dict):
        raise ReviewError(f"{file} の最上位が辞書ではありません")
    return stored


def upgrade_mapping(
    file: str, raw: Mapping[str, Any], *, current: int, upgrades: Mapping[int, Upgrade]
) -> dict[str, Any]:
    """最上位が辞書で format を持つ保存物を、current まで移して返す。返す辞書に format は含めない。

    format を持たない辞書は版 1 と読む。
    """
    stored = {key: item for key, item in raw.items() if key != "format"}
    return upgrade_to_current(file, stored_version(raw), stored, current, upgrades)
