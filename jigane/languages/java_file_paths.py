"""Java のファイルの名前の組み立てを、構文木だけから読む。実行も import もしない。

受け手は名前だけで照合する（`Files` と `java.nio.file.Files` は同じ）。
読み書きの呼び出しは引数の形を問わず拾い、名前を組むだけの式は、文字列の `+`、
`String.format(...)`、`"...".formatted(...)` を含むときだけ拾う。
"""

from tree_sitter import Node

from jigane.languages.base import FilePathPattern, FileUsage
from jigane.languages.file_paths_core import Rules, text
from jigane.languages.file_paths_core import file_path_patterns as core_file_path_patterns
from jigane.languages.java_nodes import METHOD_NODES, TYPE_NODES

_FILES = "Files"
# `Files` のメソッドごとに、パスを渡す引数の位置と使い道。移動は両端を書く、複製は元を読む
_FILES_CALLS: dict[str, tuple[tuple[int, FileUsage], ...]] = {
    "readString": ((0, FileUsage.READ),),
    "readAllLines": ((0, FileUsage.READ),),
    "readAllBytes": ((0, FileUsage.READ),),
    "lines": ((0, FileUsage.READ),),
    "newBufferedReader": ((0, FileUsage.READ),),
    "newInputStream": ((0, FileUsage.READ),),
    "exists": ((0, FileUsage.READ),),
    "writeString": ((0, FileUsage.WRITE),),
    "write": ((0, FileUsage.WRITE),),
    "newBufferedWriter": ((0, FileUsage.WRITE),),
    "newOutputStream": ((0, FileUsage.WRITE),),
    "createFile": ((0, FileUsage.WRITE),),
    "createDirectory": ((0, FileUsage.WRITE),),
    "createDirectories": ((0, FileUsage.WRITE),),
    "delete": ((0, FileUsage.WRITE),),
    "deleteIfExists": ((0, FileUsage.WRITE),),
    "move": ((0, FileUsage.WRITE), (1, FileUsage.WRITE)),
    "copy": ((0, FileUsage.READ), (1, FileUsage.WRITE)),
}
_CONSTRUCTOR_USAGES = {
    "FileReader": FileUsage.READ,
    "FileInputStream": FileUsage.READ,
    "FileWriter": FileUsage.WRITE,
    "FileOutputStream": FileUsage.WRITE,
}
_SCOPE_BOUNDARIES = METHOD_NODES | TYPE_NODES
_CALL_NODES = frozenset({"method_invocation", "object_creation_expression"})


def _simple_name(node: Node | None) -> str:
    """`Files` も `java.nio.file.Files` も、最後の名前だけを返す。名前でない式は空。"""
    if node is None:
        return ""
    if node.type in ("identifier", "type_identifier"):
        return text(node)
    if node.type == "field_access":
        field = node.child_by_field_name("field")
        return text(field) if field is not None else ""
    if node.type == "scoped_type_identifier":
        names = [c for c in node.children if c.type == "type_identifier"]
        return text(names[-1]) if names else ""
    return ""


def _is_formatting(node: Node) -> bool:
    if node.type != "method_invocation":
        return False
    name = _simple_name(node.child_by_field_name("name"))
    if name == "formatted":
        return True
    return name == "format" and _simple_name(node.child_by_field_name("object")) == "String"


def _builds_string(node: Node) -> bool:
    """文字列を含む `+` か、format と formatted の呼び出しか。"""
    if _is_formatting(node):
        return True
    if node.type == "binary_expression":
        operator = node.child_by_field_name("operator")
        sides = (node.child_by_field_name("left"), node.child_by_field_name("right"))
        return (
            operator is not None
            and text(operator) == "+"
            and any(s is not None and s.type == "string_literal" for s in sides)
        )
    return False


def _arguments(call: Node) -> list[Node]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [c for c in arguments.named_children if c.type not in ("line_comment", "block_comment")]


def _is_name_building(call: Node) -> bool:
    if call.type == "object_creation_expression":
        return _simple_name(call.child_by_field_name("type")) == "File"
    name = _simple_name(call.child_by_field_name("name"))
    if name == "resolve":
        return True
    receiver = _simple_name(call.child_by_field_name("object"))
    return (receiver, name) in {("Path", "of"), ("Paths", "get")}


def _io_arguments(call: Node) -> list[tuple[Node, FileUsage]]:
    arguments = _arguments(call)
    if call.type == "object_creation_expression":
        usage = _CONSTRUCTOR_USAGES.get(_simple_name(call.child_by_field_name("type")))
        return [(arguments[0], usage)] if usage is not None and arguments else []
    if _simple_name(call.child_by_field_name("object")) != _FILES:
        # `Files` 以外の受け手は、ファイルを開かない。名前の式は後段の走査が拾う。
        return []
    name = _simple_name(call.child_by_field_name("name"))
    return [
        (arguments[index], usage)
        for index, usage in _FILES_CALLS.get(name, ())
        if index < len(arguments)
    ]


def _binding_of(node: Node) -> tuple[str, Node] | None:
    if node.type != "variable_declarator":
        return None
    bound = node.child_by_field_name("name")
    value = node.child_by_field_name("value")
    if bound is None or value is None:
        return None
    return (text(bound), value)


_RULES = Rules(
    call_nodes=_CALL_NODES,
    io_arguments=_io_arguments,
    is_name_building=_is_name_building,
    builds_string=_builds_string,
    named_scopes=_SCOPE_BOUNDARIES,
    scope_boundaries=_SCOPE_BOUNDARIES,
    binding_scopes=METHOD_NODES,
    identifier_type="identifier",
    binding_of=_binding_of,
)


def file_path_patterns(root: Node, path: str) -> list[FilePathPattern]:
    return core_file_path_patterns(root, path, _RULES)
