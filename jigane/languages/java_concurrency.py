import re

from tree_sitter import Node

from jigane.languages import concurrency_core as core
from jigane.languages.base import (
    BlockingWait,
    Body,
    ConcurrencyStep,
    Isolation,
    IsolationContext,
    MemberAssignment,
    QueueDispatch,
    QueueDomain,
    StructuredConcurrency,
    StructuredKind,
    SyncCall,
    TaskKind,
    UnstructuredTask,
)
from jigane.languages.concurrency_core import (
    UI_THREAD,
    CallSources,
    Extractor,
    StatelessWalk,
    is_ui_thread,
    line_of,
    text_of,
)

_UI_THREAD = UI_THREAD
_THREAD_POOL = QueueDomain("スレッドプール")
# Swing の `J` 始まり以外の UI の型。AWT、JavaFX の Application・Node・Parent、
# Android の Activity・Fragment・View など。
# 上位の型を辿った先がこれらか Swing の型に当たれば UI の型。
# どれも一般的な名前なので、名前だけでは決めず、
# 書かれたファイルの import か完全修飾の綴りで確かめる
_UI_TYPES = frozenset(
    {
        "Component",
        "Container",
        "Window",
        "Frame",
        "Dialog",
        "Panel",
        "Applet",
        "Application",
        "Node",
        "Parent",
        "Stage",
        "Scene",
        "Pane",
        "Region",
        "Control",
        "Activity",
        "AppCompatActivity",
        "FragmentActivity",
        "ComponentActivity",
        "Fragment",
        "DialogFragment",
        "View",
        "ViewGroup",
        "TextView",
        "Button",
        "ImageView",
        "EditText",
        "RecyclerView",
    }
)
# UI の型を持つパッケージの先頭。import がこのどれかで始まる型だけを UI の型とみなす
_UI_PACKAGES = ("javafx.", "java.awt", "javax.swing", "java.applet", "android.", "androidx.")
# JFrame、JPanel、JComponent のような `J` で始まる Swing の型。JUnit は Swing ではない
_SWING_TYPE = re.compile(r"J[A-Z][a-z]\w*")
_NOT_SWING_PREFIX = "JUnit"
_TYPE_DECLARATIONS = frozenset(
    {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
)
# 別の本体になる宣言。ラムダは executor などへ渡されたときだけ、渡された先の本体として降りる
_SKIPPED = frozenset({"lambda_expression", "class_body", "class_declaration"})
_SUBMIT_NAMES = frozenset({"submit", "execute"})
_ASYNC_FACTORY_NAMES = frozenset({"supplyAsync", "runAsync"})
_UI_DISPATCH_RECEIVERS = frozenset({"SwingUtilities", "EventQueue", "Platform"})
_UI_DISPATCH_NAMES = frozenset({"invokeLater", "invokeAndWait", "runLater"})
_VIEW_POST_NAMES = frozenset({"post", "postDelayed"})
_COLLECTION_FACTORIES = frozenset({"of", "asList"})
_FUTURE_NAMES = re.compile(r"future|promise|task|thread|async|submit", re.IGNORECASE)
# ブロックする待ち合わせは、受け手の型が構文から分からないので、
# 受け手の名前の綴りと引数の数で見分ける。
# `get`・`join` は Future・Thread、`await` は CountDownLatch・CyclicBarrier・Condition、
# `acquire` は Semaphore を指す名前に限る。`map.get(key)` を拾わないよう、引数の数も絞る
_BLOCKING_RECEIVERS: dict[str, tuple[re.Pattern[str], tuple[int, ...]]] = {
    "get": (_FUTURE_NAMES, (0, 2)),
    "join": (_FUTURE_NAMES, (0, 1)),
    "await": (re.compile(r"latch|barrier|condition|gate", re.IGNORECASE), (0, 2)),
    "acquire": (re.compile(r"semaphore|permit", re.IGNORECASE), (0, 1)),
}


def _type_name(node: Node | None) -> str | None:
    """型の綴りの末尾の単純名。総称型 `Foo<T>` と `a.b.Foo` は Foo。"""
    if node is None:
        return None
    if node.type in ("type_identifier", "identifier"):
        return text_of(node)
    if node.type in ("generic_type", "scoped_type_identifier"):
        names = [
            c for c in node.children if c.type in ("type_identifier", "scoped_type_identifier")
        ]
        return _type_name(names[-1]) if names else None
    return None


def _is_ui_package(package: str) -> bool:
    return package.startswith(_UI_PACKAGES)


def _file_root(node: Node) -> Node:
    root = node
    while root.parent is not None:
        root = root.parent
    return root


def _declares_type(root: Node, name: str) -> bool:
    stack = [root]
    while stack:
        current = stack.pop()
        if (
            current.type in _TYPE_DECLARATIONS
            and _type_name(current.child_by_field_name("name")) == name
        ):
            return True
        stack.extend(current.children)
    return False


def _is_confirmed_ui_name(name: str, scope: Node | None) -> bool:
    """UI の名前の表に当たる型を、import か完全修飾の綴りで UI のパッケージの型と確かめる。"""
    if "." in name:
        package, _, simple = name.rpartition(".")
        return simple in _UI_TYPES and _is_ui_package(package)
    if name not in _UI_TYPES or scope is None:
        # 書かれたファイルが分からなければ確かめる手段が無く、一般的な名前は UI の型としない
        return False
    root = _file_root(scope)
    wildcard = False
    for node in root.children:
        if node.type != "import_declaration":
            continue
        spelled = (
            text_of(node)
            .removeprefix("import")
            .removeprefix(" static")
            .strip()
            .rstrip(";")
            .strip()
        )
        package, _, simple = spelled.rpartition(".")
        if simple == name and _is_ui_package(package):
            return True
        wildcard = wildcard or (simple == "*" and _is_ui_package(package + "."))
    # 同じファイルで宣言された同名の型は、ワイルドカードの import より先に解決される
    return wildcard and not _declares_type(root, name)


def _callee(call: Node) -> tuple[str | None, str | None, Node | None]:
    """呼び出しの (受け手の原文, 名前, 受け手の節点)。受け手の無い呼び出しの受け手は None。"""
    name = call.child_by_field_name("name")
    if call.type != "method_invocation" or name is None:
        return None, None, None
    receiver = call.child_by_field_name("object")
    if receiver is None:
        return None, text_of(name), None
    return text_of(receiver), text_of(name), receiver


def _positional(call: Node) -> list[Node]:
    arguments = call.child_by_field_name("arguments")
    return list(arguments.named_children) if arguments is not None else []


def _declared_bases(type_node: Node) -> list[str]:
    bases: list[str] = []
    for child in type_node.children:
        if child.type not in ("superclass", "super_interfaces", "extends_interfaces"):
            continue
        stack = list(child.named_children)
        while stack:
            current = stack.pop()
            if current.type == "type_list":
                stack.extend(current.named_children)
                continue
            # 完全修飾の綴り `a.b.Foo` は、パッケージで UI の型と確かめるため原文のまま残す
            name = (
                text_of(current)
                if current.type == "scoped_type_identifier"
                else _type_name(current)
            )
            if name is not None:
                bases.append(name)
    return bases


def is_async(_member_node: Node, /) -> bool:
    # Java に await は無いので、非同期の関数という区別は持たない。
    return False


def _receiver_label(node: Node) -> str:
    """札に出す受け手の綴り。呼び出しの連なりは引数を省いて短くする。"""
    if node.type in ("identifier", "field_access", "this"):
        return text_of(node)
    if node.type == "method_invocation":
        _, name, _ = _callee(node)
        inner = node.child_by_field_name("object")
        prefix = f"{_receiver_label(inner)}." if inner is not None else ""
        return f"{prefix}{name}(…)"
    return "…"


def _is_scope_type(creation: Node | None) -> bool:
    if creation is None or creation.type != "object_creation_expression":
        return False
    return text_of(creation.child_by_field_name("type") or creation).startswith(
        "StructuredTaskScope"
    )


def _is_thread_creation(node: Node) -> bool:
    if node.type != "object_creation_expression":
        return False
    return text_of(node.child_by_field_name("type") or node) == "Thread"


def _is_thread_builder(node: Node) -> bool:
    """`Thread.ofVirtual()` と `Thread.ofPlatform()`。"""
    if node.type != "method_invocation":
        return False
    owner, name, _ = _callee(node)
    return owner == "Thread" and name in ("ofVirtual", "ofPlatform")


def _is_swing_type(name: str) -> bool:
    return _SWING_TYPE.fullmatch(name) is not None and not name.startswith(_NOT_SWING_PREFIX)


def _assignment(x: Extractor[None], node: Node) -> list[ConcurrencyStep]:
    steps = x.walk(node)
    left = node.child_by_field_name("left")
    if left is not None and left.type == "field_access":
        receiver = left.child_by_field_name("object")
        member = left.child_by_field_name("field")
        if receiver is not None and member is not None:
            steps.append(MemberAssignment(receiver, text_of(member), line_of(node)))
    return steps


def _call(x: Extractor[None], call: Node) -> list[ConcurrencyStep]:
    if call.id in x.consumed:
        return []
    receiver, name, receiver_node = _callee(call)
    special = _special(x, call, receiver, name, receiver_node)
    if special is not None:
        return special
    steps = x.walk(call)
    blocking = _blocking(x, call, receiver, name, receiver_node)
    if blocking is not None:
        steps.append(BlockingWait(blocking, line_of(call)))
        return steps
    site = x.sites.get(call.id)
    if site is not None:
        steps.append(SyncCall(site, site.line))
    return steps


def _blocking(
    x: Extractor[None],
    call: Node,
    receiver: str | None,
    name: str | None,
    receiver_node: Node | None,
) -> str | None:
    # スレッドプールの上で待つのは Java では普通の形で、協調スレッドも主スレッドも塞がない
    if receiver is None or name is None or receiver_node is None:
        return None
    if x.domain == _THREAD_POOL:
        return None
    if name == "sleep" and (receiver in ("Thread", "java.lang.Thread") or "TimeUnit" in receiver):
        return f"{receiver}.sleep"
    rule = _BLOCKING_RECEIVERS.get(name)
    if rule is None:
        return None
    pattern, arities = rule
    label = _receiver_label(receiver_node)
    if len(_positional(call)) not in arities or pattern.search(label) is None:
        return None
    return f"{label}.{name}"


def _body(x: Extractor[None], argument: Node, domain: Isolation) -> Body:
    return Body(domain, tuple(x.lambda_steps(argument, domain)))


def _lambdas_of(collection: Node) -> list[Node]:
    """`List.of(() -> a(), () -> b())` のように並べて書いたラムダ。変数の中身は分からない。"""
    if collection.type != "method_invocation":
        return []
    _, name, _ = _callee(collection)
    if name not in _COLLECTION_FACTORIES:
        return []
    return [a for a in _positional(collection) if a.type == "lambda_expression"]


def _future_branch(x: Extractor[None], argument: Node) -> Body:
    """allOf・anyOf に渡された Future 一つを、枝の本体にする。"""
    if argument.type != "method_invocation":
        return Body(x.domain, ())
    receiver, name, _ = _callee(argument)
    arguments = _positional(argument)
    if receiver == "CompletableFuture" and name in _ASYNC_FACTORY_NAMES and arguments:
        return _body(x, arguments[0], _THREAD_POOL)
    site = x.sites.get(argument.id)
    steps = x.walk(argument)
    if site is not None:
        steps.append(SyncCall(site, site.line))
    return Body(x.domain, tuple(steps))


def _scope(x: Extractor[None], node: Node) -> list[ConcurrencyStep]:
    """StructuredTaskScope を開く try の中の fork を、一つの SCOPE にする。"""
    specification = next((c for c in node.children if c.type == "resource_specification"), None)
    block = node.child_by_field_name("body")
    scopes: dict[str, str] = {}
    for resource in specification.named_children if specification is not None else []:
        name = resource.child_by_field_name("name")
        value = resource.child_by_field_name("value")
        if name is not None and value is not None and _is_scope_type(value):
            scopes[text_of(name)] = text_of(value.child_by_field_name("type") or value)
    if block is None or not scopes:
        return x.walk(node)
    forks: list[Node] = []
    stack = [block]
    while stack:
        current = stack.pop()
        stack.extend(reversed(current.children))
        if current.type != "method_invocation":
            continue
        receiver, callee_name, _ = _callee(current)
        if callee_name == "fork" and receiver in scopes and _positional(current):
            forks.append(current)
    for fork in forks:
        x.consumed.add(fork.id)
    branches = tuple(_body(x, _positional(fork)[0], _THREAD_POOL) for fork in forks)
    label = next(iter(scopes.values())).split("<")[0]
    scope_step = StructuredConcurrency(StructuredKind.SCOPE, branches, line_of(node), label)
    return [scope_step, *x.walk(node)]


def _special(
    x: Extractor[None],
    call: Node,
    receiver: str | None,
    name: str | None,
    receiver_node: Node | None,
) -> list[ConcurrencyStep] | None:
    line = line_of(call)
    arguments = _positional(call)
    if name is None:
        return None
    if receiver == "CompletableFuture" and name in ("allOf", "anyOf"):
        kind = StructuredKind.JOIN_ALL if name == "allOf" else StructuredKind.RACE
        branches = tuple(_future_branch(x, a) for a in arguments)
        return [StructuredConcurrency(kind, branches, line, name)]
    if name in ("invokeAll", "invokeAny") and receiver is not None and arguments:
        kind = StructuredKind.JOIN_ALL if name == "invokeAll" else StructuredKind.RACE
        branches = tuple(_body(x, a, _THREAD_POOL) for a in _lambdas_of(arguments[0]))
        return [StructuredConcurrency(kind, branches, line, name)]
    task = _pool_task_argument(receiver, name, receiver_node, arguments)
    if task is not None:
        return [UnstructuredTask(TaskKind.TASK, _body(x, task, _THREAD_POOL), line)]
    dispatched = arguments[0] if arguments and _is_ui_dispatch(receiver, name) else None
    if dispatched is not None and dispatched.type in ("lambda_expression", "method_reference"):
        return [QueueDispatch(_body(x, dispatched, _UI_THREAD), line, name)]
    return None


def _pool_task_argument(
    receiver: str | None, name: str, receiver_node: Node | None, arguments: list[Node]
) -> Node | None:
    """スレッドプールで走る本体のラムダかメソッド参照。Task に当たらなければ None。"""
    if receiver is None or receiver_node is None:
        return None
    passed = arguments[0] if arguments else None
    if name == "start" and _is_thread_creation(receiver_node):
        # `new Thread(() -> …).start()` の本体は、start でなく生成の引数にある
        inner = _positional(receiver_node)
        passed = inner[0] if inner else None
    elif not (
        (name == "start" and _is_thread_builder(receiver_node))
        or (name == "startVirtualThread" and receiver == "Thread")
        or name in _SUBMIT_NAMES
        or (receiver == "CompletableFuture" and name in _ASYNC_FACTORY_NAMES)
    ):
        return None
    if passed is None or passed.type not in ("lambda_expression", "method_reference"):
        return None
    return passed


def _is_ui_dispatch(receiver: str | None, name: str) -> bool:
    if receiver in _UI_DISPATCH_RECEIVERS:
        return name in _UI_DISPATCH_NAMES
    if name == "runOnUiThread":
        return True
    return receiver is not None and name in _VIEW_POST_NAMES


class _Rules(StatelessWalk):
    skipped = _SKIPPED
    type_declarations = _TYPE_DECLARATIONS

    def is_main_thread(self, domain: Isolation) -> bool:
        return is_ui_thread(domain)

    def is_ui_name(self, name: str, scope: Node | None) -> bool:
        # `J` 始まりは紛れが少ないので、Swing の型として名前だけで認める
        return _is_swing_type(name) or _is_confirmed_ui_name(name, scope)

    def type_name(self, node: Node | None) -> str | None:
        return _type_name(node)

    def declared_bases(self, type_node: Node) -> list[str]:
        return _declared_bases(type_node)

    def lambda_body(self, node: Node) -> Node | None:
        return node.child_by_field_name("body") if node.type == "lambda_expression" else None

    def visit(self, extractor: Extractor[None], node: Node) -> list[ConcurrencyStep] | None:
        if node.type == "try_with_resources_statement":
            return _scope(extractor, node)
        if node.type == "method_invocation":
            return _call(extractor, node)
        if node.type == "assignment_expression":
            return _assignment(extractor, node)
        return None


_RULES = _Rules()


def is_ui_type(type_name: str, context: IsolationContext, scope: Node | None = None) -> bool:
    """型自身か上位の型が Swing、JavaFX、Android の UI の型なら真。上位は context で辿る。

    `J` 始まりの Swing の型以外は、scope のファイルの import か完全修飾の綴りで確かめる。
    """
    return core.is_ui_type(type_name, context, _RULES, scope)


def is_main_thread(domain: Isolation) -> bool:
    return core.is_main_thread(domain, _RULES)


def isolation_of(node: Node, context: IsolationContext) -> Isolation:
    return core.isolation_of(node, context, _RULES)


def concurrency_steps(
    adapter: CallSources, body: Node, source: bytes, enclosing: Isolation
) -> list[ConcurrencyStep]:
    return core.concurrency_steps(_RULES, adapter, body, source, enclosing)


__all__ = [
    "concurrency_steps",
    "is_async",
    "is_main_thread",
    "is_ui_type",
    "isolation_of",
]
