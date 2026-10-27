"""query の結果に掛ける検査。目録の check 欄から名前で参照する。"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from tree_sitter import Node

from jigane.flaky.setup import TIME_FIXATIONS, FileContext, in_autouse_fixture
from jigane.flaky.target import Shape, Target, Verdict, resolve
from jigane.preconditions.required import required

_COMPARISON_OPERAND_COUNT = 2
_ASSERT_EQUALS_ARGUMENT_COUNT = 2
_EXPECTED_ACTUAL_COUNT = 2


@dataclass(frozen=True)
class Finding:
    """検査が当たったときの付帯。宛先を確かめられない当たりは警告に下げ、理由を持たせる。"""

    undetermined: bool
    reason: str


# False は当たらない、True は理由のない当たり、Finding は理由のある当たり
Check = Callable[[Node, bytes], bool | Finding]
# ファイルの外の共通のセットアップで見えた情報も受ける検査
ContextCheck = Callable[[Node, bytes, FileContext], bool | Finding]

UNDETERMINED_REASON = "宛先を確かめられない"


def network_result(target: Target) -> bool | Finding:
    if target.verdict is Verdict.LOCAL:
        return False
    if target.verdict is Verdict.REMOTE:
        return Finding(undetermined=False, reason=f"宛先がローカルの外（{target.host}）")
    return Finding(undetermined=True, reason=UNDETERMINED_REASON)


_MUTATORS = "^(append|extend|update|add|clear|pop|setdefault|insert|remove)$"


def _text(node: Node) -> str:
    return required(node.text).decode()


def _ancestor(node: Node, types: frozenset[str]) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in types:
            return current
        current = current.parent
    return None


def _in_test_function(node: Node) -> bool:
    function = _ancestor(node, frozenset({"function_definition"}))
    name = function.child_by_field_name("name") if function is not None else None
    return name is not None and _text(name).startswith("test")


def not_in_with(node: Node, _source: bytes, /) -> bool:
    current = node.parent
    while current is not None and current.type not in ("block", "module"):
        if current.type == "with_item":
            return False
        current = current.parent
    return True


def unordered_list_equality(node: Node, _source: bytes, /) -> bool:
    operands = [child for child in node.children if child.is_named]
    if not any(child.type == "==" for child in node.children):
        return False
    for operand in operands:
        if operand.type != "call":
            continue
        function = operand.child_by_field_name("function")
        arguments = operand.child_by_field_name("arguments")
        if function is None or arguments is None or _text(function) != "list":
            continue
        for argument in arguments.named_children:
            if argument.type in ("set", "set_comprehension"):
                return True
            if argument.type == "call":
                callee = required(argument.child_by_field_name("function"))
                if _text(callee) == "set" or re.search(r"[.](keys|values)$", _text(callee)):
                    return True
    return False


def _is_computed(node: Node) -> bool:
    # 算術の式と呼び出しの結果が、計算した値。属性や名前をそのまま読んだ値は含めない
    if node.type == "binary_operator":
        return True
    return node.type == "call" and "approx" not in _text(node)


def float_equality(node: Node, _source: bytes, /) -> bool:
    if not any(child.type == "==" for child in node.children):
        return False
    operands = [child for child in node.children if child.is_named]
    return any(child.type != "float" and _is_computed(child) for child in operands)


def float_assert_equal(node: Node, _source: bytes, /) -> bool:
    arguments = required(node.child_by_field_name("arguments")).named_children
    return any(argument.type != "float" and _is_computed(argument) for argument in arguments)


def module_global_mutation(node: Node, _source: bytes, /) -> bool:
    function = node.child_by_field_name("function")
    target = function.child_by_field_name("object") if function is not None else None
    if target is None or target.type != "identifier" or not _in_test_function(node):
        return False
    method = required(required(function).child_by_field_name("attribute"))
    if re.match(_MUTATORS, _text(method)) is None:
        return False
    root = node
    while root.parent is not None:
        root = root.parent
    for assignment in root.children:
        # この文法では代入が式文で包まれず、モジュールの直下に裸の assignment として現れる
        if assignment.type != "assignment":
            continue
        left = assignment.child_by_field_name("left")
        right = assignment.child_by_field_name("right")
        if (
            left is not None
            and right is not None
            and _text(left) == _text(target)
            and right.type in ("list", "dictionary", "set")
        ):
            return True
    return False


def python_network_target(node: Node, _source: bytes, /) -> bool | Finding:
    arguments = required(node.child_by_field_name("arguments")).named_children
    positional = [a for a in arguments if a.type != "keyword_argument"]
    if positional:
        return network_result(resolve(positional[0], _PYTHON_SHAPE))
    urls = [a for a in arguments if _text(required(a.child_by_field_name("name"))) == "url"]
    if urls:
        return network_result(
            resolve(required(urls[0].child_by_field_name("value")), _PYTHON_SHAPE)
        )
    return network_result(Target(Verdict.UNDETERMINED))


def in_test_function(node: Node, _source: bytes, /) -> bool:
    return _in_test_function(node)


def thread_start_in_test(node: Node, source: bytes) -> bool:
    if not _in_test_function(node):
        return False
    function = required(node.child_by_field_name("function"))
    target = required(function.child_by_field_name("object"))
    if target.type == "call":
        return True
    pattern = rf"\b{re.escape(_text(target))}\s*=\s*(threading\.)?Thread\("
    return re.search(pattern.encode(), source) is not None


_SWIFT_FUNCTIONS = frozenset({"function_declaration"})
_SWIFT_COMPUTED = frozenset({"additive_expression", "multiplicative_expression"})
_SWIFT_CONVERSIONS = frozenset({"Double", "Float", "CGFloat", "Decimal"})
_SWIFT_MUTATORS = "^(append|insert|remove|removeAll|removeValue|updateValue|popLast|removeLast)$"
_SWIFT_CLOSE = re.compile(r"[.](close|closeFile)\(")


def _swift_arguments(node: Node) -> list[Node]:
    suffix = next((c for c in node.children if c.type == "call_suffix"), None)
    values = (
        next((c for c in suffix.children if c.type == "value_arguments"), None) if suffix else None
    )
    return [c for c in values.children if c.type == "value_argument"] if values else []


def _swift_label(argument: Node) -> str | None:
    label = next((c for c in argument.children if c.type == "value_argument_label"), None)
    return _text(label) if label is not None else None


def _swift_value(argument: Node) -> Node:
    return argument.named_children[-1]


def _swift_unlabeled(node: Node) -> list[Node]:
    # XCTAssertEqual の引数と #expect の == の両辺を、比べる値の並びとして揃える
    if node.type == "equality_expression":
        return list(node.named_children)
    return [_swift_value(a) for a in _swift_arguments(node) if _swift_label(a) is None]


def _swift_in_test_function(node: Node) -> bool:
    function = _ancestor(node, _SWIFT_FUNCTIONS)
    if function is None:
        return False
    name = next((c for c in function.children if c.type == "simple_identifier"), None)
    modifiers = next((c for c in function.children if c.type == "modifiers"), None)
    return (name is not None and _text(name).startswith("test")) or (
        modifiers is not None and "@Test" in _text(modifiers)
    )


def swift_random_without_generator(node: Node, _source: bytes, /) -> bool:
    return not any(_swift_label(a) == "using" for a in _swift_arguments(node))


def swift_non_local_session(node: Node, _source: bytes, /) -> bool | Finding:
    arguments = _swift_arguments(node)
    if not arguments:
        return network_result(Target(Verdict.UNDETERMINED))
    return network_result(resolve(_swift_value(arguments[0]), _SWIFT_SHAPE))


def _swift_is_computed(node: Node) -> bool:
    if node.type in _SWIFT_COMPUTED:
        return True
    if node.type != "call_expression":
        return False
    return _text(node.named_children[0]) not in _SWIFT_CONVERSIONS


def swift_float_equality(node: Node, _source: bytes, /) -> bool:
    if any(_swift_label(a) == "accuracy" for a in _swift_arguments(node)):
        return False
    values = _swift_unlabeled(node)
    return any(v.type == "real_literal" for v in values) and any(
        _swift_is_computed(v) for v in values
    )


def _swift_unordered_array(value: Node, source: bytes) -> bool:
    if value.type != "call_expression" or _text(value.named_children[0]) != "Array":
        return False
    arguments = _swift_arguments(value)
    if len(arguments) != 1:
        return False
    inner = _text(_swift_value(arguments[0]))
    if inner.startswith("Set") or re.search(r"[.](keys|values)$", inner):
        return True
    declared = rf"\b{re.escape(inner)}\s*(:\s*Set<|=\s*Set[(<])"
    return re.search(declared.encode(), source) is not None


def swift_unordered_array_equality(node: Node, source: bytes) -> bool:
    return any(_swift_unordered_array(v, source) for v in _swift_unlabeled(node))


def swift_skip_in_condition(node: Node, _source: bytes, /) -> bool:
    return _ancestor(node, frozenset({"if_statement", "guard_statement"})) is not None


def _swift_static_vars(source: bytes) -> set[str]:
    return {m.decode() for m in re.findall(rb"\bstatic\s+var\s+(\w+)", source)}


def _swift_last_segment(text: str) -> str:
    return text.rsplit(".", 1)[-1]


def swift_static_var_assignment(node: Node, source: bytes) -> bool:
    target = node.child_by_field_name("target") or node.named_children[0]
    return _swift_in_test_function(node) and (
        _swift_last_segment(_text(target)) in _swift_static_vars(source)
    )


def swift_static_var_mutation_call(node: Node, source: bytes) -> bool:
    callee = node.named_children[0]
    if callee.type != "navigation_expression" or not _swift_in_test_function(node):
        return False
    owner, _, method = _text(callee).rpartition(".")
    return re.match(_SWIFT_MUTATORS, method) is not None and _swift_last_segment(
        owner
    ) in _swift_static_vars(source)


def swift_filehandle_not_closed(node: Node, _source: bytes, /) -> bool:
    function = _ancestor(node, _SWIFT_FUNCTIONS)
    if function is not None and _SWIFT_CLOSE.search(_text(function)):
        return False
    owner = _ancestor(node, frozenset({"class_declaration"})) or node
    # 持ち主のクラスの tearDown が閉じる設計は、関数の内側からは見えない
    return not ("tearDown" in _text(owner) and _SWIFT_CLOSE.search(_text(owner)))


def swift_in_test_function(node: Node, _source: bytes, /) -> bool:
    return _swift_in_test_function(node)


def _is_scoping_call(node: Node) -> bool:
    if node.type == "as_pattern":
        node = node.named_children[0]
    function = node.child_by_field_name("function") if node.type == "call" else None
    return function is not None and _text(function) in TIME_FIXATIONS


def _inside_scoped_fixation(node: Node) -> bool:
    """当たりが、字面で時刻を固定するブロックか、固定の修飾子を持つ定義の本体の内側にあるか。"""
    child = node
    current = node.parent
    while current is not None:
        if current.child_by_field_name("body") == child:
            if current.type == "with_statement":
                clause = next(c for c in current.children if c.type == "with_clause")
                for item in clause.named_children:
                    value = item.child_by_field_name("value")
                    if value is not None and _is_scoping_call(value):
                        return True
            elif current.type in ("function_definition", "class_definition"):
                outer = current.parent
                if outer is not None and outer.type == "decorated_definition":
                    decorators = [c for c in outer.children if c.type == "decorator"]
                    if any(_is_scoping_call(d.named_children[0]) for d in decorators):
                        return True
        child = current
        current = current.parent
    return False


def time_without_fixation(node: Node, _source: bytes, context: FileContext, /) -> bool | Finding:
    if _inside_scoped_fixation(node):
        return False
    if not context.time_fixations:
        return True
    fixation = context.time_fixations[0]
    return Finding(
        undetermined=True,
        reason=f"{fixation.path}:{fixation.line} の autouse の fixture が時刻を固定しています",
    )


def _has_plain_seed(node: Node) -> bool:
    """autouse の fixture の外に、同じファイルの random.seed の呼び出しがあるか。"""
    root = node
    while root.parent is not None:
        root = root.parent
    stack = [root]
    while stack:
        current = stack.pop()
        if current.type == "call":
            function = current.child_by_field_name("function")
            if (
                function is not None
                and _text(function) == "random.seed"
                and not in_autouse_fixture(current)
            ):
                return True
        stack.extend(current.children)
    return False


def random_without_seed(node: Node, _source: bytes, context: FileContext, /) -> bool | Finding:
    if _has_plain_seed(node):
        return False
    if not context.seed_fixations:
        return True
    fixation = context.seed_fixations[0]
    return Finding(
        undetermined=True,
        reason=f"{fixation.path}:{fixation.line} の autouse の fixture が乱数の種を固定しています",
    )


_CLOCK_CALL = re.compile(
    r"^(time[.])?(time|monotonic|perf_counter|process_time)(_ns)?$|^time[.](time|monotonic|perf_counter|process_time)_ns$"
)
_UPPER_BOUND = {"<": "left", "<=": "left", ">": "right", ">=": "right"}


def _calls_clock(node: Node, clock_names: set[str]) -> bool:
    """式が時計の呼び出しか、時計から導いた名前を含むか。"""
    if node.type == "call":
        function = node.child_by_field_name("function")
        if function is not None and _CLOCK_CALL.match(_text(function)):
            return True
    if node.type == "identifier" and _text(node) in clock_names:
        return True
    return any(_calls_clock(child, clock_names) for child in node.children)


def _clock_names(scope: Node) -> set[str]:
    """scope の中で、時計の呼び出しから（推移的に）代入された名前。"""
    assignments = [
        (left, right)
        for node in _descendants(scope, "assignment")
        if (left := node.child_by_field_name("left")) is not None
        and left.type == "identifier"
        and (right := node.child_by_field_name("right")) is not None
    ]
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for left, right in assignments:
            name = _text(left)
            if name not in names and _calls_clock(right, names):
                names.add(name)
                changed = True
    return names


def wall_clock_upper_bound(node: Node, _source: bytes, /) -> bool:
    operands = [child for child in node.children if child.is_named]
    operators = [child.type for child in node.children if not child.is_named]
    if (
        len(operands) != _COMPARISON_OPERAND_COUNT
        or len(operators) != 1
        or operators[0] not in _UPPER_BOUND
    ):
        return False
    # 上限の比べ方では、大きい側が数の上限、小さい側が測った経過時間
    measured, bound = (
        (operands[0], operands[1])
        if _UPPER_BOUND[operators[0]] == "left"
        else (operands[1], operands[0])
    )
    if bound.type not in ("integer", "float"):
        return False
    function = _ancestor(node, frozenset({"function_definition"}))
    scope = function if function is not None else _ancestor(node, frozenset({"module"}))
    if scope is None:
        return False
    return _calls_clock(measured, _clock_names(scope))


CONTEXT_CHECKS: dict[str, ContextCheck] = {
    "time_without_fixation": time_without_fixation,
    "random_without_seed": random_without_seed,
}

CHECKS: dict[str, Check] = {
    "not_in_with": not_in_with,
    "unordered_list_equality": unordered_list_equality,
    "float_equality": float_equality,
    "float_assert_equal": float_assert_equal,
    "wall_clock_upper_bound": wall_clock_upper_bound,
    "module_global_mutation": module_global_mutation,
    "python_network_target": python_network_target,
    "in_test_function": in_test_function,
    "thread_start_in_test": thread_start_in_test,
    "swift_random_without_generator": swift_random_without_generator,
    "swift_non_local_session": swift_non_local_session,
    "swift_float_equality": swift_float_equality,
    "swift_unordered_array_equality": swift_unordered_array_equality,
    "swift_skip_in_condition": swift_skip_in_condition,
    "swift_static_var_assignment": swift_static_var_assignment,
    "swift_static_var_mutation_call": swift_static_var_mutation_call,
    "swift_filehandle_not_closed": swift_filehandle_not_closed,
    "swift_in_test_function": swift_in_test_function,
}


_JAVA_TEST_ANNOTATIONS = frozenset(
    {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"}
)
_JAVA_COLLECTION_MUTATORS = re.compile(
    r"^(add|addAll|put|putAll|remove|removeAll|clear|offer|push|pop|poll|set|merge|compute)$"
)
_JAVA_LIST_LIKE = re.compile(
    r"\bList\.(of|copyOf)\b|\bArrays\.asList\b|\bnew\s+(Array|Linked)List\b|\bCollections\.(singletonList|emptyList)\b"
)
_JAVA_UNORDERED_SOURCE = re.compile(
    r"\b(keySet|values|entrySet)\(\)|\bnew\s+(HashSet|HashMap)\b|\bSet\.copyOf\b|\btoSet\(\)"
)
_JAVA_CLOSEABLE_FALLBACK = frozenset({"return_statement"})


def _java_arguments(node: Node) -> Node | None:
    return node.child_by_field_name("arguments")


def _java_enclosing_test_method(node: Node) -> Node | None:
    method = _ancestor(node, frozenset({"method_declaration"}))
    if method is None:
        return None
    for modifiers in (c for c in method.children if c.type == "modifiers"):
        for annotation in modifiers.children:
            name = annotation.child_by_field_name("name")
            if (
                annotation.type in ("marker_annotation", "annotation")
                and name is not None
                and _text(name) in _JAVA_TEST_ANNOTATIONS
            ):
                return method
    return None


def java_no_arguments(node: Node, _source: bytes, /) -> bool:
    arguments = _java_arguments(node)
    return arguments is not None and arguments.named_child_count == 0


def java_now_without_clock(node: Node, _source: bytes, /) -> bool:
    arguments = _java_arguments(node)
    if arguments is None:
        return False
    # 注入した時計は識別子かフィールドで渡る。Clock.systemUTC() のような呼び出しは実時計
    return not any(
        argument.type in ("identifier", "field_access") for argument in arguments.named_children
    )


def java_non_local_target(node: Node, _source: bytes, /) -> bool | Finding:
    # URL(...).openConnection() は、宛先を持つ URL の生成の側から読む
    receiver = node.child_by_field_name("object")
    carrier = (
        receiver
        if receiver is not None and receiver.type == "object_creation_expression"
        else node
    )
    arguments = _java_arguments(carrier)
    if arguments is None or not arguments.named_children:
        return network_result(Target(Verdict.UNDETERMINED))
    return network_result(resolve(arguments.named_children[0], _JAVA_SHAPE))


def java_unordered_list_equality(node: Node, _source: bytes, /) -> bool:
    arguments = _java_arguments(node)
    if arguments is None:
        return False
    texts = [_text(argument) for argument in arguments.named_children[:2]]
    if len(texts) < _ASSERT_EQUALS_ARGUMENT_COUNT:
        return False
    # 順序の無い源と List への変換が同じ引数に同居する形（new ArrayList<>(map.keySet())）
    if any(_JAVA_LIST_LIKE.search(t) and _JAVA_UNORDERED_SOURCE.search(t) for t in texts):
        return True
    for index, text in enumerate(texts):
        other = texts[1 - index]
        if _JAVA_LIST_LIKE.search(text) and _JAVA_UNORDERED_SOURCE.search(other):
            return True
    return False


def _java_is_computed(node: Node) -> bool:
    return node.type in ("binary_expression", "method_invocation", "parenthesized_expression")


def java_float_assert_equals(node: Node, _source: bytes, /) -> bool:
    arguments = required(_java_arguments(node)).named_children
    if len(arguments) != _ASSERT_EQUALS_ARGUMENT_COUNT:
        return False
    return any(
        argument.type != "decimal_floating_point_literal" and _java_is_computed(argument)
        for argument in arguments
    )


def java_inside_if(node: Node, _source: bytes, /) -> bool:
    return _ancestor(node, frozenset({"if_statement"})) is not None


def _java_static_fields(class_node: Node) -> dict[str, tuple[bool, bool]]:
    """static なフィールドの名前から、(final か, 初期値が new の式か) への対応。"""
    fields: dict[str, tuple[bool, bool]] = {}
    body = class_node.child_by_field_name("body")
    for field in body.children if body is not None else []:
        if field.type != "field_declaration":
            continue
        modifiers = [c.type for m in field.children if m.type == "modifiers" for c in m.children]
        if "static" not in modifiers:
            continue
        for declarator in (c for c in field.children if c.type == "variable_declarator"):
            value = declarator.child_by_field_name("value")
            created = value is not None and value.type == "object_creation_expression"
            name = _text(required(declarator.child_by_field_name("name")))
            fields[name] = ("final" in modifiers, created)
    return fields


def java_static_field_mutation(node: Node, _source: bytes, /) -> bool:
    if _java_enclosing_test_method(node) is None:
        return False
    class_node = _ancestor(node, frozenset({"class_declaration"}))
    if class_node is None:
        return False
    mutating_call = node.type == "method_invocation"
    if node.type == "assignment_expression":
        target = node.child_by_field_name("left")
        if target is not None and target.type == "array_access":
            target = target.child_by_field_name("array")
    elif node.type == "update_expression":
        target = node.named_children[0]
    else:
        target = node.child_by_field_name("object")
        method = required(node.child_by_field_name("name"))
        if _JAVA_COLLECTION_MUTATORS.match(_text(method)) is None:
            return False
    if target is None:
        return False
    field = _java_static_fields(class_node).get(_text(target).rsplit(".", 1)[-1])
    if field is None:
        return False
    is_final, created = field
    # final でも、new で作ったコレクションは要素の書き換えで共有状態になる
    return not is_final or (mutating_call and created)


def java_not_in_resource(node: Node, _source: bytes, /) -> bool:
    if _ancestor(node, frozenset({"resource"})) is not None:
        return False
    return node.parent is None or node.parent.type not in _JAVA_CLOSEABLE_FALLBACK


def java_in_test_method(node: Node, _source: bytes, /) -> bool:
    return _java_enclosing_test_method(node) is not None


def java_thread_variable_start(node: Node, source: bytes) -> bool:
    if _java_enclosing_test_method(node) is None:
        return False
    target = required(node.child_by_field_name("object"))
    pattern = rf"\bThread\s+{re.escape(_text(target))}\s*=\s*new\s+Thread\("
    return re.search(pattern.encode(), source) is not None


_CSHARP_TEST_ATTRIBUTE = re.compile(
    r"^(Fact|Theory|SkippableFact|SkippableTheory|Test|TestCase|TestCaseSource|TestMethod|DataTestMethod)(Attribute)?$"
)
_CSHARP_MUTATORS = re.compile(
    r"^(Add|AddRange|Remove|Clear|Insert|Push|Enqueue|Pop|Dequeue|Set\w*)$"
)
_CSHARP_ABSOLUTE_PATH = re.compile(r'^@?"(/tmp/|/var/|/home/|/Users/|[A-Za-z]:[\\/]|\\\\)')
_CSHARP_UNORDERED = re.compile(
    r"(Keys|Values)\s*\.To(List|Array)\(\)|HashSet<.*>.*\.To(List|Array)\(\)"
)


def _csharp_arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    return [] if arguments is None else arguments.named_children


def _csharp_argument_value(argument: Node) -> Node:
    return argument.named_children[-1]


def _csharp_method(node: Node) -> Node | None:
    return _ancestor(node, frozenset({"method_declaration"}))


def _csharp_in_test_method(node: Node) -> bool:
    method = _csharp_method(node)
    if method is None:
        return False
    for attribute_list in method.children:
        if attribute_list.type != "attribute_list":
            continue
        for attribute in attribute_list.named_children:
            first = attribute.named_children[0] if attribute.named_children else None
            if first is not None and _CSHARP_TEST_ATTRIBUTE.match(_text(first)):
                return True
    return False


def _csharp_target(node: Node) -> bool | Finding:
    arguments = _csharp_arguments(node)
    if not arguments:
        return network_result(Target(Verdict.UNDETERMINED))
    return network_result(resolve(_csharp_argument_value(arguments[0]), _CSHARP_SHAPE))


def csharp_without_time_provider(_node: Node, source: bytes, /) -> bool:
    return b"TimeProvider" not in source


def csharp_random_without_seed(node: Node, _source: bytes, /) -> bool:
    return not _csharp_arguments(node)


def _csharp_receiver(node: Node) -> Node | None:
    function = node.child_by_field_name("function")
    if function is None or function.type != "member_access_expression":
        return None
    return function.child_by_field_name("expression")


def csharp_http_call_to_remote(node: Node, source: bytes) -> bool | Finding:
    receiver = _csharp_receiver(node)
    if receiver is None:
        return False
    # モックのリポジトリの GetAsync を拾わないよう、受け手が HttpClient と分かるものに絞る
    if receiver.type == "object_creation_expression":
        is_http_client = "HttpClient" in _text(receiver)
    else:
        name = re.escape(_text(receiver))
        is_http_client = (
            re.search(rf"HttpClient\s+{name}\b|\b{name}\s*=\s*new\s+HttpClient\b".encode(), source)
            is not None
        )
    if not is_http_client:
        return False
    return _csharp_target(node)


def csharp_not_localhost(node: Node, _source: bytes, /) -> bool | Finding:
    return _csharp_target(node)


def csharp_fixed_absolute_path(node: Node, _source: bytes, /) -> bool:
    arguments = _csharp_arguments(node)
    if not arguments:
        return False
    value = _csharp_argument_value(arguments[0])
    if value.type not in ("string_literal", "verbatim_string_literal"):
        return False
    return _CSHARP_ABSOLUTE_PATH.match(_text(value)) is not None


def csharp_unordered_equality(node: Node, source: bytes) -> bool:
    for argument in _csharp_arguments(node):
        text = _text(argument)
        if _CSHARP_UNORDERED.search(text):
            return True
        identifier = re.fullmatch(r"(\w+)\.To(?:List|Array)\(\)", text)
        if identifier is not None:
            declared = rf"(HashSet|Dictionary)<[^;]*>\s+{identifier.group(1)}\b"
            if re.search(declared.encode(), source):
                return True
    return False


def csharp_float_equality(node: Node, _source: bytes, /) -> bool:
    values = [_csharp_argument_value(a) for a in _csharp_arguments(node)]
    values = [
        v for v in values if v.type not in ("string_literal", "interpolated_string_expression")
    ]
    # 精度や許容誤差の引数が付いていれば、3 つ以上になる
    if len(values) != _EXPECTED_ACTUAL_COUNT:
        return False
    computed = ("binary_expression", "invocation_expression", "parenthesized_expression")
    literals = [v for v in values if v.type == "real_literal"]
    return len(literals) == 1 and any(v.type in computed for v in values)


def _descendants(node: Node, type_name: str) -> list[Node]:
    found: list[Node] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == type_name:
            found.append(current)
        stack.extend(current.children)
    return found


def _is_conditional_skip_argument(argument: Node) -> bool:
    # 文字列の中の ? を三項演算子と取り違えないよう、Skip の値の節点の型で見る
    if re.match(r"Skip\s*=", _text(argument)) is None:
        return False
    return argument.named_children[-1].type == "conditional_expression"


def csharp_conditional_skip_attribute(node: Node, _source: bytes, /) -> bool:
    text = _text(node)
    if re.search(r"Skip\s*=", text) is None:
        return False
    if any(_is_conditional_skip_argument(n) for n in _descendants(node, "attribute_argument")):
        return True
    return _ancestor(node, frozenset({"preproc_if", "preproc_elif", "preproc_else"})) is not None


def _csharp_static_fields(node: Node) -> tuple[set[str], set[str]]:
    """static で const でないフィールドの名前を、(readonly でないもの, readonly のもの) で返す。"""
    declaration = _ancestor(node, frozenset({"class_declaration", "struct_declaration"}))
    body = declaration.child_by_field_name("body") if declaration is not None else None
    writable: set[str] = set()
    readonly: set[str] = set()
    if body is None:
        return writable, readonly
    for field in body.children:
        if field.type != "field_declaration":
            continue
        modifiers = {_text(c) for c in field.children if c.type == "modifier"}
        if "static" not in modifiers or "const" in modifiers:
            continue
        names = readonly if "readonly" in modifiers else writable
        for variable in field.named_children:
            if variable.type != "variable_declaration":
                continue
            names.update(
                _text(required(d.named_children[0]))
                for d in variable.named_children
                if d.type == "variable_declarator"
            )
    return writable, readonly


def csharp_static_field_mutation(node: Node, _source: bytes, /) -> bool:
    if not _csharp_in_test_method(node):
        return False
    target: Node | None
    # readonly の参照でも、指す先のコレクションの中身の書き換えは共有の可変の状態を変える。
    # フィールドそのものへの代入は、readonly ならコンストラクタでしか書けないため当てない
    changes_contents = False
    if node.type == "assignment_expression":
        target = node.child_by_field_name("left")
        if target is not None and target.type == "element_access_expression":
            target = target.named_children[0]
            changes_contents = True
    elif node.type == "invocation_expression":
        function = node.child_by_field_name("function")
        if function is None or function.type != "member_access_expression":
            return False
        name = function.child_by_field_name("name")
        if name is None or _CSHARP_MUTATORS.match(_text(name)) is None:
            return False
        target = function.child_by_field_name("expression")
        changes_contents = True
    else:
        # 前置と後置の ++ と --。演算子の他に子は被演算子の 1 つだけ
        operands = node.named_children
        target = operands[0] if operands else None
    if target is None:
        return False
    if target.type == "member_access_expression":
        target = target.child_by_field_name("name")
    if target is None or target.type != "identifier":
        return False
    writable, readonly = _csharp_static_fields(node)
    return _text(target) in writable or (changes_contents and _text(target) in readonly)


def csharp_not_disposed(node: Node, _source: bytes, /) -> bool:
    current = node.parent
    while current is not None and current.type not in ("method_declaration", "block"):
        if current.type == "using_statement":
            return False
        if current.type == "local_declaration_statement" and re.match(
            rb"(await\s+)?using\b", required(current.text)
        ):
            return False
        current = current.parent
    return True


def csharp_unjoined_concurrency(node: Node, source: bytes) -> bool:
    if not _csharp_in_test_method(node):
        return False
    function = required(node.child_by_field_name("function"))
    if not _text(function).endswith(".Start"):
        # Task.Run の結果が await も代入もされず、文として捨てられている
        parent = node.parent
        return parent is not None and parent.type == "expression_statement"
    receiver = required(function.child_by_field_name("expression"))
    if receiver.type == "object_creation_expression":
        return "Thread" in _text(receiver)
    name = re.escape(_text(receiver))
    pattern = rf"\b{name}\s*=\s*new\s+(System\.Threading\.)?Thread\(|Thread\s+{name}\b"
    return re.search(pattern.encode(), source) is not None


CHECKS.update(
    {
        "java_no_arguments": java_no_arguments,
        "java_now_without_clock": java_now_without_clock,
        "java_non_local_target": java_non_local_target,
        "java_unordered_list_equality": java_unordered_list_equality,
        "java_float_assert_equals": java_float_assert_equals,
        "java_inside_if": java_inside_if,
        "java_static_field_mutation": java_static_field_mutation,
        "java_not_in_resource": java_not_in_resource,
        "java_in_test_method": java_in_test_method,
        "java_thread_variable_start": java_thread_variable_start,
        "csharp_without_time_provider": csharp_without_time_provider,
        "csharp_random_without_seed": csharp_random_without_seed,
        "csharp_http_call_to_remote": csharp_http_call_to_remote,
        "csharp_not_localhost": csharp_not_localhost,
        "csharp_fixed_absolute_path": csharp_fixed_absolute_path,
        "csharp_unordered_equality": csharp_unordered_equality,
        "csharp_float_equality": csharp_float_equality,
        "csharp_conditional_skip_attribute": csharp_conditional_skip_attribute,
        "csharp_static_field_mutation": csharp_static_field_mutation,
        "csharp_not_disposed": csharp_not_disposed,
        "csharp_unjoined_concurrency": csharp_unjoined_concurrency,
    }
)


def _single_return(body: Node | None) -> Node | None:
    statements = body.named_children if body is not None else []
    if len(statements) != 1 or statements[0].type != "return_statement":
        return None
    values = statements[0].named_children
    return values[0] if values else None


def _name_of(node: Node) -> str:
    return _text(required(node.child_by_field_name("name")))


def _python_bindings(root: Node) -> Iterator[tuple[str, Node]]:
    for node in _walk_nodes(root):
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if node.type == "assignment" and left is not None and left.type == "identifier" and right:
            yield _text(left), right


def _python_methods(root: Node) -> Iterator[tuple[str, Node]]:
    for node in _walk_nodes(root):
        if node.type == "function_definition":
            value = _single_return(node.child_by_field_name("body"))
            if value is not None:
                yield _name_of(node), value


def _python_call_name(node: Node) -> str | None:
    function = node.child_by_field_name("function")
    if node.type == "call" and function is not None and function.type == "identifier":
        return _text(function)
    return None


def _java_bindings(root: Node) -> Iterator[tuple[str, Node]]:
    for node in _walk_nodes(root):
        value = node.child_by_field_name("value")
        if node.type == "variable_declarator" and value is not None:
            yield _name_of(node), value


def _java_methods(root: Node) -> Iterator[tuple[str, Node]]:
    for node in _walk_nodes(root):
        if node.type == "method_declaration":
            value = _single_return(node.child_by_field_name("body"))
            if value is not None:
                yield _name_of(node), value


def _java_call_name(node: Node) -> str | None:
    owner = node.child_by_field_name("object")
    if node.type != "method_invocation" or (owner is not None and owner.type != "this"):
        return None
    return _name_of(node)


def _csharp_bindings(root: Node) -> Iterator[tuple[str, Node]]:
    for node in _walk_nodes(root):
        if node.type == "variable_declarator" and len(node.named_children) > 1:
            initializer = node.named_children[-1]
            if initializer.type == "equals_value_clause":
                initializer = initializer.named_children[-1]
            yield _name_of(node), initializer
        elif node.type == "property_declaration":
            property_value = node.child_by_field_name("value")
            if property_value is not None:
                yield _name_of(node), property_value


def _csharp_methods(root: Node) -> Iterator[tuple[str, Node]]:
    for node in _walk_nodes(root):
        if node.type not in ("method_declaration", "local_function_statement"):
            continue
        body = node.child_by_field_name("body")
        if body is not None and body.type == "arrow_expression_clause":
            yield _name_of(node), body.named_children[-1]
            continue
        value = _single_return(body)
        if value is not None:
            yield _name_of(node), value


def _csharp_call_name(node: Node) -> str | None:
    function = node.child_by_field_name("function")
    if node.type != "invocation_expression" or function is None:
        return None
    if function.type == "identifier":
        return _text(function)
    if function.type == "member_access_expression":
        owner = required(function.child_by_field_name("expression"))
        if owner.type == "this_expression":
            return _text(required(function.child_by_field_name("name")))
    return None


def _swift_bindings(root: Node) -> Iterator[tuple[str, Node]]:
    for node in _walk_nodes(root):
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if node.type == "property_declaration" and name is not None and value is not None:
            yield _text(name), value


def _swift_methods(root: Node) -> Iterator[tuple[str, Node]]:
    for node in _walk_nodes(root):
        if node.type != "function_declaration":
            continue
        body = node.child_by_field_name("body")
        statements = (
            next((c for c in body.children if c.type == "statements"), None) if body else None
        )
        if statements is None or statements.named_child_count != 1:
            continue
        statement = statements.named_children[0]
        only: Node | None = statement
        if statement.type == "control_transfer_statement":
            only = statement.child_by_field_name("result")
        elif statement.type in ("property_declaration", "if_statement", "guard_statement"):
            continue
        if only is not None:
            yield (
                _text(required(next(c for c in node.children if c.type == "simple_identifier"))),
                only,
            )


def _swift_call_name(node: Node) -> str | None:
    first = node.named_children[0] if node.named_children else None
    if node.type == "call_expression" and first is not None and first.type == "simple_identifier":
        return _text(first)
    return None


def _swift_member_name(node: Node) -> str | None:
    target = node.child_by_field_name("target")
    suffix = node.child_by_field_name("suffix")
    if node.type != "navigation_expression" or target is None or suffix is None:
        return None
    if _text(target) not in ("self", "Self"):
        return None
    return _text(required(suffix.child_by_field_name("suffix")))


def _walk_nodes(root: Node) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


_PYTHON_SHAPE = Shape(
    frozenset({"string"}),
    frozenset({"interpolation"}),
    frozenset({"identifier"}),
    frozenset({"tuple"}),
    _python_bindings,
    _python_methods,
    _python_call_name,
)
_JAVA_SHAPE = Shape(
    frozenset({"string_literal"}),
    frozenset(),
    frozenset({"identifier"}),
    frozenset(),
    _java_bindings,
    _java_methods,
    _java_call_name,
)
_CSHARP_SHAPE = Shape(
    frozenset(
        {
            "string_literal",
            "verbatim_string_literal",
            "raw_string_literal",
            "interpolated_string_expression",
        }
    ),
    frozenset({"interpolation"}),
    frozenset({"identifier"}),
    frozenset(),
    _csharp_bindings,
    _csharp_methods,
    _csharp_call_name,
)
_SWIFT_SHAPE = Shape(
    frozenset({"line_string_literal", "multi_line_string_literal", "raw_string_literal"}),
    frozenset({"interpolated_expression"}),
    frozenset({"simple_identifier"}),
    frozenset(),
    _swift_bindings,
    _swift_methods,
    _swift_call_name,
    _swift_member_name,
)
