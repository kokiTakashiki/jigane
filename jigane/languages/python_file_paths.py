"""ファイルの名前の組み立てを、構文木だけから読む。実行も import もしない。"""

import ast
import re

from tree_sitter import Node

from jigane.languages.base import FilePathPattern, FileUsage
from jigane.languages.python_walk import call_arguments, enclosing_function
from jigane.preconditions.required import required

_WRITE_METHODS = {"write_text", "write_bytes"}
_READ_METHODS = {"read_text", "read_bytes"}
# 使い道は三つだけ。作る・消すは「書く」、有無や一覧の問い合わせは「読む」に寄せる
_PATH_WRITE_METHODS = frozenset({"unlink", "mkdir", "touch", "rmdir"})
_PATH_READ_METHODS = frozenset({"is_file", "is_dir", "exists", "iterdir"})
# 第一引数がパスの標準の関数。`os.path.exists` のように、受け手がモジュールのもの
_MODULE_FUNCTIONS = {
    "os": {
        "scandir": FileUsage.READ,
        "listdir": FileUsage.READ,
        "mkdir": FileUsage.WRITE,
        "makedirs": FileUsage.WRITE,
        "remove": FileUsage.WRITE,
        "unlink": FileUsage.WRITE,
        "rmdir": FileUsage.WRITE,
    },
    "os.path": {
        "exists": FileUsage.READ,
        "isfile": FileUsage.READ,
        "isdir": FileUsage.READ,
    },
}
_WRITE_MODE_CHARACTERS = frozenset("wax+")
# `.open` を持つが、パスの受け手ではないモジュールの名前
_NOT_PATH_MODULES = frozenset({"os", "io", "gzip", "bz2", "lzma", "zipfile", "tarfile", "codecs"})
_PATH_CONSTRUCTORS = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath"})
_SCOPE_BOUNDARIES = ("function_definition", "class_definition")
# `Path` を受け取って `Path` を返すメソッドと属性。受け手が `Path` なら、結果も `Path`
_PATH_RETURNING_METHODS = frozenset(
    {"resolve", "absolute", "expanduser", "with_suffix", "with_name", "with_stem", "joinpath"}
)
_PATH_RETURNING_ATTRIBUTES = frozenset({"parent"})
_PATH_ANNOTATION = re.compile(r"\b(?:Path|PurePath|PosixPath|WindowsPath)\b")
# 名前にこれらの語を含むラッパーだけを、ファイルを扱う呼び出しとして拾う。
# `modified_at` のような時刻や属性の問い合わせは、名前の組み立てではないので拾わない
_WRAPPER_FILE_WORDS = (
    "exist",
    "is_file",
    "isfile",
    "is_dir",
    "isdir",
    "list",
    "read",
    "write",
    "open",
    "mkdir",
    "remove",
    "delete",
    "unlink",
    "copy",
    "move",
    "touch",
)
_MAX_RESOLVE_DEPTH = 5


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


def _mode_usage(mode: Node | None) -> FileUsage:
    if mode is None:
        return FileUsage.READ
    if mode.type != "string" or any(c.type == "interpolation" for c in mode.children):
        return FileUsage.UNKNOWN
    try:
        value = ast.literal_eval(required(mode.text).decode())
    except (ValueError, SyntaxError):
        return FileUsage.UNKNOWN
    if not isinstance(value, str):
        return FileUsage.UNKNOWN
    return FileUsage.WRITE if _WRITE_MODE_CHARACTERS & set(value) else FileUsage.READ


def _is_string_building(node: Node) -> bool:
    """f 文字列か、文字列を含む `+` の連結を部分に持つか。"""
    if node.type == "string" and any(c.type == "interpolation" for c in node.children):
        return True
    if node.type == "binary_operator":
        operator = node.child_by_field_name("operator")
        if operator is not None and _text(operator) == "+":
            sides = (node.child_by_field_name("left"), node.child_by_field_name("right"))
            if any(s is not None and s.type in ("string", "concatenated_string") for s in sides):
                return True
    return any(_is_string_building(child) for child in node.children)


def _is_path_expression(node: Node) -> bool:
    if node.type == "binary_operator":
        operator = node.child_by_field_name("operator")
        return operator is not None and _text(operator) == "/"
    if node.type == "call":
        function = node.child_by_field_name("function")
        if function is None:
            return False
        name = (
            function.child_by_field_name("attribute") if function.type == "attribute" else function
        )
        return name is not None and _text(name) in _PATH_CONSTRUCTORS
    return False


def _span(node: Node) -> tuple[int, int]:
    return (node.start_byte, node.end_byte)


def _contains(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


class _Reader:
    def __init__(self, root: Node, path: str) -> None:
        self._root = root
        self._path = path
        self._found: list[tuple[int, FilePathPattern]] = []
        # 報告済みの式の範囲。同じ式を、ファイルの読み書きと `Path` の組み立てで二重に報告しない
        self._reported: list[tuple[int, int]] = []

    def read(self) -> list[FilePathPattern]:
        nodes = self._all_nodes()
        for node in nodes:
            if node.type == "call":
                self._io_call(node)
        for node in nodes:
            if node.type in ("call", "binary_operator") and _is_path_expression(node):
                self._path_expression(node)
        return [pattern for _, pattern in sorted(self._found, key=lambda item: item[0])]

    def _all_nodes(self) -> list[Node]:
        found: list[Node] = []
        stack = [self._root]
        while stack:
            node = stack.pop()
            found.append(node)
            stack.extend(reversed(node.children))
        return found

    def _add(
        self, anchor: Node, expression: Node, usage: FileUsage, covered: Node | None = None
    ) -> None:
        # `covered` は、原文が呼び出し全体のとき、報告済みとして覚える引数の側の式
        span_node = covered or expression
        self._reported.append((span_node.start_byte, span_node.end_byte))
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

    def _io_call(self, call: Node) -> None:
        function = call.child_by_field_name("function")
        if function is None or any(_contains(done, _span(call)) for done in self._reported):
            return
        positional, keywords = call_arguments(call)
        if function.type == "identifier":
            self._builtin_call(call, function, positional, keywords)
            return
        if function.type != "attribute":
            return
        self._attribute_call(call, function, positional, keywords)

    def _builtin_call(
        self, call: Node, function: Node, positional: list[Node], keywords: dict[str, Node]
    ) -> None:
        """`open(path, mode)`。組み込みの `open` だけを見る。"""
        if _text(function) == "open" and positional:
            mode = positional[1] if len(positional) > 1 else keywords.get("mode")
            self._add_traced(call, positional[0], _mode_usage(mode))

    def _attribute_call(
        self, call: Node, function: Node, positional: list[Node], keywords: dict[str, Node]
    ) -> None:
        """`受け手.メソッド(...)` の呼び出し。モジュールの関数、ラッパー、メソッドの順に見る。"""
        receiver = function.child_by_field_name("object")
        attribute = function.child_by_field_name("attribute")
        if receiver is None or attribute is None:
            return
        method = _text(attribute)
        module_usage = _MODULE_FUNCTIONS.get(_text(receiver), {}).get(method)
        if module_usage is not None:
            if positional:
                self._add_traced(call, positional[0], module_usage)
            return
        if self._wrapper_call(call, receiver, method, positional):
            return
        self._method_call(call, receiver, method, positional, keywords)

    def _method_call(
        self,
        call: Node,
        receiver: Node,
        method: str,
        positional: list[Node],
        keywords: dict[str, Node],
    ) -> None:
        """`Path` のメソッドと、`write_text` などの読み書きのメソッド。受け手がパスの式になる。"""
        if method in _PATH_WRITE_METHODS | _PATH_READ_METHODS:
            # `os.DirEntry.is_file()` など、同じ名前を持つ `Path` でないものを拾わない
            if self._is_known_path(receiver, 0):
                usage = FileUsage.WRITE if method in _PATH_WRITE_METHODS else FileUsage.READ
                self._add_traced(call, receiver, usage)
        elif method in _WRITE_METHODS:
            self._add_traced(call, receiver, FileUsage.WRITE)
        elif method in _READ_METHODS:
            self._add_traced(call, receiver, FileUsage.READ)
        elif method == "open" and _text(receiver) not in _NOT_PATH_MODULES:
            mode = positional[0] if positional else keywords.get("mode")
            self._add_traced(call, receiver, _mode_usage(mode))
        # 上のどれでもない呼び出しは、ファイルを開かない。名前の式は後段の `Path` の走査が拾う。

    def _wrapper_call(
        self, call: Node, receiver: Node, method: str, positional: list[Node]
    ) -> bool:
        """`environment.read_text(path)` のような、パスを引数に受けるプロジェクトの関数。

        `Path.read_text(encoding)` と `Path.write_text(data)` の第一引数はパスではないので、
        第一引数がパスを作る式のときだけ、ラッパーとして扱う。原文は呼び出し全体を取る。
        """
        if not positional or _text(receiver) in _NOT_PATH_MODULES:
            return False
        argument = positional[0]
        resolved = self._previous_assignment(argument) if argument.type == "identifier" else None
        if not self._is_path_producing(resolved or argument):
            return False
        if method in _READ_METHODS:
            usage = FileUsage.READ
        elif method in _WRITE_METHODS and len(positional) > 1:
            usage = FileUsage.WRITE
        elif method in _READ_METHODS | _WRITE_METHODS | {"open"}:
            return False
        elif len(positional) == 1:
            # 語彙に無い呼び出しは、読むか書くかを推測しない。引数が一つのものに絞る。
            # 複数の引数を取る呼び出し（`context.render(path, ...)`）は、行が大きくなりすぎる
            if not any(word in method.lower() for word in _WRAPPER_FILE_WORDS):
                return False
            usage = FileUsage.UNKNOWN
        else:
            return False
        self._add(call, call, usage, covered=argument)
        return True

    def _is_known_path(self, node: Node, depth: int) -> bool:
        """受け手が `Path` と分かるか。`Path(...)`、`/`、それらを入れた変数、注釈付きの引数。"""
        if depth > _MAX_RESOLVE_DEPTH:
            return False
        if node.type == "parenthesized_expression":
            inner = node.named_children[0] if node.named_children else None
            return inner is not None and self._is_known_path(inner, depth)
        if _is_path_expression(node):
            return True
        if node.type == "attribute":
            return self._is_path_attribute(node, depth)
        if node.type == "call":
            return self._is_path_method_call(node, depth)
        return node.type == "identifier" and self._is_path_variable(node, depth)

    def _is_path_attribute(self, node: Node, depth: int) -> bool:
        """`path.parent` のように、`Path` の属性で `Path` になるもの。"""
        attribute = node.child_by_field_name("attribute")
        owner = node.child_by_field_name("object")
        return (
            attribute is not None
            and owner is not None
            and _text(attribute) in _PATH_RETURNING_ATTRIBUTES
            and self._is_known_path(owner, depth + 1)
        )

    def _is_path_method_call(self, node: Node, depth: int) -> bool:
        """`path.resolve()` のように、`Path` のメソッドの呼び出しで `Path` になるもの。"""
        function = node.child_by_field_name("function")
        owner = function.child_by_field_name("object") if function is not None else None
        attribute = function.child_by_field_name("attribute") if function is not None else None
        return (
            attribute is not None
            and owner is not None
            and _text(attribute) in _PATH_RETURNING_METHODS
            and self._is_known_path(owner, depth + 1)
        )

    def _is_path_variable(self, node: Node, depth: int) -> bool:
        """`Path` の注釈が付いた引数、または直前の代入の右辺が `Path` の変数。"""
        if self._annotated_as_path(node):
            return True
        resolved = self._previous_assignment(node)
        return resolved is not None and self._is_known_path(resolved, depth + 1)

    def _annotated_as_path(self, variable: Node) -> bool:
        """同じ関数の引数に、その名前で `Path` の注釈が付いているか。"""
        function = enclosing_function(variable)
        parameters = function.child_by_field_name("parameters") if function is not None else None
        name = _text(variable)
        for parameter in parameters.named_children if parameters is not None else ():
            annotation = parameter.child_by_field_name("type")
            if annotation is None or parameter.type not in (
                "typed_parameter",
                "typed_default_parameter",
            ):
                continue
            declared = parameter.child_by_field_name("name") or next(
                (c for c in parameter.named_children if c.type == "identifier"), None
            )
            if declared is not None and _text(declared) == name:
                return _PATH_ANNOTATION.search(_text(annotation)) is not None
        return False

    def _is_path_producing(self, node: Node) -> bool:
        """名前に `path` を含む関数の呼び出し、または文字列を組む `/` と `Path(...)`。"""
        if node.type == "call":
            function = node.child_by_field_name("function")
            if function is None:
                return False
            name = (
                function.child_by_field_name("attribute")
                if function.type == "attribute"
                else function
            )
            if name is not None and "path" in _text(name).lower():
                return _text(name) not in _PATH_CONSTRUCTORS or _is_string_building(node)
        return _is_path_expression(node) and _is_string_building(node)

    def _add_traced(self, call: Node, expression: Node, usage: FileUsage) -> None:
        resolved = (
            self._previous_assignment(expression) if expression.type == "identifier" else None
        )
        if resolved is None:
            self._add(call, expression, usage)
            return
        self._reported.append((expression.start_byte, expression.end_byte))
        self._add(call, resolved, usage)

    def _previous_assignment(self, variable: Node) -> Node | None:
        """同じ関数（無ければモジュール）の中で、使う位置より前のその変数への代入の右辺。"""
        name = _text(variable)
        best: Node | None = None
        stack = [enclosing_function(variable) or self._root]
        while stack:
            node = stack.pop()
            stack.extend(c for c in node.children if c.type not in _SCOPE_BOUNDARIES)
            if node.type != "assignment" or node.end_byte > variable.start_byte:
                continue
            left = node.child_by_field_name("left")
            if left is None or left.type != "identifier" or _text(left) != name:
                continue
            if best is None or node.start_byte > best.start_byte:
                best = node
        return best.child_by_field_name("right") if best is not None else None

    def _path_expression(self, node: Node) -> None:
        span = (node.start_byte, node.end_byte)
        if any(_contains(done, span) for done in self._reported):
            return
        if not _is_string_building(node):
            return
        parent = node.parent
        while parent is not None and parent.type not in (*_SCOPE_BOUNDARIES, "module"):
            if _is_path_expression(parent) and _is_string_building(parent):
                # 外側が `/` や `Path(...)` なら、外側の式だけを報告する
                return
            parent = parent.parent
        self._add(node, node, FileUsage.UNKNOWN)


def file_path_patterns(root: Node, path: str) -> list[FilePathPattern]:
    return _Reader(root, path).read()
