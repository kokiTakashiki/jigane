import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, assert_never

from jigane.git.tree_files import is_inside_tree, read_tree_bytes, read_tree_text, walk_tree_files
from jigane.languages import registry
from jigane.languages.base import parse
from jigane.markdown import blocks as markdown_blocks

NO_DECLARATION_WARNING = "宣言が見つからない。方針の M1 は未評価になる"
EMPTY_BODY_WARNING = "PR の本文が空。宣言はタイトルだけになる"
DECLARATION_TEXT_LIMIT = 16_000

# 短い名前は無関係な語に当たるため、照合する名前の下限
_MINIMUM_NAME_LENGTH = 4
# 大文字を 2 つ以上含む名前（ReviewId）は識別子らしい複合語として扱う
_MINIMUM_UPPERCASE_COUNT = 2

_ATX_HEADING_PATTERN = re.compile(r"^( {0,3})(#{1,6})(?=[ \t]|$)")
_FENCE_OPENING_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_DEEPEST_LEVEL = 6


@dataclass(frozen=True)
class Excerpt:
    path: str
    start_line: int
    end_line: int
    text: str


class DeclarationSource(StrEnum):
    """宣言の出所。利用者に見せる呼び名はこの種類の性質として持つ。値は保存する文字列。"""

    INTENT = "intent"
    PULL_REQUEST = "pull_request"
    COMMITS = "commits"
    EXCERPTS = "excerpts"

    @property
    def label(self) -> str:
        match self:
            case DeclarationSource.INTENT:
                return "利用者の意図"
            case DeclarationSource.PULL_REQUEST:
                return "PR のタイトルと本文"
            case DeclarationSource.COMMITS:
                return "コミットの題と本文"
            case DeclarationSource.EXCERPTS:
                return "README と設計書の抜粋"
            case _:
                assert_never(self)

    @property
    def kept_on_refresh(self) -> bool:
        """refresh で保存した宣言を警告ごと保つか（真）、出所から作り直すか（偽）。"""
        match self:
            case DeclarationSource.INTENT:
                # 利用者の文は出所が保存した宣言しか無く、求め直せない
                return True
            case DeclarationSource.PULL_REQUEST | DeclarationSource.COMMITS:
                # PR の本文もコミットの列も、最新の出所から取り直す
                return False
            case DeclarationSource.EXCERPTS:
                # 抜粋はファイルから抜き直す
                return False
            case _:
                assert_never(self)


@dataclass(frozen=True)
class Declaration:
    source: DeclarationSource
    intent: str | None
    excerpts: tuple[Excerpt, ...]
    # 宣言を作るときに付いた警告。宣言と一つの値で持ち、保つか作り直すかは出所が決める
    warnings: tuple[str, ...] = ()

    def to_stored_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "intent": self.intent,
            "excerpts": [asdict(excerpt) for excerpt in self.excerpts],
            "warnings": list(self.warnings),
        }

    def to_dict(self) -> dict[str, Any]:
        """出力（--json）の形。source_label は出所から導けるので保存せず、出力にだけ足す。"""
        stored = self.to_stored_dict()
        return {"source": stored.pop("source"), "source_label": self.source.label, **stored}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Declaration":
        return cls(
            DeclarationSource(data["source"]),
            data["intent"],
            tuple(Excerpt(**item) for item in data["excerpts"]),
            tuple(data["warnings"]),
        )


def demote_headings(text: str, top_level: int) -> str:
    """本文の ATX 見出しを、いちばん上が top_level になるよう一律に下げる。

    相対の階層は保ち、6 を超える分は 6 に留める。フェンスのコードブロックの中は変えない。
    Setext の見出しは対象外で、そのまま残す。すでに top_level 以下の深さなら変えない。
    """
    lines = text.split("\n")
    headings: list[tuple[int, re.Match[str]]] = []
    fence: str | None = None
    for index, line in enumerate(lines):
        opening = _FENCE_OPENING_PATTERN.match(line)
        if fence is not None:
            # 閉じる柵は、開いた柵と同じ文字で、同じかそれ以上の長さ
            closing = line.strip()
            if closing and set(closing) == {fence[0]} and len(closing) >= len(fence):
                fence = None
        elif opening:
            fence = opening[1]
        else:
            heading = _ATX_HEADING_PATTERN.match(line)
            if heading:
                headings.append((index, heading))
    if not headings:
        return text
    shift = top_level - min(len(found[2]) for _, found in headings)
    if shift <= 0:
        return text
    for index, found in headings:
        rest = lines[index][found.end() :]
        level = min(len(found[2]) + shift, _DEEPEST_LEVEL)
        lines[index] = found[1] + "#" * level + rest
    return "\n".join(lines)


def _excerpt(path: str, lines: list[str], start: int, end: int) -> Excerpt:
    # 末尾の空行は節の区切りであり、本文ではない
    while end > start and not lines[end - 1].strip():
        end -= 1
    return Excerpt(path, start, end, "\n".join(lines[start - 1 : end]))


def _read_lines(root: Path, relative: str) -> list[str]:
    # 呼ぶ側が is_inside_tree を通した道だけを渡す。通っていなければ空として扱う
    return (read_tree_text(root, relative) or "").splitlines()


def _readme_excerpts(root: Path, target: Path) -> list[Excerpt]:
    excerpts: list[Excerpt] = []
    directory = target.parent
    while True:
        candidate = directory / "README.md"
        relative = candidate.relative_to(root).as_posix()
        if is_inside_tree(root, relative):
            lines = _read_lines(root, relative)
            spans = markdown_blocks.section_spans(lines)
            if spans:
                # 表題の導入の段落と、表題の次の見出しの節を取る
                title_end = spans[1].start_line - 1 if len(spans) > 1 else len(lines)
                excerpts.append(_excerpt(relative, lines, spans[0].start_line, title_end))
                if len(spans) > 1:
                    excerpts.append(
                        _excerpt(relative, lines, spans[1].start_line, spans[1].end_line)
                    )
            return excerpts
        if directory in {root, directory.parent}:
            return excerpts
        directory = directory.parent


def public_names(root: Path, relative: str) -> list[str]:
    adapter = registry.adapter_for(Path(relative).name)
    if adapter is None:
        return []
    source = read_tree_bytes(root, relative)
    if source is None:
        return []
    tree = parse(adapter, source)
    names = (
        d.qualified_name.rsplit(".", 1)[-1]
        for d in adapter.declarations(tree.root_node, source)
        if d.is_public
    )
    return list(dict.fromkeys(names))


def _markdown_files(root: Path) -> list[str]:
    return [path for path in walk_tree_files(root, root) if path.endswith(".md")]


def _is_compound_name(name: str) -> bool:
    # Review や Span のような 1 語の名前は、一般的な語と区別できないため照合しない
    uppercase_count = sum(1 for character in name if character.isupper())
    return "_" in name or uppercase_count >= _MINIMUM_UPPERCASE_COUNT


def _mention_pattern(target_paths: list[str], names: list[str]) -> re.Pattern[str]:
    alternatives = [re.escape(path) for path in target_paths]
    alternatives += [re.escape(Path(path).name) for path in target_paths]
    # 単語の境界で照合し、短い名前が別の語の一部に当たるのを避ける
    alternatives += [
        rf"(?<!\w){re.escape(name)}(?!\w)"
        for name in names
        if len(name) >= _MINIMUM_NAME_LENGTH and _is_compound_name(name)
    ]
    return re.compile("|".join(alternatives))


def _mention_excerpts(root: Path, pattern: re.Pattern[str]) -> list[Excerpt]:
    excerpts: list[Excerpt] = []
    for relative in _markdown_files(root):
        lines = _read_lines(root, relative)
        spans = markdown_blocks.section_spans(lines)
        for index, span in enumerate(spans):
            start, end = span.start_line, span.end_line
            next_heading = spans[index + 1].start_line if index + 1 < len(spans) else None
            # 節の直下の本文だけを調べ、子の節は子自身の結果に任せる
            body_end = (next_heading - 1) if next_heading else end
            body = lines[start - 1 : body_end]
            if any(pattern.search(line) for line in body):
                excerpts.append(_excerpt(relative, lines, start, end))
    return excerpts


def extract_declaration(root: Path, files: list[str]) -> Declaration:
    """files はリポジトリのルートからの posix の相対パス。"""
    if not files:
        # 空の files からは空の正規表現ができ、すべての節に当たってしまうため
        return Declaration(DeclarationSource.EXCERPTS, None, (), (NO_DECLARATION_WARNING,))
    excerpts: list[Excerpt] = []
    for file in files:
        excerpts.extend(_readme_excerpts(root, root / file))
    names = [name for file in files for name in public_names(root, file)]
    pattern = _mention_pattern(files, list(dict.fromkeys(names)))
    excerpts.extend(_mention_excerpts(root, pattern))
    unique = tuple(dict.fromkeys(excerpts))
    # 抜粋が一つも無いことも、他の出所の薄さと同じく宣言の警告として宣言に持たせる
    warnings = () if unique else (NO_DECLARATION_WARNING,)
    return Declaration(DeclarationSource.EXCERPTS, None, unique, warnings)


def bounded_declaration(
    source: DeclarationSource, head: str, detail: str, *, thin: bool, thin_warning: str, label: str
) -> Declaration:
    """文で宣言を持つ出所（利用者の意図、コミット、PR）が通る道。

    head は切らず、detail を上限で切って警告を付ける。抜粋の出所（extract_declaration）は
    通らない。持つのが文でなく抜粋の並びで、上限で切る detail が無く、警告も「抜粋が
    一つも無い」ときの一つだけで、薄さを測る対象が無いため。

    thin は出所ごとの「題しか無い」判定で、真なら thin_warning を付ける。
    label は切った警告に出す呼び名。
    """
    warnings: list[str] = []
    if thin:
        warnings.append(thin_warning)
    # 薄さと長さは別の性質。題だけのコミットが多い範囲は、薄くても長い
    if len(detail) > DECLARATION_TEXT_LIMIT:
        warnings.append(
            f"{label}が {len(detail)} 文字あり、先頭の {DECLARATION_TEXT_LIMIT} 文字で切った"
        )
        detail = detail[:DECLARATION_TEXT_LIMIT]
    text = f"{head}\n\n{detail}" if head and detail else head or detail
    return Declaration(source, text, (), tuple(warnings))


EMPTY_INTENT_WARNING = "利用者の文が空。宣言は空になる"
EMPTY_COMMITS_WARNING = "範囲のコミットに本文が無い。宣言は題だけになる"


def intent_declaration(text: str) -> Declaration:
    return bounded_declaration(
        DeclarationSource.INTENT,
        "",
        text,
        thin=not text.strip(),
        thin_warning=EMPTY_INTENT_WARNING,
        label="利用者の文",
    )


def commits_declaration(text: str, *, has_body: bool) -> Declaration:
    return bounded_declaration(
        DeclarationSource.COMMITS,
        "",
        text,
        thin=not has_body,
        thin_warning=EMPTY_COMMITS_WARNING,
        label="範囲のコミットの文",
    )


def pull_request_declaration(title: str, body: str) -> Declaration:
    text = body.strip()
    return bounded_declaration(
        DeclarationSource.PULL_REQUEST,
        title,
        text,
        thin=not text,
        thin_warning=EMPTY_BODY_WARNING,
        label="PR の本文",
    )
