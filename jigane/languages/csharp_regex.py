import re
from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import RegexComposed, RegexDialect, RegexLiteral, RegexScan, RegexUse
from jigane.languages.regex_locals import LocalDeclaration, LocalRules, local_value
from jigane.preconditions.required import required

_DECLARATOR_CHILD_COUNT = 2  # 識別子と初期化子

_STATIC_METHODS = frozenset({"IsMatch", "Match", "Matches", "Replace", "Split"})
_USES = {
    "Regex": RegexUse.COMPILE,
    "GeneratedRegex": RegexUse.COMPILE,
    "Regex.IsMatch": RegexUse.MATCH,
    "Regex.Match": RegexUse.MATCH,
    "Regex.Matches": RegexUse.MATCH,
    "Regex.Replace": RegexUse.REPLACE,
    "Regex.Split": RegexUse.SPLIT,
}
_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "\\": "\\",
    '"': '"',
    "'": "'",
}
_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{1,4}|.)", re.DOTALL)


class _NotConstantError(Exception):
    """文字列の定数に解けない式。"""


def _text(node: Node) -> str:
    return required(node.text).decode()


_READ_PARENTS = frozenset(
    {
        "binary_expression",
        "parenthesized_expression",
        "interpolation",
        "return_statement",
        "member_access_expression",
        "conditional_expression",
    }
)


def _declaration(identifier: Node) -> LocalDeclaration | None:
    declarator = identifier.parent
    if declarator is None or declarator.type != "variable_declarator":
        return None
    if (
        declarator.named_children[0] != identifier
        or len(declarator.named_children) != _DECLARATOR_CHILD_COUNT
    ):
        return None
    declaration = declarator.parent
    if declaration is None or declaration.type != "variable_declaration":
        return None
    if sum(c.type == "variable_declarator" for c in declaration.named_children) != 1:
        return None
    statement = declaration.parent
    if statement is None or statement.type != "local_declaration_statement":
        return None
    return LocalDeclaration(value=declarator.named_children[1], statement=statement)


def _body(scope: Node) -> Node | None:
    body = scope.child_by_field_name("body")
    return body if body is not None and body.type == "block" else None


def _is_read(identifier: Node) -> bool:
    parent = identifier.parent
    if parent is None:
        return False
    if parent.type == "argument":
        # `ref` と `out` の引数は呼び先が書き換える
        return not any(c.text in {b"ref", b"out"} for c in parent.children if not c.is_named)
    return parent.type in _READ_PARENTS


_LOCALS = LocalRules(
    scopes=frozenset(
        {
            "method_declaration",
            "constructor_declaration",
            "local_function_statement",
            "accessor_declaration",
        }
    ),
    identifier="identifier",
    declaration=_declaration,
    top_level=_body,
    is_read=_is_read,
)


def regex_scan(root: Node, path: str) -> RegexScan:
    literals: list[RegexLiteral] = []
    composed: list[RegexComposed] = []
    for node in _walk(root):
        site = _site(node)
        if site is None:
            continue
        call, argument = site
        line = node.start_point[0] + 1
        try:
            pattern = _constant(argument)
        except _NotConstantError:
            composed.append(RegexComposed(call=call, use=_USES[call], path=path, line=line))
        else:
            literals.append(
                RegexLiteral(
                    pattern=pattern,
                    dialect=RegexDialect.DOTNET,
                    call=call,
                    use=_USES[call],
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


def _last_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "identifier":
        return _text(node)
    if node.type == "qualified_name":
        return _last_name(node.children[-1])
    return None


def _first_child_of(parent: Node | None, kind: str) -> Node | None:
    if parent is None:
        return None
    for child in parent.named_children:
        if child.type == kind:
            return child
    return None


def _static_pattern_argument(arguments: Node | None) -> Node | None:
    """静的なメソッドは `(input, pattern, ...)`。正規表現は `pattern:` か 2 番目の位置引数。"""
    if arguments is None:
        return None
    positional: list[Node] = []
    for argument in arguments.named_children:
        if argument.type != "argument":
            continue
        name = argument.child_by_field_name("name") or _first_child_of(argument, "name_colon")
        if name is None:
            positional.append(argument)
        elif _text(name).removesuffix(":").strip() == "pattern":
            return argument
    return positional[1] if len(positional) > 1 else None


def _creation_site(node: Node) -> tuple[str, Node] | None:
    if _last_name(node.child_by_field_name("type")) != "Regex":
        return None
    argument = _first_child_of(_first_child_of(node, "argument_list"), "argument")
    return ("Regex", argument) if argument is not None else None


def _invocation_site(node: Node) -> tuple[str, Node] | None:
    function = node.child_by_field_name("function")
    if function is None or function.type != "member_access_expression":
        return None
    method = _last_name(function.child_by_field_name("name"))
    if method not in _STATIC_METHODS:
        return None
    # 受け手が型名の `Regex` のときだけ静的な呼び出しと決められる。変数の受け手は拾わない
    if _last_name(function.child_by_field_name("expression")) != "Regex":
        return None
    argument = _static_pattern_argument(_first_child_of(node, "argument_list"))
    return (f"Regex.{method}", argument) if argument is not None else None


def _attribute_site(node: Node) -> tuple[str, Node] | None:
    if _last_name(node.child_by_field_name("name")) not in {
        "GeneratedRegex",
        "GeneratedRegexAttribute",
    }:
        return None
    argument = _first_child_of(
        _first_child_of(node, "attribute_argument_list"), "attribute_argument"
    )
    if argument is None or any(c.type in {"name_equals", "name_colon"} for c in argument.children):
        return None
    return ("GeneratedRegex", argument)


def _site(node: Node) -> tuple[str, Node] | None:
    if node.type == "object_creation_expression":
        return _creation_site(node)
    if node.type == "invocation_expression":
        return _invocation_site(node)
    if node.type == "attribute":
        return _attribute_site(node)
    return None


def _argument_constant(node: Node, *, resolve: bool) -> str:
    name = node.child_by_field_name("name")
    inner = [
        c for c in node.named_children if c.type not in {"name_colon", "name_equals"} and c != name
    ]
    if len(inner) != 1:
        raise _NotConstantError
    return _constant(inner[0], resolve=resolve)


def _concatenation(node: Node, *, resolve: bool) -> str:
    operator = node.child_by_field_name("operator")
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if operator is None or operator.text != b"+" or left is None or right is None:
        raise _NotConstantError
    return _constant(left, resolve=resolve) + _constant(right, resolve=resolve)


def _string_constant(node: Node) -> str:
    if node.type == "string_literal":
        return _ESCAPE.sub(_unescape, _text(node)[1:-1])
    if node.type == "verbatim_string_literal":
        return _text(node)[2:-1].replace('""', '"')
    return _raw(_text(node))


def _traced_constant(identifier: Node) -> str:
    value = local_value(identifier, _LOCALS)
    if value is None:
        raise _NotConstantError
    # 一段だけたどる。たどった先の識別子は解かない
    return _constant(value, resolve=False)


def _constant(node: Node, *, resolve: bool = True) -> str:
    if node.type in {"argument", "attribute_argument"}:
        return _argument_constant(node, resolve=resolve)
    if node.type == "parenthesized_expression":
        return _constant(node.named_children[0], resolve=resolve)
    if node.type == "binary_expression":
        return _concatenation(node, resolve=resolve)
    if node.type in {"string_literal", "verbatim_string_literal", "raw_string_literal"}:
        return _string_constant(node)
    if node.type == "identifier" and resolve:
        return _traced_constant(node)
    # 補間つきの文字列と、変数や呼び出しなどの式
    raise _NotConstantError


def _unescape(match: re.Match[str]) -> str:
    body = match.group(1)
    if body[0] in "uUx" and len(body) > 1:
        return chr(int(body[1:], 16))
    return _ESCAPES.get(body, match.group(0))


def _raw(text: str) -> str:
    quotes = len(text) - len(text.lstrip('"'))
    inner = text[quotes : len(text) - quotes]
    if "\n" not in inner:
        return inner
    # 複数行は、開きの直後の改行と、閉じの行の字下げを外す
    lines = inner.split("\n")
    indent = lines[-1]
    return "\n".join(line.removeprefix(indent) for line in lines[1:-1])
