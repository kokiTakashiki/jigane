"""判断の地図の種類。コードの地図と設計書の地図の和を、ここ一か所で定める。

書き出し（map.json の dict）と読み戻しは同じ型を通る。map.json の鍵はどちらの種類でも同じ。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, assert_never

from jigane.judgment_map.classification import JudgmentMap, Segment
from jigane.judgment_map.kinds import StoredMode
from jigane.judgment_map.region_table import Region, RegionTable
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import require_current_version

# 地図の保存物のファイル名。保存する側（reviews）と読む側がここから読む
MAP_FILE = "map.json"
# map.json の形の版。保存物の形を変えたら一つ上げる
MAP_FORMAT = 4


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
        # diff は差分のレビュー、feature はファイルのレビューの mode で、どちらもコードの地図
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
    """今の版（MAP_FORMAT）の map.json の dict を和へ読み戻す。古い版は load_map が止める。"""
    kind = map_kind_of_mode(stored["mode"])
    match kind:
        case MapKind.CODE:
            return CodeMap.from_dict(stored)
        case MapKind.DESIGN_DOC:
            return DesignDocMap.from_dict(stored)
        case _:
            assert_never(kind)


def load_map(raw: Mapping[str, Any]) -> JudgmentMapKind:
    """map.json を読む境の一か所。今の版でない保存物は止め、今の版の型へ読む。"""
    stored = require_current_version(MAP_FILE, raw, current=MAP_FORMAT)
    return _read_map_kind(stored)


def read_line_totals(raw: Mapping[str, Any]) -> tuple[int, int]:
    """map.json から、判断が要る行と全体の行の数だけを読む。区間と箇所は復号しない。

    版の検査は load_map と同じ。
    設計書の地図の最上位の line_counts は節の集計で、load_map が返す table.line_counts と同じ。
    """
    require_current_version(MAP_FILE, raw, current=MAP_FORMAT)
    by_level = raw["line_counts"]["by_level"]
    if not all(
        isinstance(count, int) and not isinstance(count, bool) for count in by_level.values()
    ):
        raise ReviewError(f"{MAP_FILE} の line_counts.by_level の値が整数ではありません")
    # 全体の読みは by_level をそのまま持つ。required の鍵が無い集計は、判断が要る行 0 と数える
    return by_level.get("required", 0), sum(by_level.values())
