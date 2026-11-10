import copy
from collections.abc import Mapping, Sequence
from typing import Any

from jigane.judgment_map.classification import FilePair
from jigane.storage.review_error import ReviewError
from jigane.storage.store import REVIEW_FILE
from jigane.storage.store_format import Upgrade, upgrade_mapping, upgrade_to_current

# review.json の形の版。保存物の形を変えたら一つ上げ、_REVIEW_UPGRADES に前の版からの移しを足す
REVIEW_FORMAT = 5
# regions.json の形の版。上げ方は REVIEW_FORMAT と同じ
REGIONS_FORMAT = 2
# pairs.json の形の版。上げ方は REVIEW_FORMAT と同じ
PAIRS_FORMAT = 2
PAIRS_FILE = "pairs.json"
REGIONS_FILE = "regions.json"
# PR の記録が持つ鍵。lifecycle の読みも同じ表を引く
PULL_REQUEST_FIELDS = ("url", "number", "title")
# 版 2 までの review.json の pull_request が持つ鍵。版 1 の移しが diff_source と分けるのに使う
_PULL_REQUEST_FIELDS_UNTIL_V2 = (*PULL_REQUEST_FIELDS, "linked_issues")


def _upgrade_review_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の review.json を次の版へ移す。鍵の欠けは、移す前の読み口が使っていた値で埋める。"""
    upgraded = copy.deepcopy(dict(raw))
    # 宣言の警告を保存する前の記録は、警告が無いものとして読む
    declaration = upgraded["declaration"]
    declaration.setdefault("warnings", [])
    # content_warnings を保存する前の記録は、宣言に警告が無かったので、warnings の全体が内容の警告
    upgraded.setdefault("content_warnings", list(upgraded["warnings"]))
    # 古い PR の記録は、diff_source を分けず PR の値に変更前の木の値を同居させていた
    stored_pull_request = upgraded.get("pull_request")
    if upgraded.get("diff_source") is None and stored_pull_request is not None:
        upgraded["diff_source"] = {
            key: value
            for key, value in stored_pull_request.items()
            if key not in _PULL_REQUEST_FIELDS_UNTIL_V2
        }
        upgraded["pull_request"] = {
            key: stored_pull_request[key] for key in _PULL_REQUEST_FIELDS_UNTIL_V2
        }
    # 出所の欄は、種類に属さないものを null で書く。古い記録は書いていない
    for name in (
        "refreshed_at",
        "targets",
        "pull_request",
        "diff_source",
        "local_range",
        "design_doc",
        "background",
    ):
        upgraded.setdefault(name, None)
    return upgraded


def _upgrade_review_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 2 の review.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    # PR の情報から issue を外した。版 2 までの保存物が持つ linked_issues は読み口が無いので落とす
    if upgraded.get("pull_request") is not None:
        upgraded["pull_request"].pop("linked_issues", None)
    return upgraded


def _upgrade_review_v3(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 3 の review.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    # 版 3 までの保存物が持つ次の鍵は、読み口が無いか id から導けるので落とす。
    # warnings は content_warnings の移しの材料だけだった（版 1 の移しが先に読む）
    for name in ("kind", "mode", "is_diff", "is_pull_request", "warnings"):
        upgraded.pop(name, None)
    upgraded["declaration"].pop("source_label", None)
    return upgraded


def _upgrade_review_v4(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 4 の review.json を次の版へ移す。範囲の出所に、共通の git ディレクトリと追い方を足す。"""
    upgraded = copy.deepcopy(dict(raw))
    local_range = upgraded.get("local_range")
    if local_range is not None:
        local_range["git_directory"] = ""
        local_range["head_tracking"] = None
    return upgraded


# 保存された版から次の版への移し。load_review_meta が REVIEW_FORMAT まで順に回す
_REVIEW_UPGRADES: dict[int, Upgrade] = {
    1: _upgrade_review_v1,
    2: _upgrade_review_v2,
    3: _upgrade_review_v3,
    4: _upgrade_review_v4,
}


def load_review_meta(raw: object) -> dict[str, Any]:
    """review.json を読む境の一か所。保存された版から今の版まで移した辞書を返す。"""
    if not isinstance(raw, dict):
        raise ReviewError("review.json の最上位が辞書ではありません")
    return upgrade_mapping(REVIEW_FILE, raw, current=REVIEW_FORMAT, upgrades=_REVIEW_UPGRADES)


def dump_review(meta: Mapping[str, Any]) -> dict[str, Any]:
    """review.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {"format": REVIEW_FORMAT, **meta}


def _upgrade_regions_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の regions.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    for entry in upgraded["regions"].values():
        # judged は読み口の無いまま残った鍵で、今の版は書かない
        entry.pop("judged", None)
    return upgraded


# 保存された版から次の版への移し。load_regions が REGIONS_FORMAT まで順に回す
_REGIONS_UPGRADES: dict[int, Upgrade] = {1: _upgrade_regions_v1}


def load_regions(raw: object) -> dict[str, Any]:
    """regions.json を読む境の一か所。保存された版から今の版まで移した辞書を返す。"""
    if not isinstance(raw, dict):
        raise ReviewError("regions.json の最上位が辞書ではありません")
    return upgrade_mapping(REGIONS_FILE, raw, current=REGIONS_FORMAT, upgrades=_REGIONS_UPGRADES)


def dump_regions(state: Mapping[str, Any]) -> dict[str, Any]:
    """regions.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {"format": REGIONS_FORMAT, **{k: v for k, v in state.items() if k != "format"}}


def _upgrade_pairs_v1(raw: list[Any]) -> dict[str, Any]:
    """版 1 の pairs.json（対の配列そのもの）を次の版へ移す。"""
    return {"pairs": copy.deepcopy(raw)}


# 保存された版から次の版への移し。read_pairs が PAIRS_FORMAT まで順に回す
_PAIRS_UPGRADES: dict[int, Upgrade] = {1: _upgrade_pairs_v1}


def read_pairs(raw: object) -> list[FilePair]:
    """pairs.json を読む境の一か所。版 1 は最上位が配列で、版 2 からは format を持つ辞書。"""
    if not isinstance(raw, dict | list):
        raise ReviewError("pairs.json の最上位が配列でも辞書でもありません")
    if isinstance(raw, dict):
        stored = upgrade_mapping(PAIRS_FILE, raw, current=PAIRS_FORMAT, upgrades=_PAIRS_UPGRADES)
    else:
        # 配列は format を持てないので版 1。upgrade_mapping は辞書しか受けない
        stored = upgrade_to_current(PAIRS_FILE, 1, raw, PAIRS_FORMAT, _PAIRS_UPGRADES)
    try:
        return [FilePair(old, new) for old, new in stored["pairs"]]
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewError(
            f"pairs.json の対が [変更前, 変更後] の組ではありません: {error!r}"
        ) from error


def dump_pairs(pairs: Sequence[FilePair]) -> dict[str, Any]:
    """pairs.json へ書く境の一か所。配列は format を持てないので、辞書に包んで今の版を刻む。"""
    return {"format": PAIRS_FORMAT, "pairs": [[pair.old_path, pair.new_path] for pair in pairs]}
