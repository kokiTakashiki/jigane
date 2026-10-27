"""資料の組み立てへ渡す入力。レビューと、そのレビューから一度だけ読んだ入力を一つにまとめる。"""

from dataclasses import dataclass, replace

from jigane.findings.records import (
    HistoryLoad,
    HistoryWarning,
    RelatedHistory,
    load_history_checked,
)
from jigane.reviews.lifecycle import Review
from jigane.reviews.relations import related_reviews


@dataclass(frozen=True)
class RelatedLoad:
    """関連するレビューと、そのレビューの履歴。警告にはレビューの持ち主の印が付いている。"""

    review: Review
    history: HistoryLoad


@dataclass(frozen=True)
class HistorySource:
    """資料の組み立てが読む履歴。同じレビューの分は縁が読んだ結果、関連するレビューの分はここで一度読む。"""

    own: HistoryLoad
    related: tuple[RelatedLoad, ...]

    @classmethod
    def from_loaded(cls, review: Review, own: HistoryLoad) -> "HistorySource":
        """関連するレビューの分だけここで読む。本体で呼ぶのは AssemblyInput.from_loaded だけ。"""
        related = []
        for other in related_reviews(review):
            loaded = load_history_checked(other.id.directory())
            name = f"{other.id.kind.separator}{other.id.key}"
            # 警告の持ち主を示す。縁の警告と並べたとき、どのレビューの履歴か分かるようにする
            marked = tuple(replace(w, origin=RelatedHistory(name)) for w in loaded.warnings)
            related.append(RelatedLoad(other, HistoryLoad(loaded.rounds, marked)))
        return cls(own, tuple(related))

    @property
    def related_warnings(self) -> tuple[HistoryWarning, ...]:
        return tuple(w for entry in self.related for w in entry.history.warnings)


@dataclass(frozen=True)
class AssemblyInput:
    """組み立ての公開関数が受ける唯一の入力。

    履歴などの読み込みはここを作る場所で一度だけ行い、組み立ての中では読み直さない。
    """

    review: Review
    source: HistorySource

    @classmethod
    def load(cls, review: Review) -> "AssemblyInput":
        """履歴を、同じレビューの分も関連するレビューの分もここで一度読む。"""
        return cls.from_loaded(review, load_history_checked(review.id.directory()))

    @classmethod
    def from_loaded(cls, review: Review, own: HistoryLoad) -> "AssemblyInput":
        """縁が読んだ同じレビューの履歴を受け、関連するレビューの分だけここで読む。"""
        return cls(review, HistorySource.from_loaded(review, own))
