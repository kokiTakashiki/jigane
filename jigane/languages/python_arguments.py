"""argparse の副コマンドと引数を、構文木だけから読む。実行も import もしない。"""

import ast

from tree_sitter import Node

from jigane.languages.base import CommandArgument
from jigane.languages.python_walk import call_arguments, enclosing_function
from jigane.preconditions.required import required

# 呼び出しの連鎖をたどる深さ。再帰する関数と循環で止まるための上限
_MAX_DEPTH = 4

# 最上位の parser の引数に付ける副コマンドの名前
TOP_LEVEL_COMMAND = ""


def _text(node: Node) -> str:
    return required(node.text).decode()


def _string_value(node: Node) -> str:
    if node.type == "string" and not any(c.type == "interpolation" for c in node.children):
        try:
            value = ast.literal_eval(_text(node))
        except (ValueError, SyntaxError):
            return _text(node)
        if isinstance(value, str):
            return value
    return _text(node)


def _callee_name(call: Node) -> str | None:
    function = call.child_by_field_name("function")
    if function is None:
        return None
    if function.type == "identifier":
        return _text(function)
    if function.type == "attribute":
        attribute = function.child_by_field_name("attribute")
        return _text(attribute) if attribute is not None else None
    return None


def _parameter_names(function: Node) -> list[str]:
    parameters = function.child_by_field_name("parameters")
    names: list[str] = []
    for child in parameters.named_children if parameters is not None else ():
        if child.type == "identifier":
            names.append(_text(child))
        elif child.type in ("default_parameter", "typed_default_parameter"):
            name = child.child_by_field_name("name")
            if name is not None:
                names.append(_text(name))
        elif child.type == "typed_parameter":
            identifier = next((c for c in child.named_children if c.type == "identifier"), None)
            if identifier is not None:
                names.append(_text(identifier))
    return names


def _parser_command(value: Node) -> str | None:
    """代入の右辺が parser を作る式なら、その副コマンドの名前。違えば None。"""
    if value.type != "call":
        return None
    callee = _callee_name(value)
    if callee == "add_parser":
        positional, keywords = call_arguments(value)
        name = positional[0] if positional else keywords.get("name")
        if name is not None and name.type == "string":
            return _string_value(name)
        return None
    if callee == "ArgumentParser":
        return TOP_LEVEL_COMMAND
    return None


def _group_of(value: Node) -> tuple[str, bool] | None:
    """代入の右辺が群を作る式なら、(親の parser の変数, 排他か)。違えば None。"""
    if value.type != "call":
        return None
    callee = _callee_name(value)
    if callee not in ("add_mutually_exclusive_group", "add_argument_group"):
        return None
    function = value.child_by_field_name("function")
    receiver = function.child_by_field_name("object") if function is not None else None
    if receiver is None or receiver.type != "identifier":
        return None
    return _text(receiver), callee == "add_mutually_exclusive_group"


class _Reader:
    def __init__(self, root: Node) -> None:
        self._root = root
        self._calls: list[Node] = []
        # スコープ（関数の節点の id、モジュールでは根の id）ごとの、parser を束縛する代入
        self._assignments: dict[int, list[tuple[Node, str, str]]] = {}
        # スコープごとの、群を束縛する代入: (代入, 群の変数, 親の parser の変数, 排他か)
        self._groups: dict[int, list[tuple[Node, str, str, bool]]] = {}
        self._collect(root)

    def _collect(self, root: Node) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "call":
                self._calls.append(node)
            elif node.type == "assignment":
                self._record_assignment(node)
            stack.extend(reversed(node.children))
        self._calls.sort(key=lambda call: call.start_byte)

    def _record_assignment(self, assignment: Node) -> None:
        left = assignment.child_by_field_name("left")
        right = assignment.child_by_field_name("right")
        if left is None or right is None or left.type != "identifier":
            return
        scope = enclosing_function(assignment)
        key = scope.id if scope is not None else self._root.id
        group = _group_of(right)
        if group is not None:
            self._groups.setdefault(key, []).append((assignment, _text(left), *group))
            return
        command = _parser_command(right)
        if command is None:
            return
        self._assignments.setdefault(key, []).append((assignment, _text(left), command))

    def arguments(self, path: str) -> list[CommandArgument]:
        found: list[CommandArgument] = []
        for call in self._calls:
            if _callee_name(call) != "add_argument":
                continue
            function = call.child_by_field_name("function")
            receiver = function.child_by_field_name("object") if function is not None else None
            if receiver is None or receiver.type != "identifier":
                continue
            variable = _text(receiver)
            group = self._latest_group(call, variable)
            if group is not None:
                assignment, parent, exclusive = group
                commands = self._resolve(parent, assignment, 0, frozenset())
                found.extend(
                    _argument(call, c, path, variable, exclusive=exclusive) for c in commands
                )
                continue
            commands = self._resolve(variable, call, 0, frozenset())
            found.extend(_argument(call, command, path) for command in commands)
        return found

    def _latest_group(self, at: Node, name: str) -> tuple[Node, str, bool] | None:
        scope = enclosing_function(at)
        found: tuple[Node, str, bool] | None = None
        for assignment, variable, parent, exclusive in self._groups.get(
            scope.id if scope is not None else self._root.id, ()
        ):
            if variable == name and assignment.start_byte < at.start_byte:
                found = (assignment, parent, exclusive)
        return found

    def _resolve(self, name: str, at: Node, depth: int, visiting: frozenset[int]) -> list[str]:
        """at の位置で変数 name が指す parser の副コマンドの名前。追えなければ空。"""
        scope = enclosing_function(at)
        local = self._latest(scope.id if scope is not None else self._root.id, name, at)
        if local is not None:
            return [local]
        if scope is None:
            # モジュール直下で束縛が無い名前は、他に追う先が無い
            return []
        if name in _parameter_names(scope):
            return self._resolve_parameter(scope, name, depth, visiting)
        # 関数の中から、モジュール直下で作った parser を参照する形
        outer = self._latest(self._root.id, name, at)
        return [outer] if outer is not None else []

    def _latest(self, scope_id: int, name: str, at: Node) -> str | None:
        found: str | None = None
        for assignment, variable, command in self._assignments.get(scope_id, ()):
            if variable == name and assignment.start_byte < at.start_byte:
                found = command
        return found

    def _resolve_parameter(
        self, function: Node, name: str, depth: int, visiting: frozenset[int]
    ) -> list[str]:
        function_name = function.child_by_field_name("name")
        if depth >= _MAX_DEPTH or function.id in visiting or function_name is None:
            return []
        parameters = _parameter_names(function)
        is_method = bool(parameters) and parameters[0] in ("self", "cls")
        index = parameters.index(name)
        commands: list[str] = []
        for call in self._calls:
            if _callee_name(call) != _text(function_name):
                continue
            callee = call.child_by_field_name("function")
            shift = 1 if is_method and callee is not None and callee.type == "attribute" else 0
            positional, keywords = call_arguments(call)
            argument = keywords.get(name)
            if argument is None and 0 <= index - shift < len(positional):
                argument = positional[index - shift]
            if argument is None or argument.type != "identifier":
                # 引数が識別子でない呼び出しは、parser の出どころを構文だけでは決められない
                continue
            resolved = self._resolve(_text(argument), call, depth + 1, visiting | {function.id})
            commands.extend(c for c in resolved if c not in commands)
        return commands


def _argument(
    call: Node, command: str, path: str, group: str | None = None, *, exclusive: bool = False
) -> CommandArgument:
    positional, keywords = call_arguments(call)
    names = tuple(_string_value(node) for node in positional)
    is_positional = bool(names) and not names[0].startswith("-")

    def original(key: str) -> str | None:
        node = keywords.get(key)
        return _text(node) if node is not None else None

    required_text = original("required")
    if is_positional:
        is_required = original("nargs") not in ('"?"', "'?'", '"*"', "'*'")
    else:
        is_required = required_text == "True"
    return CommandArgument(
        command=command,
        names=names,
        positional=is_positional,
        dest=original("dest"),
        default=original("default"),
        required_text=required_text,
        required=is_required,
        help=original("help"),
        action=original("action"),
        type=original("type"),
        choices=original("choices"),
        path=path,
        line=call.start_point.row + 1,
        group=group,
        exclusive=exclusive,
    )


def command_arguments(root: Node, path: str) -> list[CommandArgument]:
    return _Reader(root).arguments(path)
