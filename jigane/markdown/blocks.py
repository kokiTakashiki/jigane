"""Markdown の柵（コードブロック）と ATX 見出しの判別。

宣言の抜粋と設計書の読み取りが共有する。どちらもこの部品より上にある。
Setext の見出しと、引用や入れ子の中の柵は対象外。
"""

import re
from dataclasses import dataclass

_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+\S")
_TITLE_CLOSING_HASHES = re.compile(r"\s+#+\s*$")


@dataclass(frozen=True)
class Fence:
    """柵。行番号は 1 始まり。閉じていない柵の end_line は最終行になる。"""

    start_line: int
    end_line: int
    closed: bool
    info: str

    @property
    def body_lines(self) -> range:
        """柵の中身の行番号。開く柵と閉じる柵の行を含まない。"""
        return range(self.start_line + 1, self.end_line if self.closed else self.end_line + 1)


@dataclass(frozen=True)
class Heading:
    line: int
    level: int
    title: str


def fences(lines: list[str]) -> list[Fence]:
    """閉じる柵は、開いた柵と同じ文字で、同じかそれ以上の長さ。"""
    found: list[Fence] = []
    marker: str | None = None
    start = 0
    info = ""
    for index, line in enumerate(lines, start=1):
        if marker is None:
            opening = _FENCE_PATTERN.match(line)
            if opening:
                marker, start, info = opening[1], index, opening[2].strip()
        else:
            closing = line.strip()
            if closing and set(closing) == {marker[0]} and len(closing) >= len(marker):
                found.append(Fence(start, index, closed=True, info=info))
                marker = None
    if marker is not None:
        found.append(Fence(start, len(lines), closed=False, info=info))
    return found


def headings(lines: list[str]) -> list[Heading]:
    """コードブロック内の # は見出しではない。"""
    fenced = {
        number for fence in fences(lines) for number in range(fence.start_line, fence.end_line + 1)
    }
    found: list[Heading] = []
    for index, line in enumerate(lines, start=1):
        if index in fenced:
            continue
        match = _HEADING_PATTERN.match(line)
        if match:
            title = _TITLE_CLOSING_HASHES.sub("", line.strip().lstrip("#").strip())
            found.append(Heading(index, len(match[1]), title))
    return found


@dataclass(frozen=True)
class SectionSpan:
    """見出しとその節の範囲。行番号は 1 始まり。end_line は子の節を含む最終行。"""

    heading: Heading
    start_line: int
    end_line: int


def section_spans(lines: list[str]) -> list[SectionSpan]:
    """節は、同じかより浅い階層の次の見出しの直前まで。無ければ最終行まで。見出しの順に返す。"""
    found = headings(lines)
    spans: list[SectionSpan] = []
    for index, heading in enumerate(found):
        end = len(lines)
        for later in found[index + 1 :]:
            if later.level <= heading.level:
                end = later.line - 1
                break
        spans.append(SectionSpan(heading, heading.line, end))
    return spans
