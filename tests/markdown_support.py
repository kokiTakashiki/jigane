"""試験が Markdown の文書から構造を取り出す補助。markdown-it-py のトークンで読む。"""

from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.token import Token

# 表は既定の構文に含まれないため有効にする
_PARSER = MarkdownIt("commonmark").enable("table")


@dataclass(frozen=True)
class Fence:
    info: str
    content: str


def _tokens(text: str) -> list[Token]:
    return _PARSER.parse(text)


def _heading_level(token: Token) -> int:
    return int(token.tag[1:])


def headings(text: str) -> list[str]:
    """見出しを `## 題` の形で、文書の並びのまま返す。"""
    tokens = _tokens(text)
    return [
        f"{'#' * _heading_level(token)} {tokens[index + 1].content}"
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]


def _section_tokens(text: str, heading: str) -> list[Token]:
    """見出しの次から、同じかより浅い見出しの手前までのトークン。見出しが無ければ失敗させる。"""
    tokens = _tokens(text)
    marks, _, title = heading.partition(" ")
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.markup != marks:
            continue
        if tokens[index + 1].content != title:
            continue
        end = len(tokens)
        for later in range(index + 3, len(tokens)):
            candidate = tokens[later]
            if candidate.type == "heading_open" and _heading_level(candidate) <= len(marks):
                end = later
                break
        return tokens[index + 3 : end]
    raise AssertionError(f"見出しが無い: {heading}")


def section_prose(text: str, heading: str) -> str:
    """見出しの節の本文。コードフェンスは含めない。下位の見出しの文字は本文に含む。"""
    return "\n".join(
        token.content for token in _section_tokens(text, heading) if token.type == "inline"
    )


def items(text: str, heading: str | None = None) -> list[str]:
    """箇条書きの項目を文書の並びのまま返す。見出しを渡せばその節の中だけを見る。"""
    tokens = _section_tokens(text, heading) if heading is not None else _tokens(text)
    return [
        tokens[index + 2].content
        for index, token in enumerate(tokens)
        if token.type == "list_item_open"
    ]


def fences(text: str, heading: str | None = None) -> list[Fence]:
    """コードフェンスを言語の指定つきで返す。見出しを渡せばその節の中だけを見る。"""
    tokens = _section_tokens(text, heading) if heading is not None else _tokens(text)
    return [
        Fence(info=token.info.strip(), content=token.content)
        for token in tokens
        if token.type == "fence"
    ]
