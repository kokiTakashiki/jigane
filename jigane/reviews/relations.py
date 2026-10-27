"""同じ変更を重ねた別のレビューを見つける。方針のプロンプトの「前の回の指摘」が引く。"""

import re
from datetime import datetime

from jigane.reviews.lifecycle import (
    ARCHIVED_INCLUDE,
    RangeOrigin,
    Review,
    list_reviews,
    split_range,
)
from jigane.storage.store import ReviewKind

_TRAILING_DIGITS = re.compile(r"-?[0-9]+$")


def series_of(name: str) -> str:
    """名前の末尾の「-数字」か「数字」を除いた系列の名前。数字だけの名前は系列を作らず、名前そのもの。"""
    return _TRAILING_DIGITS.sub("", name) or name


def _range_head(review: Review) -> str | None:
    origin = review.origin
    if not isinstance(origin, RangeOrigin):
        # 範囲の文字列は範囲の出所だけが持つ
        return None
    return split_range(origin.range.range_text).head


def is_related(current: Review, other: Review) -> bool:
    """other が current の関連するレビューか。関連の規則はここだけに置く。

    同じ owner と repo と種類で、current より前に作られたもの。PR は番号が同じなら同じ
    レビューなので、系列では結ばない。機能と範囲は系列の名前が同じなら結び、範囲は
    範囲の右側（先端のブランチ）が同じでも結ぶ。
    """
    a, b = current.id, other.id
    if a == b or (a.owner, a.repo, a.kind) != (b.owner, b.repo, b.kind):
        return False
    if a.kind is ReviewKind.PULL_REQUEST:
        return False
    if datetime.fromisoformat(other.created_at) >= datetime.fromisoformat(current.created_at):
        return False
    if series_of(a.key) == series_of(b.key):
        return True
    head = _range_head(current)
    return head is not None and head == _range_head(other)


def related_reviews(review: Review) -> list[Review]:
    """関連するレビューを、新しいものから順に返す。アーカイブ済みも含む。"""
    found = [other for other in list_reviews(ARCHIVED_INCLUDE) if is_related(review, other)]
    return sorted(found, key=lambda other: datetime.fromisoformat(other.created_at), reverse=True)
