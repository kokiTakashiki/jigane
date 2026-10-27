"""判断の地図の種類。コードの地図と設計書の地図の和を、ここ一か所で定める。

書き出し（map.json の dict）と読み戻しは同じ型を通る。map.json の鍵はどちらの種類でも同じ。
"""

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, assert_never

from jigane.judgment_map.classification import ChangeKind, JudgmentMap, Segment
from jigane.judgment_map.kinds import SECTION_KIND_VALUES, StoredMode
from jigane.judgment_map.region_table import Region, RegionTable
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import Upgrade, upgrade_mapping

# 地図の保存物のファイル名。保存する側（reviews）と読む側がここから読む
MAP_FILE = "map.json"
# map.json の形の版。保存物の形を変えたら一つ上げ、_UPGRADES に前の版からの移しを足す
MAP_FORMAT = 3


def _segments_from(stored: Mapping[str, Any]) -> list[Segment]:
    return [Segment.from_dict(row) for row in stored["segments"]]


class MapKind(StrEnum):
    """地図の種類。map.json には書かず、mode から決める。"""

    CODE = "code"
    # 設計書の語は StoredMode が持つ。二か所に書かないよう、そこから取る
    DESIGN_DOC = StoredMode.DESIGN_DOC.value


def map_kind_of_mode(mode: str) -> MapKind:
    """保存された mode の文字列から地図の種類を決める。jigane が書く mode 以外は読めない保存物。"""
    try:
        stored_mode = StoredMode(mode)
    except ValueError:
        raise ReviewError(f"{MAP_FILE} の mode {mode!r} は読めません") from None
    match stored_mode:
        case StoredMode.DESIGN_DOC:
            return MapKind.DESIGN_DOC
        # diff は差分のレビュー、feature は機能のレビューの mode で、どちらもコードの地図
        case StoredMode.DIFF | StoredMode.FEATURE:
            return MapKind.CODE
        case _:
            assert_never(stored_mode)


@dataclass(frozen=True)
class CodeMap:
    """コードの地図。区間は復号した Segment で持ち、箇所は RegionTable で持つ。

    to_stored_dict の鍵の並びは JudgmentMap.to_dict と同じ。
    """

    mode: StoredMode
    segments: Sequence[Segment]
    table: RegionTable

    @staticmethod
    def of(judgment_map: JudgmentMap) -> "CodeMap":
        return CodeMap(judgment_map.mode, list(judgment_map.segments), judgment_map.region_table())

    def with_regions(self, regions: Sequence[Region]) -> "CodeMap":
        return CodeMap(self.mode, self.segments, RegionTable(regions, self.table.line_counts))

    def to_stored_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "segments": [segment.to_dict() for segment in self.segments],
            **self.table.to_stored_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        """出力（--json）の形。保存の形に、表の出力だけの鍵を足す。"""
        return {**self.to_stored_dict(), **self.table.to_dict()}

    @staticmethod
    def from_dict(stored: Mapping[str, Any]) -> "CodeMap":
        return CodeMap(
            StoredMode(stored["mode"]), _segments_from(stored), RegionTable.from_dict(stored)
        )


@dataclass(frozen=True)
class DesignDocMap:
    """設計書の地図。箇所は節で、コードは参考として CodeMap を一つの欄に持つ。

    コードの地図の segments はゲートが読む区間で、設計書の地図でも保存する。
    コードの地図の regions と line_counts は、保存では current_form の下へ回る。
    """

    sections: RegionTable
    code: CodeMap

    @property
    def table(self) -> RegionTable:
        return self.sections

    @property
    def segments(self) -> Sequence[Segment]:
        return self.code.segments

    @property
    def current_form(self) -> RegionTable:
        return self.code.table

    def with_regions(self, regions: Sequence[Region]) -> "DesignDocMap":
        return DesignDocMap(RegionTable(regions, self.sections.line_counts), self.code)

    @staticmethod
    def of(sections: RegionTable, code_map: JudgmentMap) -> "DesignDocMap":
        return DesignDocMap(sections, CodeMap.of(code_map))

    def to_stored_dict(self) -> dict[str, Any]:
        return {
            "mode": StoredMode.DESIGN_DOC.value,
            "segments": [segment.to_dict() for segment in self.code.segments],
            **self.sections.to_stored_dict(),
            "current_form": self.current_form.to_stored_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        """出力（--json）の形。保存の形に、表の出力だけの鍵を足す。"""
        return {
            **self.to_stored_dict(),
            **self.sections.to_dict(),
            "current_form": self.current_form.to_dict(),
        }

    @staticmethod
    def from_dict(stored: Mapping[str, Any]) -> "DesignDocMap":
        # 保存にはコードの地図の mode が残らないので、設計書の mode を載せて CodeMap へ戻す
        code = CodeMap(
            StoredMode.DESIGN_DOC,
            _segments_from(stored),
            RegionTable.from_dict(stored["current_form"]),
        )
        return DesignDocMap(RegionTable.from_dict(stored), code)


type JudgmentMapKind = CodeMap | DesignDocMap


def dump_map(kind: JudgmentMapKind) -> dict[str, Any]:
    """map.json へ書く境の一か所。最上位に今の版を刻む。入れ子の current_form には版を付けない。"""
    match kind:
        case CodeMap() | DesignDocMap():
            return {"format": MAP_FORMAT, **kind.to_stored_dict()}
        case _:
            assert_never(kind)


def _read_map_kind(stored: Mapping[str, Any]) -> JudgmentMapKind:
    """今の版（MAP_FORMAT）の map.json の dict を和へ読み戻す。古い版は load_map が先に移す。"""
    kind = map_kind_of_mode(stored["mode"])
    match kind:
        case MapKind.CODE:
            return CodeMap.from_dict(stored)
        case MapKind.DESIGN_DOC:
            return DesignDocMap.from_dict(stored)
        case _:
            assert_never(kind)


def _change_kind_from_spans(segment: Mapping[str, Any]) -> str:
    """change_kind を持たない区間の種類を、範囲の有無から補う。

    もとの値は宣言が変更の前後にあったかで決まるが、古い保存物にはその材料が残っていない。
    変更前の範囲が無ければ追加、変更後の範囲が無ければ削除、両方あれば変更と読む。
    """
    if segment["old_span"] is None:
        return ChangeKind.ADDED.value
    if segment["new_span"] is None:
        return ChangeKind.REMOVED.value
    return ChangeKind.CHANGED.value


def _upgrade_table_v1(table: dict[str, Any], *, code_vocabulary: bool) -> None:
    """regions と line_counts の組を、今の版の鍵へ埋める。table は書き換える。

    code_vocabulary が偽の表は節の表で、種類の語彙がコードの地図と違うため by_kind へ足さない。
    """
    for row in table["regions"]:
        # judged は読み口の無いまま残った鍵で、今の版は書かない
        row.pop("judged", None)
        # 節の行は版 1 からこの二つを書いている。版 3 で外すので、ここで足すのはコードの行だけ
        if row["kind"] not in SECTION_KIND_VALUES:
            row.setdefault("bot_comment_ids", [])
            row.setdefault("old_path", None)
    by_kind = table["line_counts"].get("by_kind")
    if code_vocabulary and by_kind is not None:
        by_kind.setdefault("internal_interface", 0)


def _upgrade_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の map.json を次の版へ移す。鍵の欠けは、移す前の読み口が使っていた値で埋める。"""
    upgraded = copy.deepcopy(dict(raw))
    for segment in upgraded["segments"]:
        segment.setdefault("declaration_kind", None)
        segment.setdefault("visibility", None)
        segment.setdefault("bot_comment_ids", [])
        if "change_kind" not in segment:
            segment["change_kind"] = _change_kind_from_spans(segment)
    # 設計書の地図の最上位の表は節の表で、コードの表は current_form の下にある
    is_design_doc = "current_form" in upgraded
    _upgrade_table_v1(upgraded, code_vocabulary=not is_design_doc)
    if is_design_doc:
        _upgrade_table_v1(upgraded["current_form"], code_vocabulary=True)
    return upgraded


def _drop_section_row_keys(table: dict[str, Any]) -> None:
    for row in table["regions"]:
        if row["kind"] in SECTION_KIND_VALUES:
            # 読み口の無い空の値。コードの行と鍵をそろえるために書いていた
            row.pop("bot_comment_ids", None)
            row.pop("old_path", None)


def _upgrade_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 2 の map.json を次の版へ移す。節の行から、書くだけで読まれない二つの鍵を捨てる。"""
    upgraded = copy.deepcopy(dict(raw))
    # 節の行は設計書の地図の最上位の表にだけある。current_form の下はコードの表
    if "current_form" in upgraded:
        _drop_section_row_keys(upgraded)
    return upgraded


# 保存された版から次の版への移し。load_map が MAP_FORMAT まで順に回す
_UPGRADES: dict[int, Upgrade] = {1: _upgrade_v1, 2: _upgrade_v2}


def load_map(raw: Mapping[str, Any]) -> JudgmentMapKind:
    """map.json を読む境の一か所。保存された版から今の版まで移してから、今の版の型へ読む。"""
    stored = upgrade_mapping(MAP_FILE, raw, current=MAP_FORMAT, upgrades=_UPGRADES)
    return _read_map_kind(stored)
