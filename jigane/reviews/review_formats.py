from collections.abc import Mapping, Sequence
from typing import Any

from jigane.judgment_map.classification import FilePair
from jigane.storage.review_error import ReviewError
from jigane.storage.store import REVIEW_FILE
from jigane.storage.store_format import (
    REVIEW_REBUILD_HINT,
    OlderFormatError,
    require_current_version,
)

# review.json の形の版。保存物の形を変えたら一つ上げる
REVIEW_FORMAT = 5
# regions.json の形の版。上げ方は REVIEW_FORMAT と同じ
REGIONS_FORMAT = 2
# pairs.json の形の版。上げ方は REVIEW_FORMAT と同じ
PAIRS_FORMAT = 2
PAIRS_FILE = "pairs.json"
REGIONS_FILE = "regions.json"
# PR の記録が持つ鍵。lifecycle の読みも同じ表を引く
PULL_REQUEST_FIELDS = ("url", "number", "title")


def load_review_meta(raw: object) -> dict[str, Any]:
    """review.json を読む境の一か所。今の版だけを読み、古い版は OlderFormatError で止める。"""
    if not isinstance(raw, dict):
        raise ReviewError("review.json の最上位が辞書ではありません")
    return require_current_version(REVIEW_FILE, raw, current=REVIEW_FORMAT)


def dump_review(meta: Mapping[str, Any]) -> dict[str, Any]:
    """review.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {"format": REVIEW_FORMAT, **meta}


def load_regions(raw: object) -> dict[str, Any]:
    """regions.json を読む境の一か所。今の版だけを読み、古い版は OlderFormatError で止める。"""
    if not isinstance(raw, dict):
        raise ReviewError("regions.json の最上位が辞書ではありません")
    return require_current_version(REGIONS_FILE, raw, current=REGIONS_FORMAT)


def dump_regions(state: Mapping[str, Any]) -> dict[str, Any]:
    """regions.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {"format": REGIONS_FORMAT, **{k: v for k, v in state.items() if k != "format"}}


def read_pairs(raw: object) -> list[FilePair]:
    """pairs.json を読む境の一か所。版 1 は最上位が配列で、版 2 からは format を持つ辞書。"""
    if not isinstance(raw, dict | list):
        raise ReviewError("pairs.json の最上位が配列でも辞書でもありません")
    if isinstance(raw, list):
        # 配列は format を持てない版 1 の形。辞書の検査へ渡さず、版 1 として止める
        raise OlderFormatError(
            "このレビューは古い形式のため、この jigane では開けません"
            f"（{PAIRS_FILE} の構造のバージョンは 1 で、"
            f"この jigane が読めるのは {PAIRS_FORMAT} だけです）。{REVIEW_REBUILD_HINT}"
        )
    stored = require_current_version(PAIRS_FILE, raw, current=PAIRS_FORMAT)
    try:
        return [FilePair(old, new) for old, new in stored["pairs"]]
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewError(
            f"pairs.json の対が [変更前, 変更後] の組ではありません: {error!r}"
        ) from error


def dump_pairs(pairs: Sequence[FilePair]) -> dict[str, Any]:
    """pairs.json へ書く境の一か所。配列は format を持てないので、辞書に包んで今の版を刻む。"""
    return {"format": PAIRS_FORMAT, "pairs": [[pair.old_path, pair.new_path] for pair in pairs]}
