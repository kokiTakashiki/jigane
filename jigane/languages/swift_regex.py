import re
from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import RegexComposed, RegexDialect, RegexLiteral, RegexScan, RegexUse
from jigane.languages.regex_locals import LocalDeclaration, LocalRules, local_value
from jigane.preconditions.required import required

_BINDING_PART_COUNT = 2  # パターンと初期値

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'", "\n": ""}


class _NotConstantError(Exception):
    """文字列の定数に解けない式。"""


def _text(node: Node) -> str:
    return required(node.text).decode()


def regex_scan(root: Node, path: str) -> RegexScan:
    literals: list[RegexLiteral] = []
    composed: list[RegexComposed] = []
    for node in _walk(root):
        line = node.start_point[0] + 1
        if node.type == "regex_literal":
            literals.append(
                RegexLiteral(
                    pattern=_regex_body(_text(node)),
                    dialect=RegexDialect.SWIFT,
                    call="/.../",
                    use=RegexUse.COMPILE,
                    path=path,
                    line=line,
                )
            )
            continue
        if node.type != "call_expression":
            continue
        site = _site(node)
        if site is None:
            continue
        call, argument = site
        try:
            pattern = _constant(argument)
        except _NotConstantError:
            composed.append(RegexComposed(call=call, use=RegexUse.COMPILE, path=path, line=line))
        else:
            literals.append(
                RegexLiteral(
                    pattern=pattern,
                    dialect=RegexDialect.SWIFT,
                    call=call,
                    use=RegexUse.COMPILE,
                    path=path,
                    line=line,
                )
            )
    return RegexScan(literals=tuple(literals), composed=tuple(composed))


def _walk(root: Node) -> Iterator[Node]:
    """文書順（先行順）に全節点を返す。"""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _regex_body(text: str) -> str:
    hashes = len(text) - len(text.lstrip("#"))
    return text[hashes + 1 : len(text) - hashes - 1]


def _label(argument: Node) -> str | None:
    for child in argument.children:
        if child.type == "value_argument_label":
            return _text(child)
    return None


def _callee_name(callee: Node) -> str | None:
    if callee.type == "simple_identifier":
        return _text(callee)
    if callee.type != "navigation_expression":
        return None
    # `Foundation.NSRegularExpression` のようなモジュール修飾は、最後の名前だけで判定する
    suffix = callee.children[-1]
    identifier = suffix.children[-1] if suffix.type == "navigation_suffix" else None
    if identifier is None or identifier.type != "simple_identifier":
        return None
    return _text(identifier)


def _argument_values(call: Node) -> list[Node] | None:
    """呼び出しの引数の節点を返す。末尾クロージャだけなど、引数の並びが無ければ None。"""
    suffix = next((c for c in call.children if c.type == "call_suffix"), None)
    if suffix is None:
        return None
    arguments = next((c for c in suffix.children if c.type == "value_arguments"), None)
    if arguments is None:
        return None
    return [c for c in arguments.children if c.type == "value_argument"]


def _site_name(call: Node) -> str | None:
    """正規表現を作る呼び出しなら、その呼び出し先の名前。それ以外は None。"""
    callee = call.children[0] if call.children else None
    if callee is None:
        return None
    name = _callee_name(callee)
    return name if name in {"Regex", "NSRegularExpression"} else None


def _site(call: Node) -> tuple[str, Node] | None:
    """正規表現を作る呼び出しの（表示名, 本文の引数）。本文の引数が決まらなければ None。"""
    name = _site_name(call)
    if name is None:
        return None
    values = _argument_values(call)
    if values is None:
        return None
    if name == "Regex":
        # ラベルつきの最初の引数（`Regex(verbatim:)` など）は正規表現の本文ではない
        if values and _label(values[0]) is None:
            return ("Regex(...)", values[0])
        return None
    for value in values:
        if _label(value) == "pattern":
            return ("NSRegularExpression", value)
    return None


_READ_PARENTS = frozenset(
    {
        "value_argument",
        "additive_expression",
        "tuple_expression",
        "interpolated_expression",
        "navigation_expression",
        "control_transfer_statement",
    }
)
_DECLARATION_PARTS = frozenset(
    {"value_binding_pattern", "modifiers", "type_annotation", "comment"}
)


def _declaration(identifier: Node) -> LocalDeclaration | None:
    pattern = identifier.parent
    if pattern is None or pattern.type != "pattern":
        return None
    statement = pattern.parent
    if statement is None or statement.type != "property_declaration":
        return None
    parts = [c for c in statement.named_children if c.type not in _DECLARATION_PARTS]
    # `let a = "x", b = "y"` のような複数の束縛や、初期値の無い宣言は外す
    if len(parts) != _BINDING_PART_COUNT or parts[0] != pattern:
        return None
    return LocalDeclaration(value=parts[1], statement=statement)


def _body(scope: Node) -> Node | None:
    body = scope.child_by_field_name("body")
    if body is None:
        body = next((c for c in scope.named_children if c.type == "function_body"), None)
    if body is None:
        return None
    return next((c for c in body.named_children if c.type == "statements"), None)


def _is_read(identifier: Node) -> bool:
    parent = identifier.parent
    return parent is not None and parent.type in _READ_PARENTS


_LOCALS = LocalRules(
    scopes=frozenset({"function_declaration", "init_declaration"}),
    identifier="simple_identifier",
    declaration=_declaration,
    top_level=_body,
    is_read=_is_read,
)


_STRING_LITERALS = frozenset(
    {"line_string_literal", "multi_line_string_literal", "raw_string_literal"}
)


def _argument_constant(node: Node, *, resolve: bool) -> str:
    """ラベルを除いた値が一つの引数から、その値の定数を解く。"""
    inner = [c for c in node.named_children if c.type != "value_argument_label"]
    if len(inner) != 1:
        raise _NotConstantError
    return _constant(inner[0], resolve=resolve)


def _sum_constant(node: Node, *, resolve: bool) -> str:
    """`+` で繋いだ両辺の定数を連結する。`+` 以外の演算は解かない。"""
    operator = node.child_by_field_name("op")
    left = node.child_by_field_name("lhs")
    right = node.child_by_field_name("rhs")
    if operator is None or operator.text != b"+" or left is None or right is None:
        raise _NotConstantError
    return _constant(left, resolve=resolve) + _constant(right, resolve=resolve)


def _string_constant(node: Node) -> str:
    """文字列リテラルの本文。補間を含むものは定数でないので解かない。"""
    if node.type == "raw_string_literal":
        return _raw(_text(node))
    if node.type == "multi_line_string_literal":
        # 不正なエスケープなどで文法が壊れた節点（ERROR）を含む文字列は、本文を信用せず解かない
        if node.has_error or any(c.type == "interpolated_expression" for c in node.children):
            raise _NotConstantError
        return _multiline(_text(node), 0)
    if any(c.type == "interpolated_expression" for c in node.children):
        raise _NotConstantError
    return _unescape(_text(node)[1:-1], "")


def _identifier_constant(node: Node) -> str:
    """局所の束縛へ一段たどって、その初期値の定数を解く。"""
    value = local_value(node, _LOCALS)
    if value is None:
        raise _NotConstantError
    # 一段だけたどる。たどった先の識別子は解かない
    return _constant(value, resolve=False)


def _constant(node: Node, *, resolve: bool = True) -> str:
    if node.type == "value_argument":
        return _argument_constant(node, resolve=resolve)
    if node.type == "tuple_expression" and len(node.named_children) == 1:
        return _constant(node.named_children[0], resolve=resolve)
    if node.type == "additive_expression":
        return _sum_constant(node, resolve=resolve)
    if node.type in _STRING_LITERALS:
        return _string_constant(node)
    if node.type == "simple_identifier" and resolve:
        return _identifier_constant(node)
    raise _NotConstantError


def _raw(text: str) -> str:
    hashes = len(text) - len(text.lstrip("#"))
    if text.startswith('"""', hashes):
        return _multiline(text, hashes)
    inner = text[hashes + 1 : len(text) - hashes - 1]
    if "\\" + "#" * hashes + "(" in inner:
        raise _NotConstantError
    return _unescape(inner, "#" * hashes)


def _multiline(text: str, hashes: int) -> str:
    """三連の二重引用符で区切った文字列を、閉じの行の字下げを外して解く。"""
    delimiter = "#" * hashes
    body = text[hashes + 3 : len(text) - hashes - 3].replace("\r\n", "\n")
    if "\\" + delimiter + "(" in body or not body.startswith("\n"):
        raise _NotConstantError
    lines = body[1:].split("\n")
    indent = lines.pop()
    if indent.strip():
        raise _NotConstantError
    stripped: list[str] = []
    for line in lines:
        if line.startswith(indent):
            stripped.append(line[len(indent) :])
        elif not line.strip():
            stripped.append("")
        else:
            raise _NotConstantError
    return _unescape("\n".join(stripped), delimiter)


def _unescape(text: str, hashes: str) -> str:
    # 拡張区切りの文字列では、エスケープは `\` の直後に区切りの `#` が並ぶ形だけが効く
    pattern = re.compile(re.escape("\\" + hashes) + r"(u\{[0-9a-fA-F]+\}|.)", re.DOTALL)

    def replace(match: re.Match[str]) -> str:
        body = match.group(1)
        if body.startswith("u{"):
            return chr(int(body[2:-1], 16))
        return _ESCAPES.get(body, match.group(0))

    return pattern.sub(replace, text)
