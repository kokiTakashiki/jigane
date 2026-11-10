"""Swift のファイルの名前の組み立てを、構文木だけから読む。実行も import もしない。"""

from tree_sitter import Node

from jigane.languages.base import FilePathPattern, FileUsage
from jigane.languages.file_paths_core import Rules, text
from jigane.languages.file_paths_core import file_path_patterns as core_file_path_patterns
from jigane.languages.swift_nodes import DECLARED_FUNCTION_NODES

_FILE_MANAGER = "FileManager.default"
# 名前を組み立てるメソッド。連なりは式全体を一つの組み立てとして取る
_APPENDING_METHODS = frozenset(
    {"appendingPathComponent", "appendingPathExtension", "appending", "appendingPathComponents"}
)
# `URL(...)` のうち、文字列からファイルや URL を作る初期化子の引数名
_URL_LABELS = frozenset({"fileURLWithPath", "filePath", "string"})
# `FileManager.default` のメソッドごとに、パスを渡す引数名と使い道。
# 移動は両端を書く、複製は元を読む
_FILE_MANAGER_CALLS: dict[str, tuple[tuple[str, FileUsage], ...]] = {
    "createFile": (("atPath", FileUsage.WRITE),),
    "contents": (("atPath", FileUsage.READ),),
    "fileExists": (("atPath", FileUsage.READ),),
    "removeItem": (("at", FileUsage.WRITE), ("atPath", FileUsage.WRITE)),
    "createDirectory": (("at", FileUsage.WRITE), ("atPath", FileUsage.WRITE)),
    "moveItem": (
        ("at", FileUsage.WRITE),
        ("atPath", FileUsage.WRITE),
        ("to", FileUsage.WRITE),
        ("toPath", FileUsage.WRITE),
    ),
    "copyItem": (
        ("at", FileUsage.READ),
        ("atPath", FileUsage.READ),
        ("to", FileUsage.WRITE),
        ("toPath", FileUsage.WRITE),
    ),
}
# 型の初期化子ごとに、パスを渡す引数名。読むだけ
_READING_INITIALIZERS = {
    "Data": frozenset({"contentsOf"}),
    "String": frozenset({"contentsOf", "contentsOfFile"}),
}
_WRITING_LABELS = frozenset({"to", "toFile"})
_CALL_NODES = frozenset({"call_expression"})
_SCOPE_BOUNDARIES = DECLARED_FUNCTION_NODES | {"class_declaration"}
_STRING_LITERALS = ("line_string_literal", "multi_line_string_literal")


def _builds_string(node: Node) -> bool:
    """補間を持つ文字列か、文字列を含む `+` の連結か。"""
    if node.type in _STRING_LITERALS:
        return any(c.type == "interpolated_expression" for c in node.children)
    if node.type == "additive_expression":
        operator = node.child_by_field_name("op")
        sides = (node.child_by_field_name("lhs"), node.child_by_field_name("rhs"))
        return (
            operator is not None
            and text(operator) == "+"
            and any(s is not None and s.type in _STRING_LITERALS for s in sides)
        )
    return False


class _Call:
    """`呼び出し先(引数...)` の呼び出し先の名前、受け手の原文、ラベル付きの引数。"""

    def __init__(self, node: Node) -> None:
        self.node = node
        self.name = ""
        self.receiver: Node | None = None
        self.arguments: dict[str, Node] = {}
        callee = node.children[0] if node.children else None
        suffix = next((c for c in node.children if c.type == "call_suffix"), None)
        if callee is None or suffix is None:
            return
        if callee.type == "simple_identifier":
            self.name = text(callee)
        elif callee.type == "navigation_expression":
            member = callee.child_by_field_name("suffix")
            member_name = member.child_by_field_name("suffix") if member is not None else None
            if member_name is not None:
                self.name = text(member_name)
            self.receiver = callee.child_by_field_name("target")
        for argument in (
            c for v in suffix.children if v.type == "value_arguments" for c in v.children
        ):
            if argument.type != "value_argument":
                continue
            label = argument.child_by_field_name("name")
            value = argument.child_by_field_name("value")
            # ラベルの無い引数は、どのメソッドも位置で読まないので捨てる
            if label is not None and value is not None:
                self.arguments[text(label)] = value


def _initializer(call: _Call) -> list[tuple[Node, FileUsage]]:
    for label in _READING_INITIALIZERS.get(call.name, ()):
        if label in call.arguments:
            return [(call.arguments[label], FileUsage.READ)]
    return []


def _write_call(call: _Call) -> list[tuple[Node, FileUsage]]:
    for label in _WRITING_LABELS:
        argument = call.arguments.get(label)
        # `to: &stream` は文字列の出力先であり、ファイルの名前ではない
        if argument is not None and argument.type != "prefix_expression":
            return [(argument, FileUsage.WRITE)]
    # `to:` を持たない `write` は、他の型のメソッドであり、ファイルの名前を取らない。
    return []


def _file_manager_call(call: _Call) -> list[tuple[Node, FileUsage]]:
    if call.name == "urls":
        # ディレクトリの問い合わせ。引数はパスでなく場所の列挙なので、呼び出し全体を取る
        return [(call.node, FileUsage.READ)]
    return [
        (call.arguments[label], usage)
        for label, usage in _FILE_MANAGER_CALLS.get(call.name, ())
        if label in call.arguments
    ]


def _io_arguments(node: Node) -> list[tuple[Node, FileUsage]]:
    call = _Call(node)
    if call.receiver is None:
        return _initializer(call)
    if text(call.receiver) == _FILE_MANAGER:
        return _file_manager_call(call)
    if call.name == "write":
        return _write_call(call)
    # 上のどれでもない呼び出しは、ファイルを開かない。名前の式は後段の走査が拾う。
    return []


def _is_name_building(node: Node) -> bool:
    call = _Call(node)
    if call.receiver is None:
        return call.name == "URL" and bool(_URL_LABELS & call.arguments.keys())
    return call.name in _APPENDING_METHODS


def _binding_of(node: Node) -> tuple[str, Node] | None:
    if node.type != "property_declaration":
        return None
    bound = node.child_by_field_name("name")
    identifier = bound.child_by_field_name("bound_identifier") if bound else None
    value = node.child_by_field_name("value")
    if identifier is None or value is None:
        return None
    return (text(identifier), value)


RULES = Rules(
    call_nodes=_CALL_NODES,
    io_arguments=_io_arguments,
    is_name_building=_is_name_building,
    builds_string=_builds_string,
    named_scopes=_SCOPE_BOUNDARIES,
    scope_boundaries=_SCOPE_BOUNDARIES,
    binding_scopes=_SCOPE_BOUNDARIES,
    identifier_type="simple_identifier",
    binding_of=_binding_of,
)


def file_path_patterns(root: Node, path: str) -> list[FilePathPattern]:
    return core_file_path_patterns(root, path, RULES)
