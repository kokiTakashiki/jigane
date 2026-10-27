from dataclasses import dataclass

from tree_sitter import Node

from jigane.languages import concurrency_core as core
from jigane.languages.base import (
    AwaitCall,
    BlockingWait,
    Body,
    ConcurrencyStep,
    EventLoopDomain,
    Isolation,
    NonisolatedDomain,
    QueueDispatch,
    QueueDomain,
    StructuredConcurrency,
    StructuredKind,
    SyncCall,
    TaskKind,
    UnstructuredTask,
)
from jigane.languages.concurrency_core import (
    CallSources,
    Extractor,
    WalkRules,
    line_of,
    text_of,
)

_EXECUTOR_MIN_ARGUMENTS = 2  # executor と呼ぶ関数

_EVENT_LOOP = EventLoopDomain()
_EXECUTOR = QueueDomain("executor")
# 別の本体になる宣言。手順を取るために降りない
_SKIPPED = frozenset({"function_definition", "class_definition", "lambda"})
_TASK_FACTORIES = frozenset({"create_task", "ensure_future"})
# asyncio の関数を名前だけで書く（from asyncio import gather）形も受ける
_ASYNCIO_RECEIVERS = frozenset({None, "asyncio"})
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request"})
_STATEMENT_PARENTS = frozenset({"expression_statement", "block", "module"})
_SUBPROCESS_CALLS = frozenset({"run", "call", "check_call", "check_output"})
# threading の待ち合わせの道具。束縛された名前と、受け手が呼び出しそのものの形の両方で見分ける
_THREADING_PRIMITIVES = frozenset(
    {"Event", "Lock", "RLock", "Semaphore", "BoundedSemaphore", "Condition", "Barrier", "Thread"}
)
_PRIMITIVE_WORDS = ("event", "lock", "semaphore", "condition", "barrier")
_WAIT_NAMES = frozenset({"wait", "acquire"})
# 受け手のモジュールごとに、呼ぶとスレッドを止める関数の名前
_BLOCKING_MODULE_CALLS = {
    "time": frozenset({"sleep"}),
    "requests": _HTTP_METHODS,
    "httpx": _HTTP_METHODS,
    "subprocess": _SUBPROCESS_CALLS,
    "urllib.request": frozenset({"urlopen"}),
}


def _definition(node: Node) -> Node:
    if node.type == "decorated_definition":
        definition = node.child_by_field_name("definition")
        return definition if definition is not None else node
    return node


def is_async(node: Node) -> bool:
    definition = _definition(node)
    return definition.type == "function_definition" and any(
        c.type == "async" for c in definition.children
    )


def isolation_of(node: Node) -> Isolation:
    """async def の本体はイベントループで走る。同期の関数とクラスは隔離されない領域。"""
    return _EVENT_LOOP if is_async(node) else NonisolatedDomain()


def _callee(call: Node) -> tuple[str | None, str | None, Node | None]:
    """呼び出しの (受け手の原文, 名前, 受け手の節点)。受け手の無い呼び出しの受け手は None。"""
    function = call.child_by_field_name("function")
    if function is None:
        return None, None, None
    if function.type == "identifier":
        return None, text_of(function), None
    if function.type == "attribute":
        obj = function.child_by_field_name("object")
        attribute = function.child_by_field_name("attribute")
        if obj is not None and attribute is not None:
            return text_of(obj), text_of(attribute), obj
    return None, None, None


def _positional(call: Node) -> list[Node]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    skipped = {"comment", "keyword_argument", "list_splat", "dictionary_splat"}
    return [c for c in arguments.named_children if c.type not in skipped]


def _bound_primitives(root: Node) -> frozenset[str]:
    found: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "assignment":
            continue
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None or right.type != "call":
            continue
        _, name, _ = _callee(right)
        if name in _THREADING_PRIMITIVES:
            found.add(text_of(left))
    return frozenset(found)


@dataclass
class _State:
    primitives: frozenset[str]
    # `async with asyncio.TaskGroup() as tg` の tg の名前と、その中で集めた枝
    groups: dict[str, list[Body]]


def _call(x: Extractor[_State], call: Node, *, awaited: bool) -> list[ConcurrencyStep]:
    receiver, name, receiver_node = _callee(call)
    special = _special(x, call, receiver, name)
    if special is not None:
        return special
    # 引数と受け手の連なりの中の呼び出しは、先に手順にする
    steps = x.walk(call)
    site = x.sites.get(call.id)
    if site is None:
        return steps
    if awaited:
        steps.append(AwaitCall(site, site.line))
    elif _blocks(x.state, receiver, name, receiver_node):
        label = f"{receiver}.{name}" if receiver is not None else str(name)
        steps.append(BlockingWait(label, site.line))
    else:
        steps.append(SyncCall(site, site.line))
    return steps


def _coroutine(x: Extractor[_State], argument: Node) -> list[ConcurrencyStep]:
    """渡されたコルーチンの呼び出し一つを、渡された先の本体の手順にする。"""
    if argument.type != "call":
        return []
    site = x.sites.get(argument.id)
    return [*x.walk(argument), *([AwaitCall(site, site.line)] if site else [])]


def _function(x: Extractor[_State], argument: Node) -> list[ConcurrencyStep]:
    """スレッドへ渡す関数。参照は呼び先を決められないので手順にせず、ラムダは中の呼び出しを取る。"""
    return x.lambda_steps(argument, _EXECUTOR)


def _special(
    x: Extractor[_State], call: Node, receiver: str | None, name: str | None
) -> list[ConcurrencyStep] | None:
    line = line_of(call)
    arguments = _positional(call)
    asyncio_like = receiver in _ASYNCIO_RECEIVERS
    groups = x.state.groups
    if name == "create_task" and receiver in groups:
        body = Body(_EVENT_LOOP, tuple(_coroutine(x, arguments[0]) if arguments else ()))
        groups[receiver].append(body)
        return []
    loop_like = receiver is not None and "loop" in receiver.lower()
    if name in _TASK_FACTORIES and (asyncio_like or loop_like) and arguments:
        body = Body(_EVENT_LOOP, tuple(_coroutine(x, arguments[0])))
        # 式文として呼ぶ形だけが戻り値を捨てる。代入、添字、引数、await の中は保持している。
        # 文の直下の呼び出しは、この文法では式文の節を挟まず block の子に付く
        parent = call.parent
        kept = parent is None or parent.type not in _STATEMENT_PARENTS
        return [UnstructuredTask(TaskKind.TASK, body, line, kept)]
    if name == "gather" and asyncio_like:
        branches = tuple(Body(_EVENT_LOOP, tuple(_coroutine(x, a))) for a in arguments)
        return [StructuredConcurrency(StructuredKind.JOIN_ALL, branches, line, "gather")]
    if name == "to_thread" and asyncio_like and arguments:
        body = Body(_EXECUTOR, tuple(_function(x, arguments[0])))
        return [QueueDispatch(body, line, "to_thread")]
    if name == "run_in_executor" and len(arguments) >= _EXECUTOR_MIN_ARGUMENTS:
        body = Body(_EXECUTOR, tuple(_function(x, arguments[1])))
        return [QueueDispatch(body, line, "run_in_executor")]
    return None


def _with(x: Extractor[_State], node: Node) -> list[ConcurrencyStep]:
    aliases = _group_aliases(node)
    if not aliases:
        return x.walk(node)
    for alias in aliases:
        x.state.groups[alias] = []
    # TaskGroup() の生成は並行の手順ではないので、with の頭は飛ばして本体だけを取る
    steps: list[ConcurrencyStep] = []
    for child in node.children:
        if child.type != "with_clause":
            steps.extend(x.visit(child))
    for alias in aliases:
        branches = x.state.groups.pop(alias)
        if branches:
            steps.append(
                StructuredConcurrency(
                    StructuredKind.SCOPE,
                    tuple(branches),
                    line_of(node),
                    "タスクグループ",
                    "addTask",
                )
            )
    return steps


def _group_aliases(node: Node) -> list[str]:
    clause = next((c for c in node.children if c.type == "with_clause"), None)
    aliases: list[str] = []
    for item in clause.named_children if clause is not None else ():
        value = item.child_by_field_name("value")
        if value is None or value.type != "as_pattern" or not value.named_children:
            continue
        created = value.named_children[0]
        alias = value.child_by_field_name("alias")
        if created.type == "call" and _callee(created)[1] == "TaskGroup" and alias is not None:
            aliases.append(text_of(alias))
    return aliases


def _is_wait_primitive(state: _State, receiver: str, receiver_node: Node | None) -> bool:
    """`wait` と `acquire` の受け手が、threading の待ち合わせの道具か。"""
    # 受け手の型は構文から分からないので、束縛された名前か、名前の語で見分ける
    if receiver in state.primitives or any(w in receiver.lower() for w in _PRIMITIVE_WORDS):
        return True
    return (
        receiver_node is not None
        and receiver_node.type == "call"
        and _callee(receiver_node)[1] in _THREADING_PRIMITIVES
    )


def _blocks(
    state: _State, receiver: str | None, name: str | None, receiver_node: Node | None
) -> bool:
    if receiver is None:
        return name == "urlopen"
    module_calls = _BLOCKING_MODULE_CALLS.get(receiver)
    if module_calls is not None:
        return name in module_calls
    if name == "join":
        return receiver in state.primitives
    if name not in _WAIT_NAMES:
        return False
    return _is_wait_primitive(state, receiver, receiver_node)


class _Rules(WalkRules[_State]):
    skipped = _SKIPPED

    def lambda_body(self, node: Node) -> Node | None:
        return node.child_by_field_name("body") if node.type == "lambda" else None

    def make_state(self, body: Node) -> _State:
        return _State(_bound_primitives(body), {})

    def visit(self, extractor: Extractor[_State], node: Node) -> list[ConcurrencyStep] | None:
        if node.type == "await":
            inner = next((c for c in node.named_children if c.type == "call"), None)
            return (
                _call(extractor, inner, awaited=True)
                if inner is not None
                else extractor.walk(node)
            )
        if node.type == "call":
            return _call(extractor, node, awaited=False)
        if node.type == "with_statement":
            return _with(extractor, node)
        return None


_RULES = _Rules()


def concurrency_steps(adapter: CallSources, body: Node, source: bytes) -> list[ConcurrencyStep]:
    return core.concurrency_steps(_RULES, adapter, body, source, NonisolatedDomain())
