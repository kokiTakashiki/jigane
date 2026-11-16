"""保存物の形の版を読む境の共通部分。版の判別と、古い版を止める扱いをここ一か所で定める。"""

from collections.abc import Mapping
from enum import Enum
from typing import Any

from jigane.storage.review_error import ReviewError

# レビューの保存物が古い版のときに添える、作り直し方の文
REVIEW_REBUILD_HINT = (
    "`jigane new` でレビューを作り直してください。"
    "以前の pick、object、hold の記録は引き継がれません。"
)


class OlderFormatError(ReviewError):
    """保存物の版がこの jigane の読める版より古いときの失敗。他の読めなさと型で見分ける。"""


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


def check_readable_version(file: str, version: object, current: int) -> None:
    """保存された版が読めるか確かめる。壊れた版（整数でない、1 未満、未来）は止める。"""
    state = classify_version(version, current)
    if state is FormatState.NEWER:
        raise ReviewError(
            f"{file} は、この jigane より新しいバージョンの jigane が作った"
            "ファイルのため、読み込めません。"
            f"このファイルの JSON の構造のバージョンは {version} で、"
            f"この jigane が読み込めるのは {current} までです。"
            "jigane を新しいバージョンに更新してください。"
            "`uv tool install jigane` で入れた場合は `uv tool upgrade jigane`、"
            "`pipx install jigane` で入れた場合は `pipx upgrade jigane` で更新できます。"
        )
    if state is FormatState.INVALID:
        if not isinstance(version, int) or isinstance(version, bool):
            raise ReviewError(f"{file} の形の版 {version!r} が整数ではありません")
        raise ReviewError(f"{file} の形の版 {version} は 1 以上でなければなりません")


def require_current_version(
    file: str,
    raw: Mapping[str, Any],
    *,
    current: int,
    rebuild_hint: str = REVIEW_REBUILD_HINT,
) -> dict[str, Any]:
    """最上位が辞書の保存物の版が current と一致することを確かめ、format を除いた辞書を返す。

    古い版は OlderFormatError、新しすぎる版と壊れた版は check_readable_version と同じ文で止める。
    format を持たない辞書は版 1 と読む。
    """
    version = stored_version(raw)
    if classify_version(version, current) is FormatState.OLDER:
        raise OlderFormatError(
            f"このレビューは古い形式のため、この jigane では開けません"
            f"（{file} の構造のバージョンは {version} で、"
            f"この jigane が読めるのは {current} だけです）。{rebuild_hint}"
        )
    check_readable_version(file, version, current)
    return {key: item for key, item in raw.items() if key != "format"}
