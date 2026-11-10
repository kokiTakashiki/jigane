"""Python の正規表現の書き場所を、構文木だけから読む。実行も import もしない。"""

import ast
from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import RegexComposed, RegexDialect, RegexLiteral, RegexScan, RegexUse
from jigane.languages.node_text import text_of
from jigane.languages.python_walk import call_arguments
from jigane.languages.regex_locals import LocalDeclaration, LocalRules, local_value

_REGEX_FUNCTIONS = frozenset(
    {"compile", "match", "search", "fullmatch", "sub", "subn", "split", "findall", "finditer"}
)
_USES = {
    "compile": RegexUse.COMPILE,
    "match": RegexUse.MATCH,
    "search": RegexUse.MATCH,
    "fullmatch": RegexUse.MATCH,
    "findall": RegexUse.MATCH,
    "finditer": RegexUse.MATCH,
    "sub": RegexUse.REPLACE,
    "subn": RegexUse.REPLACE,
    "split": RegexUse.SPLIT,
}


def _walk(root: Node) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _import_binding(name: Node) -> tuple[str, str] | None:
    """`import` の一つの名前から、束縛される名前とモジュール名の組を返す。"""
    if name.type != "aliased_import":
        module = text_of(name).split(".", 1)[0]
        return module, module
    target = name.child_by_field_name("name")
    alias = name.child_by_field_name("alias")
    if target is None or alias is None:
        return None
    return text_of(alias), text_of(target)


def _from_import_functions(node: Node) -> dict[str, str]:
    """`from re import` で束縛された名前から関数名への対応。`re` 以外のモジュールは空。"""
    module_node = node.child_by_field_name("module_name")
    if module_node is None or text_of(module_node) != "re":
        return {}
    functions: dict[str, str] = {}
    for name in node.children_by_field_name("name"):
        is_aliased = name.type == "aliased_import"
        target = name.child_by_field_name("name") if is_aliased else name
        alias = name.child_by_field_name("alias") if is_aliased else None
        if target is None:
            continue
        imported = text_of(target)
        functions[text_of(alias) if alias is not None else imported] = imported
    return functions


def _bindings(root: Node) -> tuple[set[str], dict[str, str]]:
    """`re` を指す名前の集合と、`from re import` で束縛された名前から関数名への対応。"""
    # import が無くても `re` の綴りは拾う。別のモジュールを `re` の名前に束縛した import だけが外す
    modules = {"re"}
    functions: dict[str, str] = {}
    for node in _walk(root):
        if node.type == "import_statement":
            for name in node.children_by_field_name("name"):
                binding = _import_binding(name)
                if binding is None:
                    continue
                bound, module = binding
                if module == "re":
                    modules.add(bound)
                else:
                    modules.discard(bound)
        elif node.type == "import_from_statement":
            functions.update(_from_import_functions(node))
    return modules, functions


_READ_PARENTS = frozenset(
    {
        "argument_list",
        "binary_operator",
        "comparison_operator",
        "parenthesized_expression",
        "return_statement",
        "attribute",
    }
)


def _declaration(identifier: Node) -> LocalDeclaration | None:
    assignment = identifier.parent
    if assignment is None or assignment.type != "assignment":
        return None
    value = assignment.child_by_field_name("right")
    if value is None or assignment.child_by_field_name("left") != identifier:
        return None
    parent = assignment.parent
    # `a = b = "x"` のような連鎖は、右辺が代入になるのでここで外れる
    if parent is not None and parent.type == "expression_statement":
        return LocalDeclaration(value=value, statement=parent)
    return LocalDeclaration(value=value, statement=assignment)


def _body(scope: Node) -> Node | None:
    return scope.child_by_field_name("body")


def _is_read(identifier: Node) -> bool:
    parent = identifier.parent
    if parent is None:
        return False
    if parent.type == "keyword_argument":
        return parent.child_by_field_name("value") == identifier
    return parent.type in _READ_PARENTS


_LOCALS = LocalRules(
    scopes=frozenset({"function_definition"}),
    identifier="identifier",
    declaration=_declaration,
    top_level=_body,
    is_read=_is_read,
)


def _string_constant(node: Node) -> str | None:
    """`string` 節点の値。補間を含む、または文字列に解けないものは None。"""
    if any(child.type == "interpolation" for child in node.children):
        return None
    try:
        value = ast.literal_eval(text_of(node))
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return value if isinstance(value, str) else None


def _concatenated_constant(node: Node, *, resolve: bool) -> str | None:
    """隣り合う文字列の連結の値。一つでも定数でなければ None。"""
    parts: list[str] = []
    for child in node.named_children:
        if child.type == "comment":
            continue
        part = _constant(child, resolve=resolve)
        if part is None:
            return None
        parts.append(part)
    return "".join(parts)


def _sum_constant(node: Node, *, resolve: bool) -> str | None:
    """`+` で繋いだ二項演算の値。`+` 以外の演算子や定数でない項は None。"""
    operator = node.child_by_field_name("operator")
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if operator is None or text_of(operator) != "+" or left is None or right is None:
        return None
    left_value = _constant(left, resolve=resolve)
    right_value = _constant(right, resolve=resolve)
    if left_value is None or right_value is None:
        return None
    return left_value + right_value


def _constant(node: Node, *, resolve: bool = True) -> str | None:
    """文字列の定数、または定数どうしの連結の値。それ以外の式は None。

    `resolve` が真なら、局所の名前を代入の右辺へ一段だけたどる。
    """
    if node.type == "string":
        return _string_constant(node)
    if node.type == "concatenated_string":
        return _concatenated_constant(node, resolve=resolve)
    if node.type == "parenthesized_expression" and node.named_child_count == 1:
        return _constant(node.named_children[0], resolve=resolve)
    if node.type == "binary_operator":
        return _sum_constant(node, resolve=resolve)
    if node.type == "identifier" and resolve:
        value = local_value(node, _LOCALS)
        # 一段だけたどる。たどった先の名前は解かない
        return _constant(value, resolve=False) if value is not None else None
    return None


def _function_name(call: Node, modules: set[str], functions: dict[str, str]) -> str | None:
    function = call.child_by_field_name("function")
    if function is None:
        return None
    if function.type == "identifier":
        name = functions.get(text_of(function))
        return name if name in _REGEX_FUNCTIONS else None
    if function.type == "attribute":
        owner = function.child_by_field_name("object")
        attribute = function.child_by_field_name("attribute")
        if (
            owner is not None
            and attribute is not None
            and owner.type == "identifier"
            and text_of(owner) in modules
            and text_of(attribute) in _REGEX_FUNCTIONS
        ):
            return text_of(attribute)
    return None


def regex_scan(root: Node, path: str) -> RegexScan:
    modules, functions = _bindings(root)
    literals: list[RegexLiteral] = []
    composed: list[RegexComposed] = []
    for node in _walk(root):
        if node.type != "call":
            continue
        name = _function_name(node, modules, functions)
        if name is None:
            continue
        positional, keywords = call_arguments(node)
        argument = positional[0] if positional else keywords.get("pattern")
        if argument is None:
            continue
        call = f"re.{name}"
        use = _USES[name]
        line = node.start_point.row + 1
        value = _constant(argument)
        if value is None:
            composed.append(RegexComposed(call=call, use=use, path=path, line=line))
        else:
            literals.append(
                RegexLiteral(
                    pattern=value,
                    dialect=RegexDialect.PYTHON,
                    call=call,
                    use=use,
                    path=path,
                    line=line,
                )
            )
    return RegexScan(literals=tuple(literals), composed=tuple(composed))
