"""領域の表。map.json の regions と line_counts の組の保存の形を、ここ一か所で定める。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jigane.judgment_map.kinds import SECTION_KIND_VALUES, Kind, SectionKind
from jigane.languages.base import Span


def _span_to_dict(span: Span | None) -> dict[str, int] | None:
    if span is None:
        return None
    return {"start_line": span.start_line, "end_line": span.end_line}


def _span_from_dict(stored: Mapping[str, Any] | None) -> Span | None:
    if stored is None:
        return None
    return Span(stored["start_line"], stored["end_line"])


@dataclass(frozen=True, kw_only=True)
class _RegionFields:
    """コードの行にも節の行にもある項目。kind は型が違うので、それぞれの行が持つ。"""

    # <パス>::<修飾名>::<種類>
    key: str
    path: str
    qualified_name: str
    summary: str
    old_span: Span | None
    new_span: Span | None
    line_count: int
    # 作者の修正の後に内容が変わった箇所を見分けるための、範囲の本文の sha256
    content_hash: str
    # J の番号。保存のときに割り当てるので、割り当て前の箇所には無い
    id: str | None = None

    def _tail(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "path": self.path,
            "qualified_name": self.qualified_name,
            "summary": self.summary,
            "old_span": _span_to_dict(self.old_span),
            "new_span": _span_to_dict(self.new_span),
            "line_count": self.line_count,
            "content_hash": self.content_hash,
        }
        return row


@dataclass(frozen=True, kw_only=True)
class CodeRegion(_RegionFields):
    """コードの変更の行。"""

    kind: Kind
    bot_comment_ids: tuple[int, ...] = ()
    # 改名されたファイルの変更前のパス。改名でないとき、変更前の無いときは None
    old_path: str | None = None

    def to_stored_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {"key": self.key, "kind": self.kind.value}
        row |= self._tail()
        row |= {"bot_comment_ids": list(self.bot_comment_ids), "old_path": self.old_path}
        if self.id is not None:
            row["id"] = self.id
        return row

    def to_dict(self) -> dict[str, Any]:
        """出力（--json）の形。コードの行は出力にだけ足す鍵が無く、保存の形と同じ。"""
        return self.to_stored_dict()


@dataclass(frozen=True, kw_only=True)
class SectionRegion(_RegionFields):
    """設計書の節の行。"""

    kind: SectionKind
    # 設計書の節の種類の表示名
    kind_label: str

    def to_stored_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "key": self.key,
            "kind": self.kind.value,
            "kind_label": self.kind_label,
        }
        row |= self._tail()
        if self.id is not None:
            row["id"] = self.id
        return row

    def to_dict(self) -> dict[str, Any]:
        """出力（--json）の形。コードの行と鍵をそろえるため、空の値の二つの鍵を出力にだけ足す。"""
        return {**self.to_stored_dict(), "bot_comment_ids": [], "old_path": None}


type Region = CodeRegion | SectionRegion


def region_from_dict(stored: Mapping[str, Any]) -> Region:
    common: dict[str, Any] = {
        "key": stored["key"],
        "path": stored["path"],
        "qualified_name": stored["qualified_name"],
        "summary": stored["summary"],
        "old_span": _span_from_dict(stored["old_span"]),
        "new_span": _span_from_dict(stored["new_span"]),
        "line_count": stored["line_count"],
        "content_hash": stored["content_hash"],
        "id": stored.get("id"),
    }
    if stored["kind"] in SECTION_KIND_VALUES:
        return SectionRegion(
            **common, kind=SectionKind(stored["kind"]), kind_label=stored["kind_label"]
        )
    return CodeRegion(
        **common,
        kind=Kind(stored["kind"]),
        bot_comment_ids=tuple(stored["bot_comment_ids"]),
        old_path=stored["old_path"],
    )


@dataclass(frozen=True)
class SectionKindCounts:
    """節の種類の表示名と段。種類の語彙が節ごとに違うため、地図の側に一組で添える。"""

    labels: dict[str, str]
    levels: dict[str, str]


@dataclass(frozen=True)
class LineCounts:
    """判断の地図の行数の集計。by_kind と by_level は鍵が語彙の値、数は行数。

    section_kinds は節の地図だけが持つ。コードの地図は None で、保存にも鍵を書かない。
    """

    by_kind: dict[str, int]
    by_level: dict[str, int]
    section_kinds: SectionKindCounts | None = None

    def to_stored_dict(self) -> dict[str, Any]:
        stored: dict[str, Any] = {"by_kind": self.by_kind, "by_level": self.by_level}
        if self.section_kinds is not None:
            stored["kind_labels"] = self.section_kinds.labels
            stored["kind_levels"] = self.section_kinds.levels
        return stored

    def to_dict(self) -> dict[str, Any]:
        """出力（--json）の形。出力にだけ足す鍵は無く、保存の形と同じ。"""
        return self.to_stored_dict()

    @staticmethod
    def from_dict(stored: Mapping[str, Any]) -> "LineCounts":
        has_labels = "kind_labels" in stored
        if has_labels != ("kind_levels" in stored):
            raise ValueError("kind_labels と kind_levels は一組で保存します。片方しかありません")
        section_kinds = (
            SectionKindCounts(dict(stored["kind_labels"]), dict(stored["kind_levels"]))
            if has_labels
            else None
        )
        return LineCounts(dict(stored["by_kind"]), dict(stored["by_level"]), section_kinds)

    @staticmethod
    def empty() -> "LineCounts":
        return LineCounts({}, {})


@dataclass(frozen=True)
class RegionTable:
    """regions と line_counts は型のある値。保存の形への写しは to_dict と from_dict が持つ。"""

    regions: Sequence[Region]
    line_counts: LineCounts

    def to_stored_dict(self) -> dict[str, Any]:
        return {
            "regions": [region.to_stored_dict() for region in self.regions],
            "line_counts": self.line_counts.to_stored_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        """出力（--json）の形。行ごとの to_dict を通す。"""
        return {
            "regions": [region.to_dict() for region in self.regions],
            "line_counts": self.line_counts.to_dict(),
        }

    @staticmethod
    def from_dict(stored: Mapping[str, Any]) -> "RegionTable":
        return RegionTable(
            [region_from_dict(row) for row in stored["regions"]],
            LineCounts.from_dict(stored["line_counts"]),
        )
