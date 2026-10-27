"""設計書が名指した型、パス、副コマンド、保存物を取り出す（設計は design-review の 4.4 節）。

LLM は呼ばない。名指しは名前、種類、名指した節、設計書の行の組で、同じ名前が複数の節に現れたら
節ごとに残す。同じ節の同じ名前は、出どころの優先（コードスパン > 図 > 表のセル > 地の文）で
一つにまとめる。
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from jigane.design.mermaid import AnyDiagram, ClassDiagram, ErDiagram, SequenceDiagram
from jigane.design.parse import DesignDocument, Section
from jigane.markdown import blocks as markdown_blocks


class MentionKind(StrEnum):
    TYPE = "type"
    QUALIFIED_NAME = "qualified_name"
    PATH = "path"
    SUBCOMMAND = "subcommand"
    OPTION = "option"
    STORED_FILE = "stored_file"


class MentionVia(StrEnum):
    """名指しの手がかりが現れた場所。理由の行の言い回しを決める。"""

    CLASS_DIAGRAM = "class_diagram"
    ER_DIAGRAM = "er_diagram"
    SEQUENCE_DIAGRAM = "sequence_diagram"
    CODE_SPAN = "code_span"
    TABLE_CODE_SPAN = "table_code_span"
    TABLE_CELL = "table_cell"
    PROSE = "prose"


@dataclass(frozen=True)
class Mention:
    name: str
    kind: MentionKind
    section: Section | None
    # 設計書の 1 始まりの行
    line: int
    via: MentionVia = MentionVia.CODE_SPAN


# 同じ節に同じ名前が複数の出どころで現れたとき、どれを残すかの優先。小さいほうが先
_VIA_PRIORITY = {
    MentionVia.CODE_SPAN: 0,
    MentionVia.TABLE_CODE_SPAN: 0,
    MentionVia.CLASS_DIAGRAM: 1,
    MentionVia.ER_DIAGRAM: 1,
    MentionVia.SEQUENCE_DIAGRAM: 1,
    MentionVia.TABLE_CELL: 2,
    MentionVia.PROSE: 3,
}
# コードスパンの外から拾った出どころ。パスの形の語は候補にすぎず、実在の確認を推しの側で行う
PROSE_VIAS = frozenset({MentionVia.TABLE_CELL, MentionVia.PROSE})

_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_QUALIFIED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_PATH = re.compile(r"\.{0,2}/?[\w.\-]+(?:/[\w.\-]+)+/?")
_BARE_FILE = re.compile(r"[\w\-]+(?:\.[\w\-]+)*\.(\w+)")
_SUBCOMMAND_NAME = re.compile(r"[a-z][a-z0-9\-]*")
_OPTION = re.compile(r"--[A-Za-z][\w\-]*")
# 識別子の形の語のうち、型か関数の名前らしいもの。大文字始まりで小文字を含む語（CapWords）、
# 下線を含む語、末尾に `()` を付けた語。`kind` や `JSON` のような普通の語と略語は、拾うと表が荒れる
_NAMELIKE = re.compile(r"[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*|\w*_\w*")
# 地の文（コードスパンの外）の保存物の名前。日本語の助詞に挟まれても拾えるよう、名前は ASCII に限る
_PROSE_STORED_FILE = re.compile(
    r"(?<![A-Za-z0-9_./\-])[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*"
    r"\.(?:json|jsonl|csv|toml)(?![A-Za-z0-9_/])"
)
# 地の文のパスの候補。日本語の助詞に挟まれても拾えるよう、ASCII の語に限る
_PROSE_TOKEN = re.compile(r"(?<![A-Za-z0-9_./\-:])[A-Za-z0-9_./\-]+")
_GENERIC_SUFFIX = re.compile(r"(?:~.*~|<.*>)$")

STORED_FILE_SUFFIXES = frozenset({"json", "jsonl", "ndjson", "csv", "tsv", "toml", "yaml", "yml"})
FILE_SUFFIXES = frozenset(
    {
        "py",
        "md",
        "txt",
        "swift",
        "cs",
        "java",
        "js",
        "ts",
        "tsx",
        "sh",
        "html",
        "css",
        "ini",
        "cfg",
    }
)
_NOT_NAMES = frozenset({"None", "True", "False"})


def extract_mentions(
    document: DesignDocument,
    stored_names: frozenset[str] = frozenset(),
    path_suffixes: frozenset[str] = frozenset(),
) -> tuple[Mention, ...]:
    """設計書から名指しを、行、名前、種類の順に並べて返す。

    地の文の保存物の名前は、`stored_names`（作業ディレクトリのソースが定数として持つ名前）に
    一致するものだけを拾う。定数に無い `foo.json` まで拾うと雑音が増えるため。

    地の文と表のセルのパスの形の語（`/` を含む語と、`path_suffixes`（作業ディレクトリに実在する
    ファイルの拡張子）を持つファイル名）は出どころが `PROSE` か
    `TABLE_CELL` の候補として返す。実在するかどうかの判定は呼ぶ側が行う。
    """
    found: list[Mention] = []
    for block in document.diagrams:
        found += _diagram_mentions(block.diagram, block.section, block.start_line)
    fenced = {
        number
        for fence in markdown_blocks.fences(list(document.lines))
        for number in range(fence.start_line, fence.end_line + 1)
    }
    table_lines = {
        number
        for table in document.tables
        for number in range(table.start_line, table.end_line + 1)
    }
    for number, text in enumerate(document.lines, start=1):
        if number in fenced:
            continue
        in_table = number in table_lines
        span_via = MentionVia.TABLE_CODE_SPAN if in_table else MentionVia.CODE_SPAN
        prose_via = MentionVia.TABLE_CELL if in_table else MentionVia.PROSE
        section = document.section_at(number)
        for span in _CODE_SPAN.finditer(text):
            found += _span_mentions(span[2].strip(), section, number, span_via)
        prose = _CODE_SPAN.sub(" ", text)
        found += _prose_path_mentions(
            prose, section, number, prose_via, path_suffixes, stored_names=stored_names
        )
        if stored_names:
            found += [
                Mention(match[0], MentionKind.STORED_FILE, section, number, prose_via)
                for match in _PROSE_STORED_FILE.finditer(prose)
                if match[0] in stored_names
            ]
    return _unique(found)


def _prose_path_mentions(
    prose: str,
    section: Section | None,
    line: int,
    via: MentionVia,
    path_suffixes: frozenset[str],
    *,
    stored_names: frozenset[str],
) -> list[Mention]:
    mentions: list[Mention] = []
    for match in _PROSE_TOKEN.finditer(prose):
        token = match[0].rstrip(".")
        if token.startswith("//"):
            # URL の残り
            continue
        if "/" in token:
            is_path = _PATH.fullmatch(token) is not None
        else:
            # 保存物の名前として拾う語は、保存物の名指しの側に任せる
            is_path = (
                _BARE_FILE.fullmatch(token) is not None
                and _file_suffix(token) in path_suffixes
                and token not in stored_names
            )
        if is_path:
            mentions.append(Mention(token.rstrip("/"), MentionKind.PATH, section, line, via))
    return mentions


def section_key(mention: Mention) -> int:
    """名指しの節を区別する鍵。冒頭（節の前）は 0。"""
    return mention.section.start_line if mention.section else 0


def _unique(found: list[Mention]) -> tuple[Mention, ...]:
    # 同じ節の同じ名前は、種類が違っても出どころの優先（コードスパン > 図 > 表のセル > 地の文）で
    # 一つに畳む。同じ優先なら先の行が代表になる。種類は代表のものを採る
    chosen: dict[tuple[str, int], Mention] = {}
    for mention in sorted(found, key=lambda m: (_VIA_PRIORITY[m.via], m.line, m.kind.value)):
        chosen.setdefault((mention.name, section_key(mention)), mention)
    return tuple(sorted(chosen.values(), key=lambda m: (m.line, m.name, m.kind.value)))


def _diagram_mentions(
    diagram: AnyDiagram, section: Section | None, start_line: int
) -> list[Mention]:
    if isinstance(diagram, ClassDiagram):
        names = [t.name for t in diagram.types]
        via = MentionVia.CLASS_DIAGRAM
    elif isinstance(diagram, ErDiagram):
        names = [e.name for e in diagram.entities]
        via = MentionVia.ER_DIAGRAM
    elif isinstance(diagram, SequenceDiagram):
        names = [
            word for p in diagram.participants for word in (p.name, p.alias) if word is not None
        ]
        via = MentionVia.SEQUENCE_DIAGRAM
    else:
        # flowchart の節点はラベルで、型の名前の手がかりにならない
        return []
    source_lines = diagram.source.split("\n")
    mentions: list[Mention] = []
    for raw in names:
        name = _GENERIC_SUFFIX.sub("", raw.strip())
        if _IDENTIFIER.fullmatch(name):
            if via is MentionVia.SEQUENCE_DIAGRAM and not _NAMELIKE.fullmatch(name):
                # 別名や短い名前（`U`、`db`）は型の名前と限らない。本文と同じ絞りを掛ける
                continue
            kind = MentionKind.TYPE
        elif _QUALIFIED.fullmatch(name):
            kind = MentionKind.QUALIFIED_NAME
        else:
            # 空白を含む表示名などは、コードの名前として引けない
            continue
        mentions.append(
            Mention(name, kind, section, _line_of(name, source_lines, start_line), via)
        )
    return mentions


def _line_of(name: str, source_lines: list[str], start_line: int) -> int:
    """図の原文のうち、名前を最初に含む行の、設計書での行。見つからなければ柵の行。"""
    pattern = re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w])")
    for offset, text in enumerate(source_lines, start=1):
        if pattern.search(text):
            return start_line + offset
    return start_line


def _span_mentions(
    span: str, section: Section | None, line: int, via: MentionVia
) -> list[Mention]:
    if span == "jigane" or span.startswith("jigane "):
        return _command_mentions(span.split()[1:], section, line, via)
    if _OPTION.fullmatch(span.split("=", 1)[0]):
        return [Mention(span.split("=", 1)[0], MentionKind.OPTION, section, line, via)]
    name = span.removesuffix("()")
    callable_form = name != span
    if _IDENTIFIER.fullmatch(name):
        return _identifier_mentions(name, section, line, via, callable_form=callable_form)
    file_mentions = _file_mentions(name, section, line, via)
    if file_mentions:
        return file_mentions
    if _QUALIFIED.fullmatch(name):
        return [Mention(name, MentionKind.QUALIFIED_NAME, section, line, via)]
    return []


def _identifier_mentions(
    name: str, section: Section | None, line: int, via: MentionVia, *, callable_form: bool
) -> list[Mention]:
    """識別子の形のスパン。型の名前らしいものだけを名指しとして残す。"""
    if name in _NOT_NAMES or not (callable_form or _NAMELIKE.fullmatch(name)):
        return []
    return [Mention(name, MentionKind.TYPE, section, line, via)]


def _file_mentions(
    name: str, section: Section | None, line: int, via: MentionVia
) -> list[Mention]:
    """パスかファイル名の形のスパン。どちらでもなければ空を返す。"""
    file_suffix = _file_suffix(name)
    if _PATH.fullmatch(name) or (_BARE_FILE.fullmatch(name) and file_suffix in _known_suffixes()):
        if "/" not in name and file_suffix in STORED_FILE_SUFFIXES:
            return [Mention(name, MentionKind.STORED_FILE, section, line, via)]
        return [Mention(name.rstrip("/"), MentionKind.PATH, section, line, via)]
    return []


def _known_suffixes() -> frozenset[str]:
    return STORED_FILE_SUFFIXES | FILE_SUFFIXES


def _file_suffix(name: str) -> str:
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _command_mentions(
    words: list[str], section: Section | None, line: int, via: MentionVia
) -> list[Mention]:
    mentions: list[Mention] = []
    if words and _SUBCOMMAND_NAME.fullmatch(words[0]):
        mentions.append(Mention(words[0], MentionKind.SUBCOMMAND, section, line, via))
    for word in words:
        option = word.split("=")[0]
        if _OPTION.fullmatch(option):
            mentions.append(Mention(option, MentionKind.OPTION, section, line, via))
    return mentions
