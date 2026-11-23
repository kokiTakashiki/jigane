"""設計書の構造（見出しの木、Mermaid の図、表、読めなかった箇所）を文にする。

区の印付けと読めなさの判定は呼び出し側で済ませ、ここは受け取った値を並べるだけを担う。
"""

from collections.abc import Mapping, Sequence

from jigane.design.mermaid import (
    AnyDiagram,
    ClassDiagram,
    ErDiagram,
    FlowchartDiagram,
    SequenceDiagram,
    UnreadableLine,
    UnsupportedDiagram,
)
from jigane.design.parse import DesignDocument, Section
from jigane.groups.definitions import GROUP_BY_ID, GroupId
from jigane.markdown.fence import fence, one_line
from jigane.text.prompt_labels import SECTION_TREE_LABEL

NO_STRUCTURE_TEXT = "見出し、図、表のどれも読めなかった。"


def _section_lines(
    sections: tuple[Section, ...], marks: Mapping[Section, GroupId], depth: int = 0
) -> list[str]:
    lines: list[str] = []
    for section in sections:
        mark = GROUP_BY_ID[marks[section]].label
        lines.append(
            f"{'  ' * depth}- {one_line(section.title)}"
            f"（{section.start_line}-{section.end_line} 行、印 {mark}）"
        )
        lines += _section_lines(section.children, marks, depth + 1)
    return lines


def _diagram_elements(diagram: AnyDiagram) -> str:
    match diagram:
        case ClassDiagram():
            names = "、".join(one_line(t.name) for t in diagram.types) or "なし"
            return (
                f"クラス図: 型 {len(diagram.types)} 個（{names}）、"
                f"関係 {len(diagram.relations)} 本"
            )
        case ErDiagram():
            names = "、".join(one_line(e.name) for e in diagram.entities) or "なし"
            return (
                f"ER 図: 実体 {len(diagram.entities)} 個（{names}）、"
                f"関係 {len(diagram.relations)} 本"
            )
        case SequenceDiagram():
            names = "、".join(one_line(p.alias or p.name) for p in diagram.participants) or "なし"
            return (
                f"シーケンス図: 参加者 {len(diagram.participants)} 個（{names}）、"
                f"メッセージ {len(diagram.messages)} 本"
            )
        case FlowchartDiagram():
            return f"flowchart: 節点 {len(diagram.nodes)} 個、辺 {len(diagram.edges)} 本"
        case UnsupportedDiagram():
            return f"対応外の図（{diagram.declared_type or '種類不明'}）。要素は読んでいない"


def is_structure_empty(document: DesignDocument, unreadable: Sequence[UnreadableLine]) -> bool:
    """見出し、図、表、読めなかった箇所のどれも無いか。"""
    return not (document.sections or document.diagrams or document.tables or unreadable)


def structure_text(
    document: DesignDocument,
    marks: Mapping[Section, GroupId],
    unreadable: Sequence[UnreadableLine],
) -> str:
    if is_structure_empty(document, unreadable):
        return NO_STRUCTURE_TEXT
    parts: list[str] = [f"全 {document.line_count} 行。"]
    if document.sections:
        parts.append(
            f"{SECTION_TREE_LABEL.text}\n\n" + "\n".join(_section_lines(document.sections, marks))
        )
    if document.diagrams:
        entries = []
        for block in document.diagrams:
            where = one_line(block.section.title) if block.section else "節の外"
            entries.append(
                f"- {block.start_line}-{block.end_line} 行（{where}）: "
                f"{_diagram_elements(block.diagram)}"
            )
        parts.append("Mermaid の図:\n\n" + fence("\n".join(entries)))
    if document.tables:
        entries = []
        for table in document.tables:
            where = one_line(table.section.title) if table.section else "節の外"
            columns = "、".join(one_line(column) for column in table.columns)
            entries.append(
                f"- {table.start_line}-{table.end_line} 行（{where}）: "
                f"列 {len(table.columns)}（{columns}）、行 {len(table.rows)}"
            )
        parts.append("表:\n\n" + "\n".join(entries))
    if unreadable:
        entries = [f"- {item.line_number} 行: {item.reason.label}" for item in unreadable]
        parts.append("読めなかった箇所:\n\n" + "\n".join(entries))
    return "\n\n".join(parts)
