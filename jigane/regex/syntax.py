"""四つの方言の共通部分を読む正規表現の構文解析。読めない書き方は木を返さず Unreadable を返す。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from jigane.languages.base import RegexDialect

# 括弧の入れ子の上限。実在の正規表現は十段に届かず、解析と計測の再帰が収まる値
_MAX_GROUP_DEPTH = 50
_MAX_CONDITIONAL_BRANCHES = 2  # 条件つきの組は then と else の二つまで


class QuantifierMode(StrEnum):
    GREEDY = "greedy"
    LAZY = "lazy"
    POSSESSIVE = "possessive"


class AtomKind(StrEnum):
    CHAR = "char"
    ESCAPE = "escape"
    CLASS = "class"
    PROPERTY = "property"
    ANY = "any"
    ANCHOR = "anchor"


@dataclass(frozen=True)
class Alternation:
    """選択。各選択肢は一つの節点（並び）で持つ。"""

    branches: tuple[RegexNode, ...]


@dataclass(frozen=True)
class Sequence:
    """並び。"""

    items: tuple[RegexNode, ...]


@dataclass(frozen=True)
class Flags:
    """旗の集まり。`(?i-s)` なら enabled が {i}、disabled が {s}。"""

    enabled: frozenset[str]
    disabled: frozenset[str]


@dataclass(frozen=True)
class NumberedReference:
    """番号で指す組の参照。"""

    number: int


@dataclass(frozen=True)
class NamedReference:
    """名前で指す組の参照。NamedGroup.name と突き合わせられる。"""

    name: str


GroupReference = NumberedReference | NamedReference


@dataclass(frozen=True)
class CaptureGroup:
    """捕獲の組。本文は一つの節点で持つ。"""

    body: RegexNode


@dataclass(frozen=True)
class NonCaptureGroup:
    body: RegexNode


@dataclass(frozen=True)
class NamedGroup:
    name: str
    body: RegexNode


@dataclass(frozen=True)
class AtomicGroup:
    body: RegexNode


@dataclass(frozen=True)
class FlaggedGroup:
    flags: Flags
    body: RegexNode


Group = CaptureGroup | NonCaptureGroup | NamedGroup | AtomicGroup | FlaggedGroup


@dataclass(frozen=True)
class Look:
    """先読みと後読み。"""

    ahead: bool
    negated: bool
    body: RegexNode


@dataclass(frozen=True)
class Conditional:
    """条件つきの組。"""

    condition: GroupReference
    yes: RegexNode
    no: RegexNode | None


@dataclass(frozen=True)
class Quantifier:
    """量指定子。maximum が None なら上限なし。"""

    target: RegexNode
    minimum: int
    maximum: int | None
    mode: QuantifierMode


@dataclass(frozen=True)
class CharClass:
    negated: bool
    has_intersection: bool


@dataclass(frozen=True)
class Backreference:
    """後方参照。"""

    reference: GroupReference


@dataclass(frozen=True)
class Atom:
    """一文字。text は書かれたとおりの文字列。"""

    kind: AtomKind
    text: str


@dataclass(frozen=True)
class Quote:
    r"""`\Q..\E` の引用。"""

    text: str


@dataclass(frozen=True)
class LeadingFlags:
    """`(?i)` のような旗だけの組。"""

    flags: Flags


RegexNode = (
    Alternation
    | Sequence
    | CaptureGroup
    | NonCaptureGroup
    | NamedGroup
    | AtomicGroup
    | FlaggedGroup
    | Look
    | Conditional
    | Quantifier
    | CharClass
    | Backreference
    | Atom
    | Quote
    | LeadingFlags
)


@dataclass(frozen=True)
class Unreadable:
    """読めなかった書き方。position は 0 始まりの文字の位置。"""

    position: int
    reason: str


class _ParseError(Exception):
    def __init__(self, position: int, reason: str) -> None:
        super().__init__(reason)
        self.position = position
        self.reason = reason


_FLAG_LETTERS: dict[RegexDialect, str] = {
    RegexDialect.PYTHON: "aiLmsux-",
    RegexDialect.JAVA: "idmsuxU-",
    RegexDialect.DOTNET: "imnsx-",
    RegexDialect.SWIFT: "imsxuUwWdD-",
}
_NAME = re.compile(r"\w+")
_BRACES = re.compile(r"\{(\d+)(?:(,)(\d*))?\}|\{,(\d+)\}")
_DEFAULT_CLASSES = "dDwWsShHvVR"
_ANCHOR_ESCAPES = "bBAzZG"


def _to_flags(text: str) -> Flags:
    enabled, _, disabled = text.partition("-")
    return Flags(frozenset(enabled), frozenset(disabled))


def _condition_reference(text: str) -> GroupReference | None:
    if text.isdecimal():
        return NumberedReference(int(text))
    if _NAME.fullmatch(text):
        return NamedReference(text)
    return None


class _Parser:
    def __init__(self, pattern: str, dialect: RegexDialect) -> None:
        self.text = pattern
        self.dialect = dialect
        self.pos = 0
        self.depth = 0

    def fail(self, reason: str, position: int | None = None) -> _ParseError:
        return _ParseError(self.pos if position is None else position, reason)

    def peek(self, count: int = 1) -> str:
        return self.text[self.pos : self.pos + count]

    def parse_all(self) -> RegexNode:
        node = self.parse_alternation()
        if self.pos < len(self.text):
            # parse_alternation は ')' で止まる。最上位で残るのは '(' の無い ')' だけ
            raise self.fail("対応する開き括弧の無い閉じ括弧")
        return node

    def parse_alternation(self) -> RegexNode:
        branches = [self.parse_sequence()]
        while self.peek() == "|":
            self.pos += 1
            branches.append(self.parse_sequence())
        if len(branches) == 1:
            return branches[0]
        return Alternation(tuple(branches))

    def starts_quantifier(self) -> bool:
        ch = self.peek()
        return ch in ("*", "+", "?") or (ch == "{" and self.match_braces() is not None)

    def parse_sequence(self) -> Sequence:
        items: list[RegexNode] = []
        while self.pos < len(self.text) and self.peek() not in ("|", ")"):
            start = self.pos
            if self.starts_quantifier():
                raise self.fail("量指定子の対象が無い")
            atom = self.parse_atom()
            items.append(self.parse_quantifier(atom, start))
        return Sequence(tuple(items))

    def match_braces(self) -> tuple[int, int | None, int] | None:
        m = _BRACES.match(self.text, self.pos)
        if m is None:
            return None
        if m.group(4) is not None:
            if self.dialect is not RegexDialect.PYTHON:
                return None
            return 0, int(m.group(4)), m.end()
        low = int(m.group(1))
        if m.group(2) is None:
            return low, low, m.end()
        high = int(m.group(3)) if m.group(3) else None
        return low, high, m.end()

    def parse_quantifier(self, target: RegexNode, start: int) -> RegexNode:
        ch = self.peek()
        braces = self.match_braces() if ch == "{" else None
        if ch in ("*", "+", "?"):
            low, high = {"*": (0, None), "+": (1, None), "?": (0, 1)}[ch]
            self.pos += 1
        elif braces is not None:
            low, high, self.pos = braces
        else:
            return target
        if isinstance(target, LeadingFlags):
            raise self.fail("量指定子の対象が無い", start)
        mode = QuantifierMode.GREEDY
        if self.peek() == "?":
            mode = QuantifierMode.LAZY
            self.pos += 1
        elif self.peek() == "+":
            mode = QuantifierMode.POSSESSIVE
            self.pos += 1
        if self.starts_quantifier():
            raise self.fail("量指定子が重なっている")
        return Quantifier(target, low, high, mode)

    def parse_atom(self) -> RegexNode:
        ch = self.peek()
        if ch == "(":
            return self.parse_group()
        if ch == "[":
            return self.parse_class()
        if ch == "\\":
            return self.parse_escape()
        self.pos += 1
        if ch == ".":
            return Atom(AtomKind.ANY, ch)
        if ch in ("^", "$"):
            return Atom(AtomKind.ANCHOR, ch)
        return Atom(AtomKind.CHAR, ch)

    def read_until(self, terminator: str, what: str) -> str:
        end = self.text.find(terminator, self.pos)
        if end < 0:
            raise self.fail(f"{what}が閉じていない")
        value = self.text[self.pos : end]
        self.pos = end + len(terminator)
        return value

    def read_name(self, terminator: str) -> str:
        start = self.pos
        name = self.read_until(terminator, "名前")
        if not _NAME.fullmatch(name):
            if "-" in name:
                raise self.fail("釣り合いの組は読めない", start)
            raise self.fail("組の名前が不正", start)
        return name

    def parse_body_at(self, open_at: int) -> RegexNode:
        self.depth += 1
        if self.depth > _MAX_GROUP_DEPTH:
            raise self.fail("括弧の入れ子が深すぎる", open_at)
        body = self.parse_alternation()
        self.depth -= 1
        if self.peek() != ")":
            raise self.fail("閉じていない括弧", open_at)
        self.pos += 1
        return body

    def parse_group(self) -> RegexNode:
        open_at = self.pos
        self.pos += 1
        if self.peek() != "?":
            return CaptureGroup(self.parse_body_at(open_at))
        self.pos += 1
        return self.parse_extended_group(open_at)

    def parse_extended_group(self, open_at: int) -> RegexNode:
        """(? の直後から、括弧の種類を決めて読む。"""
        two = self.peek(2)
        ch = self.peek()
        if ch in (":", ">", "=", "!"):
            return self.parse_marked_group(ch, open_at)
        if two in ("<=", "<!"):
            self.pos += 2
            return Look(ahead=False, negated=two == "<!", body=self.parse_body_at(open_at))
        if ch in ("<", "'") or two == "P<":
            return self.parse_named_group(ch, two, open_at)
        if two == "P=":
            self.pos += 2
            return Backreference(NamedReference(self.read_name(")")))
        if ch == "(":
            return self.parse_conditional(open_at)
        return self.parse_flags(open_at)

    def parse_marked_group(self, mark: str, open_at: int) -> RegexNode:
        """(?: (?> (?= (?! のように、? の次の一文字で種類が決まる括弧を読む。"""
        self.pos += 1
        body = self.parse_body_at(open_at)
        if mark == ":":
            return NonCaptureGroup(body)
        if mark == ">":
            return AtomicGroup(body)
        return Look(ahead=True, negated=mark == "!", body=body)

    def parse_named_group(self, ch: str, two: str, open_at: int) -> RegexNode:
        """(?<n> (?'n' (?P<n> のように、名前を持つ括弧を読む。"""
        if two == "P<":
            self.pos += 2
            closer = ">"
        else:
            self.pos += 1
            closer = ">" if ch == "<" else "'"
        name = self.read_name(closer)
        return NamedGroup(name, self.parse_body_at(open_at))

    def parse_conditional(self, open_at: int) -> RegexNode:
        self.pos += 1
        condition = self.read_until(")", "条件")
        if not condition:
            raise self.fail("条件が空")
        reference = _condition_reference(condition)
        if reference is None:
            raise self.fail("条件が番号でも名前でもない")
        body = self.parse_body_at(open_at)
        if isinstance(body, Alternation):
            if len(body.branches) > _MAX_CONDITIONAL_BRANCHES:
                raise self.fail("条件つきの組の選択肢が三つ以上", open_at)
            return Conditional(reference, body.branches[0], body.branches[1])
        return Conditional(reference, body, None)

    def parse_flags(self, open_at: int) -> RegexNode:
        allowed = _FLAG_LETTERS[self.dialect]
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] in allowed:
            self.pos += 1
        flags = self.text[start : self.pos]
        ch = self.peek()
        if ch == ")":
            self.pos += 1
            return LeadingFlags(_to_flags(flags))
        if ch == ":":
            self.pos += 1
            return FlaggedGroup(_to_flags(flags), self.parse_body_at(open_at))
        if self.pos >= len(self.text):
            raise self.fail("閉じていない括弧", open_at)
        raise self.fail(f"未知の書き方 (?{self.text[start : self.pos + 1]}", open_at)

    def parse_class(self) -> RegexNode:
        open_at = self.pos
        self.pos += 1
        negated = self.peek() == "^"
        if negated:
            self.pos += 1
        nested = self.dialect is not RegexDialect.PYTHON
        has_intersection = False
        first = True
        while True:
            if self.pos >= len(self.text):
                raise self.fail("閉じていない文字クラス", open_at)
            ch = self.peek()
            if ch == "]" and not first:
                self.pos += 1
                return CharClass(negated, has_intersection)
            first = False
            if ch == "\\":
                if self.pos + 1 >= len(self.text):
                    raise self.fail("末尾のバックスラッシュ")
                self.pos += 2
            elif ch == "[" and nested:
                inner = self.parse_class()
                if isinstance(inner, CharClass) and inner.has_intersection:
                    has_intersection = True
            elif self.dialect is RegexDialect.JAVA and self.peek(2) == "&&":
                has_intersection = True
                self.pos += 2
            else:
                self.pos += 1

    def parse_escape(self) -> RegexNode:
        start = self.pos
        if self.pos + 1 >= len(self.text):
            raise self.fail("末尾のバックスラッシュ")
        ch = self.text[self.pos + 1]
        self.pos += 2
        if ch in "123456789":
            return self.parse_numbered_reference(ch)
        if ch == "k" and self.peek() in ("<", "'", "{"):
            return self.parse_named_reference()
        if ch in "pP":
            return self.parse_property(start)
        if ch == "Q":
            return self.parse_quote(start)
        return self.parse_plain_escape(ch, start)

    def parse_numbered_reference(self, first_digit: str) -> RegexNode:
        """番号で組を指す後方参照を読む。番号は続く数字をすべて含める。"""
        digits = first_digit
        while self.peek().isdigit():
            digits += self.peek()
            self.pos += 1
        return Backreference(NumberedReference(int(digits)))

    def parse_named_reference(self) -> RegexNode:
        """名前で組を指す後方参照を読む。名前は <>、''、{} のどれかで囲まれている。"""
        closer = {"<": ">", "'": "'", "{": "}"}[self.peek()]
        self.pos += 1
        return Backreference(NamedReference(self.read_name(closer)))

    def parse_property(self, start: int) -> RegexNode:
        """文字のプロパティを読む。名前は {} で囲むか、一文字で書く。"""
        if self.peek() == "{":
            self.pos += 1
            self.read_until("}", "プロパティ")
        elif self.pos < len(self.text):
            self.pos += 1
        else:
            raise self.fail("プロパティの名前が無い", start)
        return Atom(AtomKind.PROPERTY, self.text[start : self.pos])

    def parse_quote(self, start: int) -> RegexNode:
        """引用の範囲を読む。終わりの印が無ければ末尾までを引用とする。"""
        if self.dialect is RegexDialect.PYTHON:
            raise self.fail("\\Q..\\E は Python では読めない", start)
        end = self.text.find("\\E", self.pos)
        if end < 0:
            quoted = self.text[self.pos :]
            self.pos = len(self.text)
        else:
            quoted = self.text[self.pos : end]
            self.pos = end + 2
        return Quote(quoted)

    def parse_plain_escape(self, ch: str, start: int) -> RegexNode:
        """既定の文字クラス、アンカー、それ以外の逃がしを読む。"""
        if ch in _DEFAULT_CLASSES:
            return Atom(AtomKind.CLASS, self.text[start : self.pos])
        if ch in _ANCHOR_ESCAPES:
            return Atom(AtomKind.ANCHOR, self.text[start : self.pos])
        self.skip_escape_payload(ch)
        return Atom(AtomKind.ESCAPE, self.text[start : self.pos])

    def skip_escape_payload(self, ch: str) -> None:
        """16 進、ユニコード、名前つきの逃がしで、文字の後ろに続く部分を読み飛ばす。"""
        if ch == "x" and self.peek() == "{":
            self.pos += 1
            self.read_until("}", "16 進の文字")
        elif ch in ("x", "u"):
            self.pos += min(2 if ch == "x" else 4, len(self.text) - self.pos)
        elif ch == "N" and self.peek() == "{":
            self.pos += 1
            self.read_until("}", "文字の名前")


def parse(pattern: str, dialect: RegexDialect) -> RegexNode | Unreadable:
    """正規表現の本文を木にする。読めなければ Unreadable を返す。"""
    parser = _Parser(pattern, dialect)
    try:
        return parser.parse_all()
    except _ParseError as failure:
        return Unreadable(failure.position, failure.reason)
    except RecursionError:
        # 入れ子の上限に掛からない経路で再帰が尽きても、読めない入力として扱う
        return Unreadable(parser.pos, "入れ子が深すぎて読めない")
