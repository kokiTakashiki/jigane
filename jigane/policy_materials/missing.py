"""方針の群に渡す材料の欠けの種類と、その表示文。

コードの範囲と設計書の範囲で共通の閉じた列挙型。runs.json には値の文字列のまま保存し、
文字列から列挙型への読み替えは `recorded_missing` が唯一の場所として持つ。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class MissingMaterial(StrEnum):
    """材料の欠けの種類。コードの範囲と設計書の範囲で共通の閉じた列挙型。

    値は runs.json の `no_material` に保存する。表示文は `text` が一つの表から引く。
    定義順が、文を並べる順になる。
    """

    DOCUMENT = "document"
    STRUCTURE = "structure"
    MENTIONS = "mentions"
    CURRENT_SHAPE = "current_shape"
    FIGURES = "figures"
    DECLARATION = "declaration"

    @property
    def text(self) -> str:
        return _MISSING_TEXTS[self]


_MISSING_TEXTS = {
    MissingMaterial.DOCUMENT: "設計書の本文が渡っていません",
    MissingMaterial.STRUCTURE: "設計書から見出し、図、表が読めません",
    MissingMaterial.MENTIONS: "設計書の名指しが一つも引き当たりません",
    MissingMaterial.CURRENT_SHAPE: "いまの形の図が描けません",
    MissingMaterial.FIGURES: "描ける図がありません（変更したファイルがありません）",
    MissingMaterial.DECLARATION: "宣言が空です",
}

UNKNOWN_MISSING_TEXT = "この版が知らない欠けの種類が記録されています"


def ordered_missing(kinds: Iterable[MissingMaterial]) -> tuple[MissingMaterial, ...]:
    """欠けの種類を、重複を除いて列挙型の定義順に並べる。"""
    present = set(kinds)
    return tuple(kind for kind in MissingMaterial if kind in present)


@dataclass(frozen=True)
class RecordedMissing:
    """runs.json に記録された欠けの読み替えの結果。

    known は、この版が知っている種類を列挙型の定義順に並べた組。has_unknown は、この版が
    知らない値が記録に含まれていたこと。二つは `recorded_missing` が一度の走査で一緒に導く。
    """

    known: tuple[MissingMaterial, ...]
    has_unknown: bool = False

    @property
    def texts(self) -> tuple[str, ...]:
        """表示文。知っている種類の文を定義順に並べ、知らない値があればその旨の一文を最後に添える。"""
        texts = missing_texts(self.known)
        return (*texts, UNKNOWN_MISSING_TEXT) if self.has_unknown else texts


def recorded_missing(values: Iterable[str]) -> RecordedMissing:
    """runs.json の値の並びを、知っている種類と未知の値の有無に読み替える。

    未知の値は種類にしない。保存する側は値の文字列をそのまま持つので、書き戻しでは失われない。
    """
    kinds = {kind.value: kind for kind in MissingMaterial}
    present: set[MissingMaterial] = set()
    has_unknown = False
    for value in values:
        kind = kinds.get(value)
        if kind is None:
            has_unknown = True
        else:
            present.add(kind)
    return RecordedMissing(ordered_missing(present), has_unknown)


def missing_texts(kinds: Iterable[MissingMaterial]) -> tuple[str, ...]:
    """欠けの種類の表示文を並べる。"""
    return tuple(kind.text for kind in kinds)
