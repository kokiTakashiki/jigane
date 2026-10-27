"""外しの注記 `jigane: allow-<種類> <規則> -- <理由>` を、コメントから読む部品。

flaky と正規表現が共有する。
"""

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from functools import cache

from tree_sitter import Node

from jigane.languages.base import LanguageAdapter, comment_nodes
from jigane.preconditions.required import required

_BLOCK_CLOSE = re.compile(r"\s*\*/\s*$")


@dataclass(frozen=True)
class AllowedSpot:
    """注記で規則を外した場所。line は 1 始まり。"""

    rule: str
    line: int


@dataclass(frozen=True)
class AllowScan:
    """allowed は外した場所から理由への対応。malformed は書式に合わない注記の行（1 始まり）。"""

    allowed: dict[AllowedSpot, str] = field(default_factory=dict)
    malformed: tuple[int, ...] = ()


@cache
def _pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"jigane:\s*allow-{re.escape(keyword)}\b\s*(\S*)\s*(?:--\s*(.*))?$")


def split_rows(text: str) -> list[str]:
    """tree-sitter の row と同じく、改行だけで行に分ける。splitlines では行番号がずれる。"""
    return text.split("\n")


def scan_allow_comments(
    adapter: LanguageAdapter,
    root: Node,
    keyword: str,
    known: Collection[str],
) -> AllowScan:
    """注記は、書いた行、次の行、複数行のコメントなら直後の行の規則を外す。

    規則の名前か理由が無い注記と、known に無い規則の注記は malformed になる。
    """
    pattern = _pattern(keyword)
    allowed: dict[AllowedSpot, str] = {}
    malformed: list[int] = []
    for comment in comment_nodes(adapter, root):
        # ブロックコメントは行ごとに読む。注記は複数行の途中にも置け、閉じ記号 */ は注記に含めない
        for offset, text in enumerate(split_rows(required(comment.text).decode(errors="replace"))):
            match = pattern.search(_BLOCK_CLOSE.sub("", text))
            if match is None:
                continue
            line = comment.start_point.row + offset + 1
            rule, reason = match.group(1), (match.group(2) or "").strip()
            if rule not in known or not reason:
                malformed.append(line)
                continue
            after_comment = comment.end_point.row + 2
            for target in (line, line + 1, after_comment):
                allowed[AllowedSpot(rule, target)] = reason
    return AllowScan(allowed, tuple(malformed))
