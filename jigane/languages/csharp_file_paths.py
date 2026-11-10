"""C# のファイルの名前の組み立てを、構文木だけから読む。実行も import もしない。

受け手は名前だけで照合する（`File` と `System.IO.File` は同じ）。
読み書きの呼び出しは引数の形を問わず拾い、名前を組むだけの式（`Path.Combine`、`Path.Join`）は、
補間文字列か文字列の `+` を含むときだけ拾う。
"""

from tree_sitter import Node

from jigane.languages.base import FilePathPattern, FileUsage
from jigane.languages.csharp_nodes import CALLABLE_BODIES, STRING_LITERALS, TYPE_KINDS
from jigane.languages.file_paths_core import Rules, text
from jigane.languages.file_paths_core import file_path_patterns as core_file_path_patterns

_READ = ((0, FileUsage.READ),)
_WRITE = ((0, FileUsage.WRITE),)
_MOVE = ((0, FileUsage.WRITE), (1, FileUsage.WRITE))
# 非同期版（`Async` で終わる名前）があるメソッド。無いメソッドの `Async` は拾わない
_FILE_ASYNC = {
    "ReadAllText": _READ,
    "ReadAllLines": _READ,
    "ReadAllBytes": _READ,
    "ReadLines": _READ,
    "WriteAllText": _WRITE,
    "WriteAllLines": _WRITE,
    "WriteAllBytes": _WRITE,
    "AppendAllText": _WRITE,
    "AppendAllLines": _WRITE,
}
_FILE_CALLS: dict[str, tuple[tuple[int, FileUsage], ...]] = {
    **_FILE_ASYNC,
    **{f"{name}Async": usages for name, usages in _FILE_ASYNC.items()},
    "OpenRead": _READ,
    "OpenText": _READ,
    "Exists": _READ,
    "Create": _WRITE,
    "CreateText": _WRITE,
    "AppendText": _WRITE,
    "OpenWrite": _WRITE,
    "Delete": _WRITE,
    "Move": _MOVE,
    # 複製は元を読み、先を書く
    "Copy": ((0, FileUsage.READ), (1, FileUsage.WRITE)),
}
_DIRECTORY_CALLS: dict[str, tuple[tuple[int, FileUsage], ...]] = {
    "Exists": _READ,
    "CreateDirectory": _WRITE,
    "Delete": _WRITE,
    "Move": _MOVE,
}
_STATIC_CALLS = {"File": _FILE_CALLS, "Directory": _DIRECTORY_CALLS}
# `FileStream` は第二引数の指定で読み書きが決まるので拾わない
_CONSTRUCTOR_USAGES = {"StreamReader": FileUsage.READ, "StreamWriter": FileUsage.WRITE}
_NAME_BUILDING_METHODS = frozenset({"Combine", "Join"})
_COMMENTS = frozenset({"comment"})
_CALL_NODES = frozenset({"invocation_expression", "object_creation_expression"})
# 変数の初期値を探すとき、中へ入らない境界。局所関数は囲むメソッドに含めるので除き、
# デストラクタを足す
_SCOPE_BOUNDARIES = (
    frozenset(TYPE_KINDS)
    | (CALLABLE_BODIES - {"local_function_statement"})
    | {"destructor_declaration"}
)
# 型より内側の名前として連ねる宣言。局所関数とラムダは囲むメソッドに含める
_NAMED_SCOPES = _SCOPE_BOUNDARIES | {"property_declaration"}
_BINDING_SCOPES = _SCOPE_BOUNDARIES - frozenset(TYPE_KINDS)


def _simple_name(node: Node | None) -> str:
    """`File` も `System.IO.File` も、最後の名前だけを返す。名前でない式は空。"""
    if node is None:
        return ""
    if node.type == "identifier":
        return text(node)
    if node.type in ("member_access_expression", "qualified_name", "alias_qualified_name"):
        return _simple_name(node.child_by_field_name("name"))
    return ""


def _builds_string(node: Node) -> bool:
    """補間文字列か、文字列を含む `+` か。"""
    if node.type == "interpolated_string_expression":
        return True
    if node.type == "binary_expression":
        operator = node.child_by_field_name("operator")
        sides = (node.child_by_field_name("left"), node.child_by_field_name("right"))
        return (
            operator is not None
            and text(operator) == "+"
            and any(s is not None and s.type in STRING_LITERALS for s in sides)
        )
    return False


def _arguments(call: Node) -> list[Node]:
    """引数の式を位置の順に返す。`name: value` の名前は外す。"""
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    found: list[Node] = []
    for argument in arguments.named_children:
        if argument.type != "argument":
            continue
        values = [c for c in argument.named_children if c.type not in _COMMENTS]
        if values:
            found.append(values[-1])
    return found


def _is_name_building(call: Node) -> bool:
    if call.type != "invocation_expression":
        return False
    function = call.child_by_field_name("function")
    if function is None or function.type != "member_access_expression":
        return False
    return (
        _simple_name(function.child_by_field_name("expression")) == "Path"
        and _simple_name(function) in _NAME_BUILDING_METHODS
    )


def _io_arguments(call: Node) -> list[tuple[Node, FileUsage]]:
    arguments = _arguments(call)
    if call.type == "object_creation_expression":
        usage = _CONSTRUCTOR_USAGES.get(_simple_name(call.child_by_field_name("type")))
        return [(arguments[0], usage)] if usage is not None and arguments else []
    function = call.child_by_field_name("function")
    if function is None or function.type != "member_access_expression":
        # 受け手の無い呼び出しは、`File` も `Directory` も指さない
        return []
    table = _STATIC_CALLS.get(_simple_name(function.child_by_field_name("expression")))
    if table is None:
        # `File` と `Directory` 以外の受け手は開かない。名前の式は後段の走査が拾う。
        return []
    return [
        (arguments[index], usage)
        for index, usage in table.get(_simple_name(function), ())
        if index < len(arguments)
    ]


def _initializer(declarator: Node) -> Node | None:
    children = declarator.children
    for index, child in enumerate(children):
        if child.type == "=":
            rest = [c for c in children[index + 1 :] if c.is_named and c.type not in _COMMENTS]
            return rest[0] if rest else None
    return None


def _binding_of(node: Node) -> tuple[str, Node] | None:
    if node.type != "variable_declarator":
        return None
    bound = node.child_by_field_name("name")
    value = _initializer(node)
    if bound is None or value is None:
        return None
    return (text(bound), value)


_RULES = Rules(
    call_nodes=_CALL_NODES,
    io_arguments=_io_arguments,
    is_name_building=_is_name_building,
    builds_string=_builds_string,
    named_scopes=_NAMED_SCOPES,
    scope_boundaries=_SCOPE_BOUNDARIES,
    binding_scopes=_BINDING_SCOPES,
    identifier_type="identifier",
    binding_of=_binding_of,
)


def file_path_patterns(root: Node, path: str) -> list[FilePathPattern]:
    return core_file_path_patterns(root, path, _RULES)
