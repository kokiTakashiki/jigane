"""Java の正規表現の書き場所を、構文木だけから読む。実行も import もしない。"""

import re
from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import RegexComposed, RegexDialect, RegexLiteral, RegexScan, RegexUse
from jigane.languages.regex_locals import LocalDeclaration, LocalRules, local_value
from jigane.preconditions.required import required

_PATTERN_METHODS = frozenset({"compile", "matches"})
_STRING_METHODS = frozenset({"matches", "replaceAll", "replaceFirst", "split"})
_USES = {
    "Pattern.compile": RegexUse.COMPILE,
    "Pattern.matches": RegexUse.MATCH,
    "String.matches": RegexUse.MATCH,
    "String.replaceAll": RegexUse.REPLACE,
    "String.replaceFirst": RegexUse.REPLACE,
    "String.split": RegexUse.SPLIT,
}
_SIMPLE_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "b": "\b",
    "r": "\r",
    "f": "\f",
    "s": " ",
    '"': '"',
    "'": "'",
    "\\": "\\",
}
_ESCAPE = re.compile(r"\\(u+[0-9a-fA-F]{4}|[0-3]?[0-7]{1,2}|\n|.)", re.DOTALL)


def _text(node: Node) -> str:
    return required(node.text).decode()


def _walk(root: Node) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _unescape(raw: str) -> str:
    def replace(match: re.Match[str]) -> str:
        body = match.group(1)
        if body in _SIMPLE_ESCAPES:
            return _SIMPLE_ESCAPES[body]
        if body == "\n":
            return ""
        if body[0] == "u":
            return chr(int(body.lstrip("u"), 16))
        if body[0].isdigit():
            return chr(int(body, 8))
        return match.group(0)

    return _ESCAPE.sub(replace, raw)


def _text_block(raw: str) -> str:
    """`\"\"\"` で囲んだテキストブロックの値。付随する空白を除いてから、エスケープを解く。"""
    body = raw[3:-3]
    # 開きの区切りの行の残りは空白だけと決まっている
    body = body.split("\n", 1)[1] if "\n" in body else ""
    lines = body.split("\n")
    # 最後の行は閉じの区切りの行で、空白だけでも字下げの算出に加える
    significant = [line for line in lines[:-1] if line.strip()] + [lines[-1]]
    indent = min(len(line) - len(line.lstrip(" \t")) for line in significant)
    stripped = [line[indent:].rstrip(" \t") for line in lines]
    return _unescape("\n".join(stripped))


_READ_PARENTS = frozenset(
    {
        "argument_list",
        "binary_expression",
        "parenthesized_expression",
        "return_statement",
        "method_invocation",
        "field_access",
        "ternary_expression",
    }
)


def _declaration(identifier: Node) -> LocalDeclaration | None:
    declarator = identifier.parent
    if declarator is None or declarator.type != "variable_declarator":
        return None
    value = declarator.child_by_field_name("value")
    if value is None or declarator.child_by_field_name("name") != identifier:
        return None
    statement = declarator.parent
    if statement is None or statement.type != "local_variable_declaration":
        return None
    if sum(c.type == "variable_declarator" for c in statement.named_children) != 1:
        return None
    return LocalDeclaration(value=value, statement=statement)


def _body(scope: Node) -> Node | None:
    return scope.child_by_field_name("body")


def _is_read(identifier: Node) -> bool:
    parent = identifier.parent
    return parent is not None and parent.type in _READ_PARENTS


_LOCALS = LocalRules(
    scopes=frozenset({"method_declaration", "constructor_declaration"}),
    identifier="identifier",
    declaration=_declaration,
    top_level=_body,
    is_read=_is_read,
)


def _concatenation(node: Node, *, resolve: bool) -> str | None:
    operator = node.child_by_field_name("operator")
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if operator is None or _text(operator) != "+" or left is None or right is None:
        return None
    left_value = _constant(left, resolve=resolve)
    right_value = _constant(right, resolve=resolve)
    if left_value is None or right_value is None:
        return None
    return left_value + right_value


def _traced_constant(identifier: Node) -> str | None:
    value = local_value(identifier, _LOCALS)
    # 一段だけたどる。たどった先の識別子は解かない
    return _constant(value, resolve=False) if value is not None else None


def _constant(node: Node, *, resolve: bool = True) -> str | None:
    """文字列の定数、または定数どうしの連結の値。それ以外の式は None。

    `resolve` が真なら、局所の識別子を宣言の初期値へ一段だけたどる。
    """
    if node.type == "string_literal":
        raw = _text(node)
        return _text_block(raw) if raw.startswith('"""') else _unescape(raw[1:-1])
    if node.type == "parenthesized_expression" and node.named_child_count == 1:
        return _constant(node.named_children[0], resolve=resolve)
    if node.type == "binary_expression":
        return _concatenation(node, resolve=resolve)
    if node.type == "identifier" and resolve:
        return _traced_constant(node)
    return None


def _is_pattern_class(owner: Node) -> bool:
    if owner.type == "identifier":
        return _text(owner) == "Pattern"
    return owner.type == "field_access" and _text(owner).endswith(".regex.Pattern")


def _first_argument(invocation: Node) -> Node | None:
    arguments = invocation.child_by_field_name("arguments")
    if arguments is None:
        return None
    comments = ("line_comment", "block_comment")
    return next((c for c in arguments.named_children if c.type not in comments), None)


def regex_scan(root: Node, path: str) -> RegexScan:
    literals: list[RegexLiteral] = []
    composed: list[RegexComposed] = []
    for node in _walk(root):
        if node.type != "method_invocation":
            continue
        owner = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        argument = _first_argument(node)
        if owner is None or name_node is None or argument is None:
            continue
        name = _text(name_node)
        line = node.start_point.row + 1
        value = _constant(argument)
        if _is_pattern_class(owner) and name in _PATTERN_METHODS:
            call = f"Pattern.{name}"
            if value is None:
                composed.append(RegexComposed(call=call, use=_USES[call], path=path, line=line))
                continue
        elif name in _STRING_METHODS:
            call = f"String.{name}"
            # 受け手の型が分からないので、定数でない引数は正規表現かどうか決められない
            if value is None:
                continue
            # 一文字の split は他の型の split と区別できず、正規表現を使う意図も薄い
            if name == "split" and len(value) == 1:
                continue
        else:
            continue
        literals.append(
            RegexLiteral(
                pattern=value,
                dialect=RegexDialect.JAVA,
                call=call,
                use=_USES[call],
                path=path,
                line=line,
            )
        )
    return RegexScan(literals=tuple(literals), composed=tuple(composed))
