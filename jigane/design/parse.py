"""設計書（Markdown）を、見出しの木、Mermaid の図、表に分ける。

Markdown のパーサには依存しない。柵と ATX 見出しの判別は jigane.markdown.blocks の部品を使う。
Setext の見出しと、引用や入れ子の中の表は対象外。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from jigane.design.mermaid import AnyDiagram, UnreadableLine, UnreadableReason, parse_diagram
from jigane.markdown import blocks as markdown_blocks

_TABLE_DELIMITER_CELL = re.compile(r"^:?-+:?$")


@dataclass(frozen=True, eq=False)
class Section:
    """見出しの木の節。比較は同一性で行う。end_line は子の節を含む範囲の最終行。"""

    level: int
    title: str
    start_line: int
    end_line: int
    children: tuple["Section", ...]


@dataclass(frozen=True)
class MermaidBlock:
    diagram: AnyDiagram
    section: Section | None
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    section: Section | None
    start_line: int
    end_line: int


@dataclass(frozen=True)
class DesignDocument:
    line_count: int
    sections: tuple[Section, ...]
    diagrams: tuple[MermaidBlock, ...]
    tables: tuple[Table, ...]
    # 図の外で読めなかった箇所（閉じない柵、列の数が合わない表）。図の中の行は各図が持つ
    unreadable: tuple[UnreadableLine, ...] = ()
    # 原文の行（末尾の改行を除く）。本文のコードスパンを名指しとして拾う側が読む
    lines: tuple[str, ...] = ()

    def all_unreadable(self) -> tuple[UnreadableLine, ...]:
        """図の中の読めない行も含めた、文書全体の読めない箇所。行の順。"""
        found = [*self.unreadable]
        for block in self.diagrams:
            found.extend(block.diagram.unreadable)
        return tuple(sorted(found, key=lambda item: item.line_number))

    def section_at(self, line: int) -> Section | None:
        """その行を含む、いちばん深い節。節に属さない行は None。"""
        return find_section(self.all_sections(), line)

    def all_sections(self) -> tuple[Section, ...]:
        """文書の順（深さ優先）に並べた全ての節。"""
        flat: list[Section] = []

        def visit(section: Section) -> None:
            flat.append(section)
            for child in section.children:
                visit(child)

        for root in self.sections:
            visit(root)
        return tuple(flat)


def parse_design(text: str) -> DesignDocument:
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        # 末尾の改行の後ろは行ではない
        lines.pop()
    roots = _section_tree(lines)
    flat = _flatten(roots)
    blocks = markdown_blocks.fences(lines)
    fenced = {number for fence in blocks for number in range(fence.start_line, fence.end_line + 1)}
    diagrams = tuple(
        MermaidBlock(
            diagram=parse_diagram(
                "\n".join(
                    _dedent(
                        [lines[number - 1] for number in fence.body_lines],
                        lines[fence.start_line - 1],
                    )
                ),
                fence.start_line + 1,
            ),
            section=find_section(flat, fence.start_line),
            start_line=fence.start_line,
            end_line=fence.end_line,
        )
        for fence in blocks
        if fence.info.split()[:1] == ["mermaid"]
    )
    unreadable = [
        UnreadableLine(
            fence.start_line, lines[fence.start_line - 1], UnreadableReason.UNCLOSED_FENCE
        )
        for fence in blocks
        if not fence.closed
    ]
    tables = _tables(lines, fenced, flat, unreadable)
    unreadable.sort(key=lambda item: item.line_number)
    return DesignDocument(
        line_count=len(lines),
        sections=roots,
        diagrams=diagrams,
        tables=tuple(tables),
        unreadable=tuple(unreadable),
        lines=tuple(lines),
    )


def _dedent(body: list[str], opening: str) -> list[str]:
    indent = len(opening) - len(opening.lstrip())
    return [line[indent:] if line[:indent].strip() == "" else line for line in body]


def _section_tree(lines: list[str]) -> tuple[Section, ...]:
    spans = markdown_blocks.section_spans(lines)
    # 子は親が閉じるときに確定するので、作りかけの節を (階層, 題, 開始, 終了, 子の並び) で持つ
    pending: list[tuple[int, str, int, int, list[Section]]] = []
    roots: list[Section] = []

    def close(item: tuple[int, str, int, int, list[Section]]) -> None:
        level, title, start, end, children = item
        section = Section(level, title, start, end, tuple(children))
        (pending[-1][4] if pending else roots).append(section)

    for span in spans:
        heading = span.heading
        level = heading.level
        end = span.end_line
        while pending and pending[-1][0] >= level:
            close(pending.pop())
        pending.append((level, heading.title, span.start_line, end, []))
    while pending:
        close(pending.pop())
    return tuple(roots)


def _flatten(roots: tuple[Section, ...]) -> list[Section]:
    flat: list[Section] = []
    for root in roots:
        flat.append(root)
        flat.extend(_flatten(root.children))
    return flat


def find_section(flat: Iterable[Section], line: int) -> Section | None:
    """行から節を引く規則の唯一の置き場。深さ優先に並べた節のうち、その行を含むいちばん深い節。

    最初の見出しより前の行は節に属さない。深さ優先の順の最後に当たったものが深い。
    """
    found: Section | None = None
    for section in flat:
        if section.start_line <= line <= section.end_line:
            found = section
    return found


def _split_cells(line: str) -> list[str]:
    text = line.strip()
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and text[index + 1 : index + 2] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current))
    if text.startswith("|"):
        cells = cells[1:]
    if text.endswith("|") and not text.endswith("\\|") and cells:
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def _is_delimiter(cells: list[str]) -> bool:
    return bool(cells) and all(_TABLE_DELIMITER_CELL.match(cell) for cell in cells)


def _tables(
    lines: list[str], fenced: set[int], flat: list[Section], unreadable: list[UnreadableLine]
) -> list[Table]:
    tables: list[Table] = []
    index = 0
    while index + 1 < len(lines):
        number = index + 1
        header = lines[index]
        if number in fenced or "|" not in header or (number + 1) in fenced:
            index += 1
            continue
        columns = _split_cells(header)
        delimiter = _split_cells(lines[index + 1])
        if "|" not in lines[index + 1] and len(columns) != 1:
            index += 1
            continue
        if not _is_delimiter(delimiter):
            # 区切り行ではないので、表ではなく本文の行
            index += 1
            continue
        if len(delimiter) != len(columns):
            unreadable.append(
                UnreadableLine(number, header, UnreadableReason.TABLE_DELIMITER_MISMATCH)
            )
            index += 1
            continue
        rows: list[tuple[str, ...]] = []
        end = index + 2
        while (
            end < len(lines)
            and (end + 1) not in fenced
            and lines[end].strip()
            and "|" in lines[end]
        ):
            cells = _split_cells(lines[end])
            if len(cells) != len(columns):
                unreadable.append(
                    UnreadableLine(end + 1, lines[end], UnreadableReason.TABLE_ROW_MISMATCH)
                )
            cells = cells[: len(columns)]
            cells += [""] * (len(columns) - len(cells))
            rows.append(tuple(cells))
            end += 1
        tables.append(Table(tuple(columns), tuple(rows), find_section(flat, number), number, end))
        index = end
    return tables
