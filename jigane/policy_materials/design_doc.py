"""設計書のレビューの資料を、設計書の全文、構造、照合の表、選んだファイル、いまの形の図から組む。

レビューの種類にも出所にも依らない。設計書の本文と、選んだファイルのパスだけを受け取る。
LLM は呼ばない。同じ設計書と同じコードからは、同じ資料ができる。
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from jigane.design.division import section_marks
from jigane.design.mentions import MentionVia
from jigane.design.mermaid import (
    AnyDiagram,
    ClassDiagram,
    ErDiagram,
    FlowchartDiagram,
    SequenceDiagram,
    UnsupportedDiagram,
)
from jigane.design.parse import DesignDocument, Section, parse_design
from jigane.design.recommend import (
    AccessorCallerReason,
    Ambiguous,
    CalleeReason,
    CallerReason,
    Candidate,
    Found,
    Location,
    LocationKind,
    NamedReason,
    NotFound,
    ReaderReason,
    Reason,
    Recommendation,
    WriterReason,
    recommend,
)
from jigane.groups.definitions import GROUP_BY_ID, GroupId
from jigane.markdown.cells import markdown_table
from jigane.policy_materials.current_shape import current_shape
from jigane.policy_materials.missing import MissingMaterial, ordered_missing
from jigane.reviews.attachments import AttachmentContent

SECTION_DOCUMENT = "設計書の全文"
# 付属の文書を渡すときの見出し。主の設計書の見出しは SECTION_DOCUMENT から替わる
SECTION_MAIN_DOCUMENT = "主の設計書"
SECTION_ATTACHMENT_PREFIX = "付属の文書: "
SECTION_STRUCTURE = "設計書の構造"
SECTION_MENTIONS = "名指しの照合の表"
SECTION_SELECTION = "選んだファイルと推しの理由"
SECTION_CURRENT_SHAPE = "いまの形の図"

NO_STRUCTURE_TEXT = "見出し、図、表のどれも読めなかった。"
NO_MENTIONS_TEXT = "設計書が名指した型、ファイル、副コマンド、保存物は無い。"
NO_SELECTION_TEXT = "利用者が選んだファイルは無い。"
NO_CURRENT_SHAPE_TEXT = "選んだファイルから描けるいまの形の図は無い。"
NOT_RECOMMENDED_TEXT = "推しの候補に無いファイル（利用者が選んだ）"
# recommend の上限。選んだファイルの理由を、推しの上限で落とさずに引くために大きく取る
REASON_LIMIT = 10_000

_VIA_LABELS = {
    MentionVia.CLASS_DIAGRAM: "クラス図で",
    MentionVia.ER_DIAGRAM: "ER 図で",
    MentionVia.SEQUENCE_DIAGRAM: "シーケンス図で",
    MentionVia.CODE_SPAN: "本文で",
    MentionVia.TABLE_CODE_SPAN: "表で",
    MentionVia.TABLE_CELL: "表で",
    MentionVia.PROSE: "本文で",
}

_LOCATION_LABELS = {
    LocationKind.FILE: "ファイル",
    LocationKind.DIRECTORY: "ディレクトリ",
    LocationKind.SUBCOMMAND: "副コマンド",
    LocationKind.OPTION: "オプション",
    LocationKind.STORED_FILE: "保存物",
}
# 宣言の種類（型と、型の中やモジュール直下の宣言）は、種類の値をそのまま見せる
_NON_DECLARATIONS = frozenset(_LOCATION_LABELS)
LOCATION_SAMPLE = 3
# 設計書の範囲で数える材料。すべて欠けたときが材料なし
DESIGN_MATERIALS = {
    MissingMaterial.DOCUMENT,
    MissingMaterial.STRUCTURE,
    MissingMaterial.MENTIONS,
    MissingMaterial.CURRENT_SHAPE,
}


@dataclass(frozen=True)
class DesignMaterials:
    """資料の五つの節の文と、四つの材料のうち欠けているもの。

    節の文は見出し（`### `）を含まない。見出しは `titled` で付ける。
    selection は、材料なしの判定に数えない（選んだファイルが無ければ、いまの形の図も空になる）。
    """

    document: str
    structure: str
    mentions: str
    selection: str
    current_shape: str
    # 付属の文書。主の設計書の後ろに並ぶ。無ければ空
    attachments: tuple[AttachmentContent, ...]
    # 照合の表は引き当たらない行だけでも出るので、欠けは節の文から決まらない
    missing: tuple[MissingMaterial, ...]

    @property
    def is_empty(self) -> bool:
        return set(self.missing) == DESIGN_MATERIALS

    def sections(self) -> tuple[tuple[str, str], ...]:
        """資料の順（設計書の全文、構造、照合の表、選んだファイル、いまの形の図）の見出しと文。"""
        document_title = SECTION_MAIN_DOCUMENT if self.attachments else SECTION_DOCUMENT
        return (
            (document_title, self.document),
            *attachment_sections(self.attachments),
            (SECTION_STRUCTURE, self.structure),
            (SECTION_MENTIONS, self.mentions),
            (SECTION_SELECTION, self.selection),
            (SECTION_CURRENT_SHAPE, self.current_shape),
        )

    def text(self) -> str:
        return "\n\n".join(f"### {title}\n\n{body}" for title, body in self.sections())


def attachment_sections(
    attachments: Sequence[AttachmentContent],
) -> tuple[tuple[str, str], ...]:
    """付属の文書を、見出し付きの節の並びにする。パスの順に並べる。表示の直前の唯一の置き場。"""
    return tuple(
        (
            f"{SECTION_ATTACHMENT_PREFIX}{item.path}",
            _numbered_text(item.path, item.text, "付属の文書のパス"),
        )
        for item in sorted(attachments, key=lambda item: item.path)
    )


def design_materials(
    root: Path,
    document_path: str,
    document_text: str,
    selected: Collection[str],
    attachments: Sequence[AttachmentContent] = (),
) -> DesignMaterials:
    """設計書の本文と、利用者が選んだファイルから、資料を組む。

    root はいまのコードの作業ディレクトリ。selected は root からの相対パス。
    attachments は付属の文書で、主の設計書とは別の節にする。
    名指しの照合と構造は主の設計書だけから作る（付属の名指しは推しに入れない）。
    """
    document = parse_design(document_text)
    chosen = tuple(sorted(set(selected)))
    recommendation = recommend(root, document, REASON_LIMIT)
    structure_empty = not (
        document.sections or document.diagrams or document.tables or document.all_unreadable()
    )
    mentions_empty = not any(isinstance(m, Found | Ambiguous) for m in recommendation.matches)
    shape_text, shape_empty = _current_shape_text(root, chosen, recommendation)
    missing = ordered_missing(
        kind
        for kind, is_empty in (
            (MissingMaterial.DOCUMENT, not document_text.strip()),
            (MissingMaterial.STRUCTURE, structure_empty),
            (MissingMaterial.MENTIONS, mentions_empty),
            (MissingMaterial.CURRENT_SHAPE, shape_empty),
        )
        if is_empty
    )
    return DesignMaterials(
        document=_numbered_text(document_path, document_text),
        structure=_structure_text(document),
        mentions=_mentions_text(recommendation),
        selection=_selection_text(chosen, recommendation),
        current_shape=shape_text,
        attachments=tuple(attachments),
        missing=missing,
    )


def _numbered_text(path: str, text: str, label: str = "設計書のパス") -> str:
    """全文を、行番号を付けて切らずに載せる。根拠の位置（パス:行）を引けるようにする。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        # 末尾の改行の後ろは行ではない
        lines.pop()
    body = "\n".join(f"{number:>4}  {line}" for number, line in enumerate(lines, start=1))
    return f"{label}: {path}（{len(lines)} 行）\n\n{body}"


def _section_lines(
    sections: tuple[Section, ...], marks: Mapping[Section, GroupId], depth: int = 0
) -> list[str]:
    lines: list[str] = []
    for section in sections:
        mark = GROUP_BY_ID[marks[section]].label
        lines.append(
            f"{'  ' * depth}- {section.title}"
            f"（{section.start_line}-{section.end_line} 行、印 {mark}）"
        )
        lines += _section_lines(section.children, marks, depth + 1)
    return lines


def _diagram_elements(diagram: AnyDiagram) -> str:
    match diagram:
        case ClassDiagram():
            names = "、".join(t.name for t in diagram.types) or "なし"
            return (
                f"クラス図: 型 {len(diagram.types)} 個（{names}）、"
                f"関係 {len(diagram.relations)} 本"
            )
        case ErDiagram():
            names = "、".join(e.name for e in diagram.entities) or "なし"
            return (
                f"ER 図: 実体 {len(diagram.entities)} 個（{names}）、"
                f"関係 {len(diagram.relations)} 本"
            )
        case SequenceDiagram():
            names = "、".join(p.alias or p.name for p in diagram.participants) or "なし"
            return (
                f"シーケンス図: 参加者 {len(diagram.participants)} 個（{names}）、"
                f"メッセージ {len(diagram.messages)} 本"
            )
        case FlowchartDiagram():
            return f"flowchart: 節点 {len(diagram.nodes)} 個、辺 {len(diagram.edges)} 本"
        case UnsupportedDiagram():
            return f"対応外の図（{diagram.declared_type or '種類不明'}）。要素は読んでいない"


def _structure_text(document: DesignDocument) -> str:
    if not (
        document.sections or document.diagrams or document.tables or document.all_unreadable()
    ):
        return NO_STRUCTURE_TEXT
    parts: list[str] = [f"全 {document.line_count} 行。"]
    if document.sections:
        parts.append(
            "見出しの木（印は受け持ちの目安）:\n\n"
            + "\n".join(_section_lines(document.sections, section_marks(document.sections)))
        )
    if document.diagrams:
        entries = []
        for block in document.diagrams:
            where = block.section.title if block.section else "節の外"
            entries.append(
                f"- {block.start_line}-{block.end_line} 行（{where}）: "
                f"{_diagram_elements(block.diagram)}"
            )
        parts.append("Mermaid の図:\n\n" + "\n".join(entries))
    if document.tables:
        entries = []
        for table in document.tables:
            where = table.section.title if table.section else "節の外"
            entries.append(
                f"- {table.start_line}-{table.end_line} 行（{where}）: "
                f"列 {len(table.columns)}（{'、'.join(table.columns)}）、行 {len(table.rows)}"
            )
        parts.append("表:\n\n" + "\n".join(entries))
    unreadable = document.all_unreadable()
    if unreadable:
        entries = [f"- {item.line_number} 行: {item.reason.label}" for item in unreadable]
        parts.append("読めなかった箇所:\n\n" + "\n".join(entries))
    return "\n\n".join(parts)


def _location_text(location: Location) -> str:
    kind = _LOCATION_LABELS.get(location.kind, location.kind.value)
    position = location.path if location.line is None else f"{location.path}:{location.line}"
    if location.kind in _NON_DECLARATIONS:
        return f"{kind} {position}"
    return f"{kind} {location.label} {position}"


def _locations_text(locations: tuple[Location, ...]) -> str:
    shown = "、".join(_location_text(item) for item in locations[:LOCATION_SAMPLE])
    rest = len(locations) - LOCATION_SAMPLE
    return shown + (f"、ほか {rest} 件" if rest > 0 else "")


def _mentions_text(recommendation: Recommendation) -> str:
    if not recommendation.matches:
        return NO_MENTIONS_TEXT
    rows: list[list[str]] = []
    for entry in recommendation.matches:
        mention = entry.mention
        section = mention.section.title if mention.section else "節の外"
        match entry:
            case Found():
                result, where = "引き当たった", _locations_text(entry.locations)
            case Ambiguous():
                result = "曖昧（候補が複数で、一つに絞れない）"
                where = _locations_text(entry.locations)
            case NotFound():
                result, where = "見つからない", "いまのコードに無い"
        rows.append(
            [
                mention.name,
                mention.kind.value,
                section,
                str(mention.line),
                result,
                where,
            ]
        )
    return markdown_table(
        ["名指し", "種類", "節", "設計書の行", "照合", "引き当て先"],
        rows,
        ["codespan", "raw", "cell", "raw", "raw", "cell"],
    )


def _reason_text(reason: Reason) -> str:
    mention = reason.mention
    section = mention.section.title if mention.section else "節の外"
    via = _VIA_LABELS[mention.via]
    named = f"「{section}」節（{mention.line} 行）が{via} `{mention.name}` を名指しました"
    match reason:
        case NamedReason():
            return named
        case CallerReason():
            return f"{named}。その宣言を呼ぶ側（一段のたどり）"
        case CalleeReason():
            return f"{named}。その宣言が呼ぶ先（一段のたどり）"
        case ReaderReason():
            return f"{named}。保存物を読む側の関数 `{reason.function}`（一段のたどり）"
        case WriterReason():
            return f"{named}。保存物を書く側の関数 `{reason.function}`（一段のたどり）"
        case AccessorCallerReason():
            return (
                f"{named}。保存物を読み書きする関数 `{reason.function}` を呼ぶ側（二段のたどり）"
            )


def _selection_blocks(chosen: tuple[str, ...], recommendation: Recommendation) -> dict[str, str]:
    """選んだファイルごとの、名指した節と推しの理由の文。"""
    by_path = {candidate.path: candidate for candidate in recommendation.candidates}
    blocks: dict[str, str] = {}
    for path in chosen:
        candidate: Candidate | None = by_path.get(path)
        if candidate is None:
            blocks[path] = f"- {path}\n  - {NOT_RECOMMENDED_TEXT}"
            continue
        reasons = "\n".join(f"  - {_reason_text(r)}" for r in candidate.reasons)
        blocks[path] = f"- {path}\n{reasons}"
    return blocks


def _selection_text(chosen: tuple[str, ...], recommendation: Recommendation) -> str:
    if not chosen:
        return NO_SELECTION_TEXT
    return "\n".join(_selection_blocks(chosen, recommendation).values())


@dataclass(frozen=True)
class ShardMaterials:
    """実装の群の設計書の計画が使う部品。全文、見出しの木、選んだファイルごとの理由。"""

    document: str
    structure: str
    reasons: Mapping[str, str]


def shard_materials(
    root: Path,
    document_path: str,
    document_text: str,
    selected: Collection[str],
) -> ShardMaterials:
    """設計書の本文と選んだファイルから、シャードの資料の部品を組む。推しは一度だけ引く。"""
    document = parse_design(document_text)
    chosen = tuple(sorted(set(selected)))
    recommendation = recommend(root, document, REASON_LIMIT)
    return ShardMaterials(
        document=_numbered_text(document_path, document_text),
        structure=_structure_text(document),
        reasons=_selection_blocks(chosen, recommendation),
    )


def _shape_names(chosen: tuple[str, ...], recommendation: Recommendation) -> list[str]:
    """照合で引き当たった宣言のうち、選んだファイルにあるものの名前。図の入口と型の指定に渡す。"""
    paths = set(chosen)
    names: set[str] = set()
    for match in recommendation.matches:
        if not isinstance(match, Found):
            continue
        for location in match.locations:
            if location.path in paths and location.kind not in _NON_DECLARATIONS:
                names.add(location.label)
    return sorted(names)


def _current_shape_text(
    root: Path, chosen: tuple[str, ...], recommendation: Recommendation
) -> tuple[str, bool]:
    """いまの形の図の文と、描けた図が無いか。"""
    if not chosen:
        return NO_CURRENT_SHAPE_TEXT, True
    shape = current_shape(root, chosen, _shape_names(chosen, recommendation))
    drawable = (
        shape.tree.is_drawable(),
        shape.classes.is_drawable(),
        shape.sequences.is_drawable(),
    )
    if not any(drawable):
        return NO_CURRENT_SHAPE_TEXT, True
    text = "\n\n".join(
        (
            f"#### ツリー図\n\n{shape.tree.text()}",
            f"#### クラス図\n\n{shape.classes.text()}",
            f"#### シーケンス図\n\n{shape.sequences.text()}",
        )
    )
    return text, False
