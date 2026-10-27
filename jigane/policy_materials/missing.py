"""方針の群に渡す材料の欠けの種類と、その表示文。

コードの範囲と設計書の範囲で共通の閉じた列挙型。runs.json への保存と読み戻しは
group_result が持つ。
"""

from collections.abc import Iterable
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


def ordered_missing(kinds: Iterable[MissingMaterial]) -> tuple[MissingMaterial, ...]:
    """欠けの種類を、重複を除いて列挙型の定義順に並べる。"""
    present = set(kinds)
    return tuple(kind for kind in MissingMaterial if kind in present)


def missing_texts(kinds: Iterable[MissingMaterial]) -> tuple[str, ...]:
    return tuple(kind.text for kind in kinds)
