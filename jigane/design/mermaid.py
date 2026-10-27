"""Mermaid の図の要素を読む。

図の種類ごとに、要素の宣言の行だけを正規表現で読む。Mermaid の文法の全体は読まない。
読めない種類の図は UnsupportedDiagram に、読めない行は unreadable に、原文のまま残す。
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import ClassVar

_TYPED_FIELD_MIN_TOKENS = 2  # 型と名前
_QUOTE_PAIR_LENGTH = 2  # 開きと閉じの引用符


class DiagramKind(StrEnum):
    CLASS = "class"
    ER = "er"
    SEQUENCE = "sequence"
    FLOWCHART = "flowchart"
    UNSUPPORTED = "unsupported"


class UnreadableReason(StrEnum):
    """読めなかった理由。図の中の行も、文書の見出し、表、柵も、同じ理由の型で持つ。"""

    UNRECOGNIZED = "unrecognized"
    UNCLOSED_BRACE = "unclosed_brace"
    UNCLOSED_FRONT_MATTER = "unclosed_front_matter"
    UNRECOGNIZED_AFTER_HEADER = "unrecognized_after_header"
    UNCLOSED_FENCE = "unclosed_fence"
    TABLE_DELIMITER_MISMATCH = "table_delimiter_mismatch"
    TABLE_ROW_MISMATCH = "table_row_mismatch"

    @property
    def label(self) -> str:
        return _UNREADABLE_LABELS[self]


_UNREADABLE_LABELS = {
    UnreadableReason.UNRECOGNIZED: "読み方の分からない行",
    UnreadableReason.UNRECOGNIZED_AFTER_HEADER: "宣言行の続きの読み方の分からない部分",
    UnreadableReason.UNCLOSED_BRACE: "閉じない波括弧",
    UnreadableReason.UNCLOSED_FRONT_MATTER: "閉じない front matter",
    UnreadableReason.UNCLOSED_FENCE: "閉じない柵",
    UnreadableReason.TABLE_DELIMITER_MISMATCH: "区切り行の列の数が見出しと合わない表",
    UnreadableReason.TABLE_ROW_MISMATCH: "列の数が見出しと合わない表の行",
}


@dataclass(frozen=True)
class UnreadableLine:
    """読めなかった行。line_number は文書全体での 1 始まりの行、text は原文の行そのもの。"""

    line_number: int
    text: str
    reason: UnreadableReason = UnreadableReason.UNRECOGNIZED


@dataclass(frozen=True, kw_only=True)
class Diagram:
    source: str
    unreadable: tuple[UnreadableLine, ...] = ()


# --- クラス図 ---


class ClassRelationKind(StrEnum):
    INHERITANCE = "inheritance"
    COMPOSITION = "composition"
    AGGREGATION = "aggregation"
    ASSOCIATION = "association"
    DEPENDENCY = "dependency"
    REALIZATION = "realization"
    LINK = "link"
    DASHED_LINK = "dashed_link"


class ClassArrow(StrEnum):
    """クラス図の関係の記号。値は原文の綴り。"""

    INHERITS_FROM = "<|--"
    INHERITED_BY = "--|>"
    COMPOSED_LEFT = "*--"
    COMPOSED_RIGHT = "--*"
    AGGREGATED_LEFT = "o--"
    AGGREGATED_RIGHT = "--o"
    ASSOCIATES_LEFT = "<--"
    ASSOCIATES_RIGHT = "-->"
    DEPENDS_RIGHT = "..>"
    DEPENDS_LEFT = "<.."
    REALIZES_RIGHT = "..|>"
    REALIZES_LEFT = "<|.."
    LINK = "--"
    DASHED_LINK = ".."

    @property
    def kind(self) -> ClassRelationKind:
        return _CLASS_ARROW_KINDS[self]


@dataclass(frozen=True)
class ClassField:
    name: str
    type: str | None
    text: str


@dataclass(frozen=True)
class ClassMethod:
    name: str
    text: str


@dataclass(frozen=True)
class ClassType:
    name: str
    stereotype: str | None
    fields: tuple[ClassField, ...]
    methods: tuple[ClassMethod, ...]


@dataclass(frozen=True)
class ClassRelation:
    left: str
    right: str
    kind: ClassRelationKind
    arrow: ClassArrow
    label: str | None


@dataclass(frozen=True, kw_only=True)
class ClassDiagram(Diagram):
    kind: ClassVar[DiagramKind] = DiagramKind.CLASS
    types: tuple[ClassType, ...] = ()
    relations: tuple[ClassRelation, ...] = ()


# --- ER 図 ---


class ErKey(StrEnum):
    PRIMARY = "PK"
    FOREIGN = "FK"
    UNIQUE = "UK"


class ErCardinality(StrEnum):
    """ER 図の多重度。左右で綴りが違う（`|o` と `o|`）ため、意味で持つ。"""

    ZERO_OR_ONE = "zero_or_one"
    EXACTLY_ONE = "exactly_one"
    ZERO_OR_MORE = "zero_or_more"
    ONE_OR_MORE = "one_or_more"


@dataclass(frozen=True)
class ErAttribute:
    type: str
    name: str
    keys: tuple[ErKey, ...]
    comment: str | None


@dataclass(frozen=True)
class ErEntity:
    name: str
    attributes: tuple[ErAttribute, ...]


@dataclass(frozen=True)
class ErRelation:
    left: str
    right: str
    left_cardinality: ErCardinality
    right_cardinality: ErCardinality
    identifying: bool
    label: str | None


@dataclass(frozen=True, kw_only=True)
class ErDiagram(Diagram):
    kind: ClassVar[DiagramKind] = DiagramKind.ER
    entities: tuple[ErEntity, ...] = ()
    relations: tuple[ErRelation, ...] = ()


# --- シーケンス図 ---


class SequenceArrow(StrEnum):
    """シーケンス図の矢印。値は原文の綴り。"""

    SOLID_ARROW = "->>"
    DOTTED_ARROW = "-->>"
    SOLID_CROSS = "-x"
    DOTTED_CROSS = "--x"
    SOLID_ASYNC = "-)"
    DOTTED_ASYNC = "--)"
    SOLID_OPEN = "->"
    DOTTED_OPEN = "-->"


@dataclass(frozen=True)
class SequenceParticipant:
    name: str
    alias: str | None


@dataclass(frozen=True)
class SequenceMessage:
    sender: str
    receiver: str
    text: str
    arrow: SequenceArrow


@dataclass(frozen=True, kw_only=True)
class SequenceDiagram(Diagram):
    kind: ClassVar[DiagramKind] = DiagramKind.SEQUENCE
    participants: tuple[SequenceParticipant, ...] = ()
    messages: tuple[SequenceMessage, ...] = ()


# --- flowchart ---


class FlowDirection(StrEnum):
    TOP_BOTTOM = "TB"
    TOP_DOWN = "TD"
    BOTTOM_TOP = "BT"
    RIGHT_LEFT = "RL"
    LEFT_RIGHT = "LR"


class FlowLinkStyle(StrEnum):
    SOLID = "solid"
    THICK = "thick"
    DOTTED = "dotted"
    INVISIBLE = "invisible"


class FlowLinkEnd(StrEnum):
    """辺の端の形。矢じりなら ARROW、端に何も付かなければ NONE。"""

    NONE = "none"
    ARROW = "arrow"
    CIRCLE = "circle"
    CROSS = "cross"


@dataclass(frozen=True)
class FlowNode:
    id: str
    label: str | None


@dataclass(frozen=True)
class FlowEdge:
    source: str
    target: str
    label: str | None
    # 辺の記号の原文。ラベルを間に挟む書き方では、挟んだ全体を持つ
    link: str
    style: FlowLinkStyle
    source_end: FlowLinkEnd
    target_end: FlowLinkEnd


@dataclass(frozen=True, kw_only=True)
class FlowchartDiagram(Diagram):
    kind: ClassVar[DiagramKind] = DiagramKind.FLOWCHART
    # 宣言に向きの指定が無ければ None
    direction: FlowDirection | None = None
    nodes: tuple[FlowNode, ...] = ()
    edges: tuple[FlowEdge, ...] = ()


@dataclass(frozen=True, kw_only=True)
class UnsupportedDiagram(Diagram):
    kind: ClassVar[DiagramKind] = DiagramKind.UNSUPPORTED
    declared_type: str = ""


AnyDiagram = ClassDiagram | ErDiagram | SequenceDiagram | FlowchartDiagram | UnsupportedDiagram


_FLOW_HEADER_DIRECTION = re.compile(r"^(?P<direction>TB|TD|BT|RL|LR)\b;?\s*(?P<rest>.*)$")


def parse_diagram(source: str, first_line: int = 1) -> AnyDiagram:
    """図の原文を読む。first_line は原文の 1 行目の、文書全体での行番号。"""
    significant, unreadable = _significant_lines(source, first_line)
    if not significant:
        return UnsupportedDiagram(source=source, declared_type="", unreadable=tuple(unreadable))
    number, header = significant[0]
    word = header.split()[0]
    # 宣言語と同じ行の続きは、次の行として読む（`graph TD; A-->B` の `A-->B` など）
    rest = header[len(word) :].strip()
    direction: FlowDirection | None = None
    is_flowchart = word in ("flowchart", "graph", "flowchart-elk")
    if is_flowchart:
        found = _FLOW_HEADER_DIRECTION.match(rest)
        if found:
            direction = FlowDirection(found["direction"])
            rest = found["rest"].strip()
    following = significant[1:]
    if rest.removesuffix(";").strip():
        following = [(number, rest), *following]
    lines = iter(following)
    diagram: AnyDiagram
    if word in ("classDiagram", "classDiagram-v2"):
        diagram = _parse_class(source, lines)
    elif word == "erDiagram":
        diagram = _parse_er(source, lines)
    elif word == "sequenceDiagram":
        diagram = _parse_sequence(source, lines)
    elif is_flowchart:
        diagram = _parse_flowchart(source, lines, direction)
    else:
        diagram = UnsupportedDiagram(source=source, declared_type=word)
    # 各読みは前後の空白を除いた行や宣言行の続きを持つので、text は原文の行に揃える。
    # 宣言行の行番号で読めなかったものは、
    # 宣言行の続きである（宣言語そのものは読めない行にならない）
    raw_lines = source.split("\n")
    merged = sorted([*unreadable, *diagram.unreadable], key=lambda item: item.line_number)
    normalized = tuple(
        replace(
            item,
            text=raw_lines[item.line_number - first_line],
            reason=UnreadableReason.UNRECOGNIZED_AFTER_HEADER
            if item.line_number == number and item.reason == UnreadableReason.UNRECOGNIZED
            else item.reason,
        )
        for item in merged
    )
    return replace(diagram, unreadable=normalized)


def _significant_lines(
    source: str, first_line: int
) -> tuple[list[tuple[int, str]], list[UnreadableLine]]:
    """空行、`%%` のコメント、先頭の front matter を除いた (行番号, 前後の空白を除いた行)。

    front matter が閉じないときは、開いた行より後ろの行を、読めない行として返す。
    """
    significant: list[tuple[int, str]] = []
    # 閉じるかどうかは最後まで読まないと決まらないので、front matter の中の行は一旦ここに溜める
    inside: list[UnreadableLine] = []
    in_front_matter = False
    seen_content = False
    for number, raw in enumerate(source.split("\n"), start=first_line):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if line == "---" and (in_front_matter or not seen_content):
            in_front_matter = not in_front_matter
            inside = []
            continue
        if in_front_matter:
            inside.append(UnreadableLine(number, raw, UnreadableReason.UNCLOSED_FRONT_MATTER))
            continue
        seen_content = True
        significant.append((number, line))
    return significant, (inside if in_front_matter else [])


# --- クラス図の読み ---

_CLASS_ARROW_KINDS = {
    ClassArrow.INHERITS_FROM: ClassRelationKind.INHERITANCE,
    ClassArrow.INHERITED_BY: ClassRelationKind.INHERITANCE,
    ClassArrow.COMPOSED_LEFT: ClassRelationKind.COMPOSITION,
    ClassArrow.COMPOSED_RIGHT: ClassRelationKind.COMPOSITION,
    ClassArrow.AGGREGATED_LEFT: ClassRelationKind.AGGREGATION,
    ClassArrow.AGGREGATED_RIGHT: ClassRelationKind.AGGREGATION,
    ClassArrow.ASSOCIATES_LEFT: ClassRelationKind.ASSOCIATION,
    ClassArrow.ASSOCIATES_RIGHT: ClassRelationKind.ASSOCIATION,
    ClassArrow.DEPENDS_RIGHT: ClassRelationKind.DEPENDENCY,
    ClassArrow.DEPENDS_LEFT: ClassRelationKind.DEPENDENCY,
    ClassArrow.REALIZES_RIGHT: ClassRelationKind.REALIZATION,
    ClassArrow.REALIZES_LEFT: ClassRelationKind.REALIZATION,
    ClassArrow.LINK: ClassRelationKind.LINK,
    ClassArrow.DASHED_LINK: ClassRelationKind.DASHED_LINK,
}
_CLASS_NAME = r"[\w.~]+"
_CLASS_ARROW_ALTERNATION = "|".join(
    re.escape(arrow.value)
    for arrow in sorted(ClassArrow, key=lambda a: len(a.value), reverse=True)
)
_CLASS_RELATION = re.compile(
    rf"^(?P<left>{_CLASS_NAME})\s*(?:\"[^\"]*\"\s*)?"
    rf"(?P<arrow>{_CLASS_ARROW_ALTERNATION})"
    rf"\s*(?:\"[^\"]*\"\s*)?(?P<right>{_CLASS_NAME})(?:\s*:\s*(?P<label>.*))?$"
)
_CLASS_DECLARATION = re.compile(rf"^class\s+(?P<name>{_CLASS_NAME})(?::::\S+)?\s*(?P<open>\{{)?$")
_CLASS_STEREOTYPE = re.compile(rf"^<<(?P<stereotype>[^>]+)>>\s*(?P<name>{_CLASS_NAME})?$")
_CLASS_MEMBER = re.compile(rf"^(?P<name>{_CLASS_NAME})\s*:\s*(?P<member>.+)$")
# 図の配置だけを決める行。要素を宣言しない
_CLASS_LAYOUT = re.compile(r"^direction\s+(TB|BT|LR|RL)$")
_VISIBILITY = "+-#~"


@dataclass
class _ClassBuilder:
    stereotype: str | None = None
    fields: list[ClassField] = field(default_factory=list)
    methods: list[ClassMethod] = field(default_factory=list)


def _class_member(text: str) -> ClassField | ClassMethod:
    body = text.strip().lstrip(_VISIBILITY).strip().rstrip("$*").strip()
    if "(" in body:
        head = body[: body.index("(")].split()
        return ClassMethod(name=head[-1] if head else body, text=text)
    if ":" in body:
        name, _, field_type = body.partition(":")
        return ClassField(name=name.strip(), type=field_type.strip() or None, text=text)
    tokens = body.split()
    if len(tokens) >= _TYPED_FIELD_MIN_TOKENS:
        return ClassField(name=tokens[-1], type=" ".join(tokens[:-1]), text=text)
    return ClassField(name=body, type=None, text=text)


def _parse_class(source: str, lines: Iterator[tuple[int, str]]) -> ClassDiagram:
    builders: dict[str, _ClassBuilder] = {}
    relations: list[ClassRelation] = []
    unreadable: list[UnreadableLine] = []
    current: str | None = None
    opened_at = 0
    opened_line = ""
    for number, line in lines:
        if current is not None:
            current = _read_class_block_line(builders, current, line)
            continue
        opened = _read_class_statement(builders, relations, unreadable, number, line)
        if opened is not None:
            current = opened
            opened_at = number
            opened_line = line
    if current is not None:
        # 閉じていない波括弧は、開いた行を原文のまま読めない行として残す
        unreadable.append(UnreadableLine(opened_at, opened_line, UnreadableReason.UNCLOSED_BRACE))
    types = tuple(
        ClassType(name, found.stereotype, tuple(found.fields), tuple(found.methods))
        for name, found in builders.items()
    )
    return ClassDiagram(
        source=source, types=types, relations=tuple(relations), unreadable=tuple(unreadable)
    )


def _class_builder(builders: dict[str, _ClassBuilder], name: str) -> _ClassBuilder:
    return builders.setdefault(name, _ClassBuilder())


def _read_class_block_line(
    builders: dict[str, _ClassBuilder], current: str, line: str
) -> str | None:
    """波括弧の中の 1 行を読む。閉じる行なら None を、続くなら今のクラス名を返す。"""
    stereotype = _CLASS_STEREOTYPE.match(line)
    if line == "}":
        return None
    if stereotype and not stereotype["name"]:
        _class_builder(builders, current).stereotype = stereotype["stereotype"].strip()
    else:
        _add_member(_class_builder(builders, current), _class_member(line))
    return current


def _read_class_statement(
    builders: dict[str, _ClassBuilder],
    relations: list[ClassRelation],
    unreadable: list[UnreadableLine],
    number: int,
    line: str,
) -> str | None:
    """波括弧の外の 1 行を読む。波括弧を開く宣言ならそのクラス名を、それ以外は None を返す。"""
    declaration = _CLASS_DECLARATION.match(line)
    stereotype = _CLASS_STEREOTYPE.match(line)
    relation = _CLASS_RELATION.match(line)
    member = _CLASS_MEMBER.match(line)
    if declaration:
        _class_builder(builders, declaration["name"])
        return declaration["name"] if declaration["open"] else None
    if stereotype and stereotype["name"]:
        _class_builder(builders, stereotype["name"]).stereotype = stereotype["stereotype"].strip()
    elif relation:
        _class_builder(builders, relation["left"])
        _class_builder(builders, relation["right"])
        relations.append(_class_relation(relation))
    elif member:
        _add_member(_class_builder(builders, member["name"]), _class_member(member["member"]))
    elif _CLASS_LAYOUT.match(line):
        # 配置の指定だけの行は、読めているので読めない行に数えない
        pass
    else:
        unreadable.append(UnreadableLine(number, line))
    return None


def _class_relation(relation: re.Match[str]) -> ClassRelation:
    label = relation["label"]
    return ClassRelation(
        left=relation["left"],
        right=relation["right"],
        kind=ClassArrow(relation["arrow"]).kind,
        arrow=ClassArrow(relation["arrow"]),
        label=label.strip() if label else None,
    )


def _add_member(target: _ClassBuilder, member: ClassField | ClassMethod) -> None:
    if isinstance(member, ClassMethod):
        target.methods.append(member)
    else:
        target.fields.append(member)


# --- ER 図の読み ---

# 実体名は `-` や `.` を含められる。`|` `{` `}` は関係や括弧の記号と取り違えるので入れない
_ER_NAME = r"[^\s{}|:]+"
_ER_RELATION = re.compile(
    rf"^(?P<left>{_ER_NAME})\s+(?P<lc>[|}}][|o])(?P<line>--|\.\.)(?P<rc>[o|][|{{])"
    rf"\s+(?P<right>{_ER_NAME})(?:\s*:\s*(?P<label>.*))?$"
)
_ER_LEFT_CARDINALITIES = {
    "|o": ErCardinality.ZERO_OR_ONE,
    "||": ErCardinality.EXACTLY_ONE,
    "}o": ErCardinality.ZERO_OR_MORE,
    "}|": ErCardinality.ONE_OR_MORE,
}
_ER_RIGHT_CARDINALITIES = {
    "o|": ErCardinality.ZERO_OR_ONE,
    "||": ErCardinality.EXACTLY_ONE,
    "o{": ErCardinality.ZERO_OR_MORE,
    "|{": ErCardinality.ONE_OR_MORE,
}
_ER_BLOCK = re.compile(rf"^(?P<name>{_ER_NAME})\s*\{{$")
_ER_ATTRIBUTE = re.compile(
    r"^(?P<type>\S+)\s+(?P<name>\S+)"
    r"(?:\s+(?P<keys>(?:PK|FK|UK)(?:\s*,\s*(?:PK|FK|UK))*))?"
    r"(?:\s+\"(?P<comment>[^\"]*)\")?$"
)
_ER_ENTITY_ONLY = re.compile(rf"^{_ER_NAME}$")


def _parse_er(source: str, lines: Iterator[tuple[int, str]]) -> ErDiagram:
    attributes: dict[str, list[ErAttribute]] = {}
    relations: list[ErRelation] = []
    unreadable: list[UnreadableLine] = []
    current: str | None = None
    opened_at = 0
    opened_line = ""
    for number, line in lines:
        if current is not None:
            attribute = _ER_ATTRIBUTE.match(line)
            if line == "}":
                current = None
            elif attribute:
                keys = attribute["keys"]
                attributes[current].append(
                    ErAttribute(
                        type=attribute["type"],
                        name=attribute["name"],
                        keys=tuple(ErKey(key.strip()) for key in keys.split(",")) if keys else (),
                        comment=attribute["comment"],
                    )
                )
            else:
                unreadable.append(UnreadableLine(number, line))
            continue
        block = _ER_BLOCK.match(line)
        relation = _ER_RELATION.match(line)
        if relation:
            attributes.setdefault(relation["left"], [])
            attributes.setdefault(relation["right"], [])
            label = relation["label"]
            relations.append(
                ErRelation(
                    left=relation["left"],
                    right=relation["right"],
                    left_cardinality=_ER_LEFT_CARDINALITIES[relation["lc"]],
                    right_cardinality=_ER_RIGHT_CARDINALITIES[relation["rc"]],
                    identifying=relation["line"] == "--",
                    label=label.strip().strip('"') if label else None,
                )
            )
        elif block:
            current = block["name"]
            opened_at = number
            opened_line = line
            attributes.setdefault(current, [])
        elif _ER_ENTITY_ONLY.match(line):
            attributes.setdefault(line, [])
        else:
            unreadable.append(UnreadableLine(number, line))
    if current is not None:
        unreadable.append(UnreadableLine(opened_at, opened_line, UnreadableReason.UNCLOSED_BRACE))
    entities = tuple(ErEntity(name, tuple(found)) for name, found in attributes.items())
    return ErDiagram(
        source=source, entities=entities, relations=tuple(relations), unreadable=tuple(unreadable)
    )


# --- シーケンス図の読み ---

_SEQUENCE_PARTICIPANT = re.compile(
    r"^(?:participant|actor)\s+(?P<name>\S+)(?:\s+as\s+(?P<alias>.+))?$"
)
# 矢印は長いものから試す。送り手は最短から伸ばし、受け手は矢印を含まない名前に限る。
# `A-xyz->>B` は受け手が矢印を含めないので、送り手が `A-xyz` まで伸びる
_SEQUENCE_ARROWS = r"-->>|->>|--x|-x|--\)|-\)|-->|->"
_SEQUENCE_MESSAGE = re.compile(
    rf"^(?P<sender>\S+?)\s*(?P<arrow>{_SEQUENCE_ARROWS})(?P<activation>[+-]?)\s*"
    rf"(?P<receiver>(?:(?!{_SEQUENCE_ARROWS})[^\s:])+)\s*:\s*(?P<text>.*)$"
)
# メッセージではない制御の行。ブロックの開閉、注、活性化、番号付けは、参加者でもメッセージでもない
_SEQUENCE_STRUCTURE = re.compile(
    r"^(autonumber|activate|deactivate|loop|alt|else|opt|par|and|end|rect|critical|option|break"
    r"|box|title|note)\b",
    re.IGNORECASE,
)


def _parse_sequence(source: str, lines: Iterator[tuple[int, str]]) -> SequenceDiagram:
    participants: dict[str, str | None] = {}
    messages: list[SequenceMessage] = []
    unreadable: list[UnreadableLine] = []
    for number, line in lines:
        participant = _SEQUENCE_PARTICIPANT.match(line)
        message = _SEQUENCE_MESSAGE.match(line)
        if participant:
            alias = participant["alias"]
            participants[participant["name"]] = alias.strip() if alias else None
        elif message:
            # 宣言の無い参加者も、メッセージに現れた順で参加者に数える
            participants.setdefault(message["sender"], None)
            participants.setdefault(message["receiver"], None)
            messages.append(
                SequenceMessage(
                    sender=message["sender"],
                    receiver=message["receiver"],
                    text=message["text"].strip(),
                    arrow=SequenceArrow(message["arrow"]),
                )
            )
        elif _SEQUENCE_STRUCTURE.match(line):
            continue
        else:
            unreadable.append(UnreadableLine(number, line))
    return SequenceDiagram(
        source=source,
        participants=tuple(
            SequenceParticipant(name, alias) for name, alias in participants.items()
        ),
        messages=tuple(messages),
        unreadable=tuple(unreadable),
    )


# --- flowchart の読み ---

# (開き, 閉じの候補)。長い開きを先に試す
_FLOW_SHAPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("(((", (")))",)),
    ("((", ("))",)),
    ("([", ("])",)),
    ("[[", ("]]",)),
    ("[(", (")]",)),
    ("{{", ("}}",)),
    ("[/", ("/]", "\\]")),
    ("[\\", ("\\]", "/]")),
    ("(", (")",)),
    ("[", ("]",)),
    ("{", ("}",)),
    (">", ("]",)),
)
_FLOW_ID = re.compile(r"\w+")
_FLOW_CLASS_SUFFIX = re.compile(r":::\w+")
_FLOW_LINK_WITH_TEXT = re.compile(
    r"(?:--|==|-\.)\s+(?P<text>.+?)\s+(?:-{2,}[>ox]?|={2,}>?|\.-+>?)(?=\s|$)"
)
_FLOW_LINK = re.compile(r"[<ox]?(?:-{2,}|={2,}|-\.+-|~{3,})[>ox]?")
_FLOW_LINK_PIPE_TEXT = re.compile(r"\s*\|(?P<text>[^|]*)\|")
_FLOW_LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
# 節点と辺を宣言しない行。入れ物の囲い、配置、見た目の指定。
# 予約語の後ろが空白と引数の形のときだけ当て、後ろが辺の記号なら節点の名前として読む
# （`class --> B` は辺）。`end` は引数を持たないため、単独のときだけ当てる
_FLOW_NON_ELEMENT = re.compile(
    r"^(?:end\s*;?$|(?:subgraph|direction|style|classDef|class|linkStyle|click)"
    r"\s+(?![<ox]?(?:-{2,}|={2,}|-\.|~{3,}))\S)"
)


def _flow_label(text: str) -> str:
    text = text.strip()
    if len(text) >= _QUOTE_PAIR_LENGTH and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return _FLOW_LINE_BREAK.sub("\n", text)


def _flow_node(text: str, position: int) -> tuple[FlowNode, int] | None:
    found = _FLOW_ID.match(text, position)
    if not found:
        return None
    node_id = found[0]
    position = found.end()
    label: str | None = None
    for opening, closings in _FLOW_SHAPES:
        if not text.startswith(opening, position):
            continue
        start = position + len(opening)
        search_from = start
        if text.startswith('"', start):
            quote_end = text.find('"', start + 1)
            if quote_end < 0:
                return None
            search_from = quote_end + 1
        ends = [
            (index, closing)
            for closing in closings
            if (index := text.find(closing, search_from)) >= 0
        ]
        if not ends:
            return None
        index, closing = min(ends)
        label = _flow_label(text[start:index])
        position = index + len(closing)
        break
    suffix = _FLOW_CLASS_SUFFIX.match(text, position)
    if suffix:
        position = suffix.end()
    return FlowNode(node_id, label), position


def _flow_link(text: str, position: int) -> tuple[str, str | None, int] | None:
    """(辺の記号, ラベル, 次の位置)。"""
    with_text = _FLOW_LINK_WITH_TEXT.match(text, position)
    if with_text:
        return with_text[0], _flow_label(with_text["text"]), with_text.end()
    plain = _FLOW_LINK.match(text, position)
    if not plain:
        return None
    position = plain.end()
    piped = _FLOW_LINK_PIPE_TEXT.match(text, position)
    if piped:
        return plain[0], _flow_label(piped["text"]), piped.end()
    return plain[0], None, position


_FLOW_END_BY_CHARACTER = {">": FlowLinkEnd.ARROW, "o": FlowLinkEnd.CIRCLE, "x": FlowLinkEnd.CROSS}


def _flow_edge(source: str, target: str, label: str | None, link: str) -> FlowEdge:
    tokens = link.split()
    first, last = tokens[0], tokens[-1]
    if "=" in first:
        style = FlowLinkStyle.THICK
    elif "~" in first:
        style = FlowLinkStyle.INVISIBLE
    elif "." in first:
        style = FlowLinkStyle.DOTTED
    else:
        style = FlowLinkStyle.SOLID
    source_end = {"<": FlowLinkEnd.ARROW, "o": FlowLinkEnd.CIRCLE, "x": FlowLinkEnd.CROSS}.get(
        first[0], FlowLinkEnd.NONE
    )
    target_end = _FLOW_END_BY_CHARACTER.get(last[-1], FlowLinkEnd.NONE)
    return FlowEdge(source, target, label, link, style, source_end, target_end)


def _flow_statement(text: str) -> tuple[list[FlowNode], list[FlowEdge]] | None:
    text = text.rstrip().removesuffix(";").rstrip()
    first = _flow_node(text, 0)
    if first is None:
        return None
    nodes = [first[0]]
    edges: list[FlowEdge] = []
    position = first[1]
    while True:
        while position < len(text) and text[position] == " ":
            position += 1
        if position >= len(text):
            return nodes, edges
        link = _flow_link(text, position)
        if link is None:
            return None
        while link[2] < len(text) and text[link[2]] == " ":
            link = (link[0], link[1], link[2] + 1)
        following = _flow_node(text, link[2])
        if following is None:
            return None
        edges.append(_flow_edge(nodes[-1].id, following[0].id, link[1], link[0]))
        nodes.append(following[0])
        position = following[1]


def _parse_flowchart(
    source: str, lines: Iterator[tuple[int, str]], direction: FlowDirection | None
) -> FlowchartDiagram:
    labels: dict[str, str | None] = {}
    edges: list[FlowEdge] = []
    unreadable: list[UnreadableLine] = []
    for number, line in lines:
        if _FLOW_NON_ELEMENT.match(line):
            continue
        statement = _flow_statement(line)
        if statement is None:
            unreadable.append(UnreadableLine(number, line))
            continue
        for node in statement[0]:
            # 同じ節点は何度も現れ、ラベルは最初に付けた所だけにある
            if labels.get(node.id) is None:
                labels[node.id] = node.label
        edges.extend(statement[1])
    return FlowchartDiagram(
        source=source,
        direction=direction,
        nodes=tuple(FlowNode(node_id, label) for node_id, label in labels.items()),
        edges=tuple(edges),
        unreadable=tuple(unreadable),
    )
