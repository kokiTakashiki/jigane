"""Swift のファイルの名前の組み立てを、構文木だけから読む。実行も import もしない。"""

from tree_sitter import Node

from jigane.languages.base import FilePathPattern, FileUsage
from jigane.preconditions.required import required

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
_SCOPE_BOUNDARIES = ("function_declaration", "init_declaration", "class_declaration")
_STRING_LITERALS = ("line_string_literal", "multi_line_string_literal")


def _text(node: Node) -> str:
    return " ".join(required(node.text).decode().split())


def _qualified_name(node: Node) -> str:
    names: list[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type in _SCOPE_BOUNDARIES:
            name = parent.child_by_field_name("name")
            if name is not None:
                names.append(_text(name))
        parent = parent.parent
    return ".".join(reversed(names))


def _span(node: Node) -> tuple[int, int]:
    return (node.start_byte, node.end_byte)


def _contains(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _is_string_building(node: Node) -> bool:
    """補間を持つ文字列か、文字列を含む `+` の連結を部分に持つか。"""
    if node.type in _STRING_LITERALS and any(
        c.type == "interpolated_expression" for c in node.children
    ):
        return True
    if node.type == "additive_expression":
        operator = node.child_by_field_name("op")
        sides = (node.child_by_field_name("lhs"), node.child_by_field_name("rhs"))
        if (
            operator is not None
            and _text(operator) == "+"
            and any(s is not None and s.type in _STRING_LITERALS for s in sides)
        ):
            return True
    return any(_is_string_building(child) for child in node.children)


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
            self.name = _text(callee)
        elif callee.type == "navigation_expression":
            member = callee.child_by_field_name("suffix")
            member_name = member.child_by_field_name("suffix") if member is not None else None
            if member_name is not None:
                self.name = _text(member_name)
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
                self.arguments[_text(label)] = value


class _Reader:
    def __init__(self, root: Node, path: str) -> None:
        self._root = root
        self._path = path
        self._found: list[tuple[int, FilePathPattern]] = []
        # 報告済みの式の範囲。読み書きの呼び出しの引数を、組み立てとして二重に報告しない
        self._reported: list[tuple[int, int]] = []

    def read(self) -> list[FilePathPattern]:
        calls = [_Call(n) for n in self._all_nodes() if n.type == "call_expression"]
        for call in calls:
            self._io_call(call)
        for call in calls:
            if self._is_name_building(call):
                self._name_building(call)
        return [pattern for _, pattern in sorted(self._found, key=lambda item: item[0])]

    def _all_nodes(self) -> list[Node]:
        found: list[Node] = []
        stack = [self._root]
        while stack:
            node = stack.pop()
            found.append(node)
            stack.extend(reversed(node.children))
        return found

    def _add(self, anchor: Node, expression: Node, usage: FileUsage, covered: Node) -> None:
        self._reported.append(_span(covered))
        self._found.append(
            (
                anchor.start_byte,
                FilePathPattern(
                    function=_qualified_name(anchor),
                    expression=_text(expression),
                    usage=usage,
                    path=self._path,
                    line=anchor.start_point[0] + 1,
                ),
            )
        )

    def _add_traced(self, call: Node, argument: Node, usage: FileUsage) -> None:
        resolved = (
            self._previous_binding(argument) if argument.type == "simple_identifier" else None
        )
        if resolved is not None:
            # 初期値の側の組み立ては、この行が代わりに表す
            self._reported.append(_span(resolved))
        self._add(call, resolved or argument, usage, argument)

    def _io_call(self, call: _Call) -> None:
        if any(_contains(done, _span(call.node)) for done in self._reported):
            return
        if call.receiver is None:
            self._initializer(call)
        elif _text(call.receiver) == _FILE_MANAGER:
            self._file_manager_call(call)
        elif call.name == "write":
            self._write_call(call)
        # 上のどれでもない呼び出しは、ファイルを開かない。名前の式は後段の走査が拾う。

    def _initializer(self, call: _Call) -> None:
        for label in _READING_INITIALIZERS.get(call.name, ()):
            if label in call.arguments:
                self._add_traced(call.node, call.arguments[label], FileUsage.READ)
                return

    def _write_call(self, call: _Call) -> None:
        for label in _WRITING_LABELS:
            argument = call.arguments.get(label)
            # `to: &stream` は文字列の出力先であり、ファイルの名前ではない
            if argument is not None and argument.type != "prefix_expression":
                self._add_traced(call.node, argument, FileUsage.WRITE)
                return
        # `to:` を持たない `write` は、他の型のメソッドであり、ファイルの名前を取らない。

    def _file_manager_call(self, call: _Call) -> None:
        if call.name == "urls":
            # ディレクトリの問い合わせ。引数はパスでなく場所の列挙なので、呼び出し全体を取る
            self._add(call.node, call.node, FileUsage.READ, call.node)
            return
        for label, usage in _FILE_MANAGER_CALLS.get(call.name, ()):
            if label in call.arguments:
                self._add_traced(call.node, call.arguments[label], usage)

    def _is_name_building(self, call: _Call) -> bool:
        if call.receiver is None:
            return call.name == "URL" and bool(_URL_LABELS & call.arguments.keys())
        return call.name in _APPENDING_METHODS

    def _name_building(self, call: _Call) -> None:
        span = _span(call.node)
        if any(_contains(done, span) for done in self._reported):
            return
        if not _is_string_building(call.node):
            return
        parent = call.node.parent
        while parent is not None and parent.type not in _SCOPE_BOUNDARIES:
            if (
                parent.type == "call_expression"
                and self._is_name_building(_Call(parent))
                and _is_string_building(parent)
            ):
                # 外側が連なりなら、外側の式だけを報告する
                return
            parent = parent.parent
        self._add(call.node, call.node, FileUsage.UNKNOWN, call.node)

    def _previous_binding(self, variable: Node) -> Node | None:
        """同じ関数（無ければファイル）の中で、使う位置より前のその名前の初期値。"""
        name = _text(variable)
        best: Node | None = None
        stack = [self._enclosing_scope(variable)]
        while stack:
            node = stack.pop()
            stack.extend(c for c in node.children if c.type not in _SCOPE_BOUNDARIES)
            if node.type != "property_declaration" or node.end_byte > variable.start_byte:
                continue
            bound = node.child_by_field_name("name")
            identifier = bound.child_by_field_name("bound_identifier") if bound else None
            if identifier is None or _text(identifier) != name:
                continue
            if node.child_by_field_name("value") is None:
                continue
            if best is None or node.start_byte > best.start_byte:
                best = node
        return best.child_by_field_name("value") if best is not None else None

    def _enclosing_scope(self, node: Node) -> Node:
        parent = node.parent
        while parent is not None and parent.type not in _SCOPE_BOUNDARIES:
            parent = parent.parent
        return parent or self._root


def file_path_patterns(root: Node, path: str) -> list[FilePathPattern]:
    return _Reader(root, path).read()
