"""Codex に渡す統一差分のハンクと、提示済みの根拠位置。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jigane.git.tree_files import read_tree_file
from jigane.languages import registry
from jigane.languages.base import Span, parse
from jigane.languages.states import LanguageReady
from jigane.markdown.fence import fence, one_line
from jigane.skills import config_head, shards

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_HUNK = re.compile(r"@@ -\d+(?:,(?P<old_count>\d+))? \+(?P<start>\d+)(?:,(?P<new_count>\d+))? @@")


@dataclass(frozen=True)
class Hunk:
    unit: shards.Unit
    start: int
    stop: int
    contracts: tuple[Contract, ...] = ()


@dataclass(frozen=True)
class Contract:
    path: str
    span: Span
    text: str


def with_contracts(unit: shards.Unit, parts: tuple[Hunk, ...], root: Path) -> tuple[Hunk, ...]:
    """新側のハンクに重なる宣言のシグネチャを、元の行番号付きで添える。"""
    language = registry.language_for_suffix(unit.path)
    if not parts or not isinstance(language, LanguageReady):
        return parts
    data = read_tree_file(root, unit.path)
    if data is None:
        return parts
    adapter = language.plugin.adapter
    declarations = adapter.declarations(parse(adapter, data).root_node, data)
    lines = data.decode("utf-8").splitlines()
    return tuple(
        Hunk(
            hunk.unit,
            hunk.start,
            hunk.stop,
            tuple(
                Contract(
                    unit.path,
                    signature,
                    "\n".join(
                        f"{line}: {lines[line - 1]}"
                        for line in range(signature.start_line, signature.end_line + 1)
                    ),
                )
                for declaration in declarations
                if declaration.span.start_line < hunk.stop
                and declaration.span.end_line >= hunk.start
                for signature in (declaration.signature,)
                if not hunk.start <= signature.start_line <= signature.end_line < hunk.stop
            ),
        )
        for hunk in parts
    )


def contract_section(parts: Sequence[Hunk]) -> str:
    contracts = dict.fromkeys(contract for part in parts for contract in part.contracts)
    if not contracts:
        return ""
    body = "\n".join(
        f"=== {one_line(contract.path)}:{contract.span.start_line}-{contract.span.end_line} ===\n"
        f"{fence(contract.text)}"
        for contract in contracts
    )
    return f"\n\n### 関連する宣言（変更後）\n\n{body}"


def hunks(unit: shards.Unit) -> tuple[Hunk, ...]:
    """元のパス・旧新行と前後行を保ち、差分の行を順に一度ずつ割り当てる。"""
    headers = unit.diff_lines[:2]
    result: list[Hunk] = []
    lines = unit.diff_lines[2:]
    cursor = 0
    while cursor < len(lines):
        header = lines[cursor]
        match = _HUNK.fullmatch(header)
        if match is None:
            raise ValueError(f"{one_line(unit.path)}: ハンクの見出しを確認できません")
        start = int(match["start"])
        new_count = int(match["new_count"] or 1)
        old, new = int(match["old_count"] or 1), new_count
        end = cursor + 1
        while end < len(lines) and (old or new):
            line = lines[end]
            if not line or line[0] not in " +-":
                break
            old -= line[0] != "+"
            new -= line[0] != "-"
            end += 1
        if old or new:
            raise ValueError(f"{one_line(unit.path)} {header}: 旧新行の数が一致しません")
        body = shards.diff_unit(unit.path, (*headers, *lines[cursor:end]))
        result.append(Hunk(body, start, start + new_count))
        cursor = end
    return tuple(result)


def evidence(
    index: dict[str, tuple[Hunk, ...]], locations: Sequence[tuple[int, int, str]], root: Path
) -> str | None:
    """全位置を新側の行で被覆するハンクと設定冒頭を返す。"""
    if not locations:
        return None
    selected: set[Hunk] = set()
    for start, end, path in locations:
        covered = _cover_hunks(index.get(path, ()), start, end)
        if covered:
            selected.update(covered)
    ordered = [hunk for group in index.values() for hunk in group if hunk in selected]
    if not ordered or not all(_covers(ordered, *item) for item in locations):
        return None
    parts = [hunk.unit.text for hunk in ordered]
    heads = config_head.config_heads_section(root, {hunk.unit.path for hunk in ordered})
    return "\n".join([*parts, heads]) + contract_section(ordered)


def _cover_hunks(parts: Sequence[Hunk], start: int, end: int) -> list[Hunk]:
    cursor = start
    selected: list[Hunk] = []
    for hunk in parts:
        if hunk.start <= cursor < hunk.stop:
            selected.append(hunk)
            cursor = hunk.stop
        if cursor > end:
            return selected
    return []


def _covers(parts: Sequence[Hunk], start: int, end: int, path: str) -> bool:
    ranges = [(hunk.start, hunk.stop) for hunk in parts if hunk.unit.path == path] + [
        (contract.span.start_line, contract.span.end_line + 1)
        for hunk in parts
        for contract in hunk.contracts
        if contract.path == path
    ]
    cursor = start
    for first, stop in sorted(ranges):
        if first <= cursor < stop:
            cursor = stop
        if cursor > end:
            return True
    return False
