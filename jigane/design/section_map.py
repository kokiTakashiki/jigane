"""設計書の節を判断の地図の箇所にする。LLM は呼ばない。

節の種類は `jigane.design.division.SECTION_KINDS` の表が決める。ここは表を読んで箇所と行数を組む。
"""

import hashlib

from jigane.design.division import SECTION_KINDS, inherited_value, section_kind
from jigane.design.parse import Section, parse_design
from jigane.judgment_map.kinds import Level, SectionKind
from jigane.judgment_map.region_table import (
    LineCounts,
    RegionTable,
    SectionKindCounts,
    SectionRegion,
)
from jigane.languages.base import Span


def _own_end(section: Section) -> int:
    """子の節を含まない、その節自身の最終行。"""
    return section.children[0].start_line - 1 if section.children else section.end_line


def build_section_map(document_path: str, text: str) -> RegionTable:
    """節の箇所の列と、節の種類・段ごとの行数。箇所の id は保存のときに割り当てる。"""
    document = parse_design(text)
    lines = text.splitlines()
    regions: list[SectionRegion] = []
    by_kind = {kind.value: 0 for kind in SectionKind}
    by_level = {level.value: 0 for level in Level}
    seen: dict[str, int] = {}

    def visit(section: Section, titles: tuple[str, ...], parent_kind: SectionKind) -> None:
        kind = inherited_value(section, parent_kind, section_kind)
        names = (*titles, section.title)
        end = _own_end(section)
        count = end - section.start_line + 1
        if count > 1:
            # 見出しの行だけの節は、箇所にせず子の節に任せる
            row = SECTION_KINDS[kind]
            by_kind[kind.value] += count
            by_level[row.level.value] += count
            qualified = " > ".join(names)
            key = f"{document_path}::{qualified}::{kind.value}"
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                key += f"#{seen[key]}"
            body = "\n".join(lines[section.start_line - 1 : end])
            regions.append(
                SectionRegion(
                    key=key,
                    kind=kind,
                    kind_label=row.label,
                    path=document_path,
                    qualified_name=qualified,
                    summary=section.title,
                    old_span=None,
                    new_span=Span(section.start_line, end),
                    line_count=count,
                    content_hash=hashlib.sha256(body.encode()).hexdigest(),
                )
            )
        for child in section.children:
            visit(child, names, kind)

    for root in document.sections:
        visit(root, (), SectionKind.OTHER)
    return RegionTable(
        regions,
        LineCounts(
            by_kind,
            by_level,
            SectionKindCounts(
                {kind.value: row.label for kind, row in SECTION_KINDS.items()},
                {kind.value: row.level.value for kind, row in SECTION_KINDS.items()},
            ),
        ),
    )
