import re

from tree_sitter import Node

from jigane.languages import concurrency_core as core
from jigane.languages.base import (
    AwaitCall,
    BlockingWait,
    Body,
    ConcurrencyStep,
    ContinueOn,
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
# WPF、WinForms、MAUI の UI の型。上位の型を辿った先がこれらに当たれば UI の型
_UI_TYPES = frozenset(
    {
        "Window",
        "UserControl",
        "Page",
        "Control",
        "ContentControl",
        "FrameworkElement",
        "UIElement",
        "Form",
        "ContentPage",
        "ContentView",
        "NavigationPage",
        "TabbedPage",
        "FlyoutPage",
        "Shell",
        "VisualElement",
        "Button",
        "TextBox",
        "TextBlock",
        "Label",
        "ListBox",
        "ListView",
        "ComboBox",
        "CheckBox",
        "ProgressBar",
        "DataGrid",
        "Entry",
        "Editor",
    }
)
_TYPE_DECLARATIONS = frozenset(
    {"class_declaration", "struct_declaration", "record_declaration", "interface_declaration"}
)
# 別の本体になる宣言。ラムダは Task.Run などへ渡されたときだけ、渡された先の本体として降りる
_SKIPPED = frozenset(
    {"lambda_expression", "anonymous_method_expression", "local_function_statement"}
)
_MEMBER_BODIES = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "accessor_declaration",
        "lambda_expression",
        "anonymous_method_expression",
    }
)
_LAMBDAS = frozenset({"lambda_expression", "anonymous_method_expression"})
_DISPATCH_NAMES = frozenset({"Invoke", "BeginInvoke", "InvokeAsync"})
_MAIN_THREAD_NAMES = frozenset({"BeginInvokeOnMainThread", "InvokeOnMainThreadAsync"})
_WAIT_NAMES = frozenset({"Wait", "WaitAll", "WaitAny"})
_TASK_FACTORY = re.compile(r"(?:\w+\.)*(?:Task\.Factory|TaskFactory)")


def _simple_name(node: Node | None) -> str | None:
    """識別子か、総称型の名前（`Run<T>`）の名前の部分。"""
    if node is None:
        return None
    if node.type == "identifier":
        return text_of(node)
    if node.type == "generic_name":
        first = next((c for c in node.children if c.type == "identifier"), None)
        return text_of(first) if first is not None else None
    if node.type == "qualified_name":
        return _simple_name(node.child_by_field_name("name"))
    return None


def _callee(call: Node) -> tuple[str | None, str | None, Node | None]:
    """呼び出しの (受け手の原文, 名前, 受け手の節点)。受け手の無い呼び出しの受け手は None。"""
    function = call.child_by_field_name("function")
    if function is None:
        return None, None, None
    if function.type in ("identifier", "generic_name"):
        return None, _simple_name(function), None
    if function.type == "member_access_expression":
        receiver = function.child_by_field_name("expression")
        name = _simple_name(function.child_by_field_name("name"))
        if receiver is not None and name is not None:
            return text_of(receiver), name, receiver
    return None, None, None


def _positional(call: Node) -> list[Node]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [a.named_children[-1] for a in arguments.named_children if a.named_children]


def _lambda_body(node: Node) -> Node | None:
    body = node.child_by_field_name("body")
    return body or next((c for c in node.children if c.type == "block"), None)


def is_async(member_node: Node) -> bool:
    return any(m.type == "modifier" and text_of(m) == "async" for m in member_node.children)


def _declared_bases(type_node: Node) -> list[str]:
    base_list = next((c for c in type_node.children if c.type == "base_list"), None)
    if base_list is None:
        return []
    names = (_simple_name(base) for base in base_list.named_children)
    return [name for name in names if name is not None]


def _is_task_factory(receiver: str | None) -> bool:
    return receiver is not None and _TASK_FACTORY.fullmatch(receiver) is not None


def _await(x: Extractor[None], node: Node) -> list[ConcurrencyStep]:
    inner = next((c for c in node.named_children if c.type == "invocation_expression"), None)
    # `await x.ConfigureAwait(false)` の待つ対象は x
    leaves_context = False
    while inner is not None and _callee(inner)[1] == "ConfigureAwait":
        arguments = _positional(inner)
        leaves_context = leaves_context or (
            len(arguments) == 1 and text_of(arguments[0]) == "false"
        )
        function = inner.child_by_field_name("function")
        receiver = function.child_by_field_name("expression") if function else None
        is_call = receiver is not None and receiver.type == "invocation_expression"
        inner = receiver if is_call else None
    steps = _call(x, inner, awaited=True) if inner is not None else x.walk(node)
    if leaves_context and is_ui_thread(x.domain):
        # UI スレッドの外は継続がもともと戻らないので、切り替えを出さない。
        # 以降の手順の領域を変えるのは、この本体の走査器だけ（枝や Task は別の走査器を持つ）
        x.domain = _THREAD_POOL
        steps.append(ContinueOn(_THREAD_POOL, "ConfigureAwait(false) の後の継続", line_of(node)))
    return steps


def _assignment(x: Extractor[None], node: Node) -> list[ConcurrencyStep]:
    steps = x.walk(node)
    left = node.child_by_field_name("left")
    if left is not None and left.type == "member_access_expression":
        receiver = left.child_by_field_name("expression")
        member = _simple_name(left.child_by_field_name("name"))
        if receiver is not None and member is not None:
            steps.append(MemberAssignment(receiver, member, line_of(node)))
    return steps


def _property(x: Extractor[None], node: Node) -> list[ConcurrencyStep]:
    steps = x.walk(node)
    name = node.child_by_field_name("name")
    parent = node.parent
    called = (
        parent is not None
        and parent.type == "invocation_expression"
        and parent.child_by_field_name("function") == node
    )
    receiver = node.child_by_field_name("expression")
    if name is not None and text_of(name) == "Result" and not called and receiver is not None:
        steps.append(BlockingWait(f"{text_of(receiver)}.Result", line_of(node)))
    return steps


def _call(x: Extractor[None], call: Node, *, awaited: bool) -> list[ConcurrencyStep]:
    receiver, name, receiver_node = _callee(call)
    special = _special(x, call, receiver, name, awaited=awaited)
    if special is not None:
        return special
    steps = x.walk(call)
    blocking = _blocking(receiver, name, receiver_node)
    if blocking is not None:
        steps.append(BlockingWait(blocking, line_of(call)))
        return steps
    site = x.sites.get(call.id)
    if site is None:
        return steps
    steps.append(AwaitCall(site, site.line) if awaited else SyncCall(site, site.line))
    return steps


def _blocking(receiver: str | None, name: str | None, receiver_node: Node | None) -> str | None:
    if receiver is None or name is None:
        return None
    if receiver == "Thread" and name == "Sleep":
        return "Thread.Sleep"
    if name in _WAIT_NAMES:
        return f"{receiver}.{name}"
    is_awaiter = (
        receiver_node is not None
        and receiver_node.type == "invocation_expression"
        and _callee(receiver_node)[1] == "GetAwaiter"
    )
    return f"{receiver}.GetResult" if name == "GetResult" and is_awaiter else None


def _body(x: Extractor[None], argument: Node, domain: Isolation) -> Body:
    return Body(domain, tuple(x.lambda_steps(argument, domain)))


def _branch(x: Extractor[None], argument: Node) -> Body:
    """Task.WhenAll に渡された呼び出し一つを、枝の本体にする。"""
    if argument.type != "invocation_expression":
        return Body(x.domain, ())
    site = x.sites.get(argument.id)
    # 枝ごとに走査器を分け、枝の中の ConfigureAwait(false) の切り替えを隣の枝へ残さない
    steps = x.derive(x.domain).walk(argument)
    if site is not None:
        steps.append(AwaitCall(site, site.line))
    return Body(x.domain, tuple(steps))


def _continuation(
    x: Extractor[None], arguments: list[Node], line: int, *, awaited: bool
) -> ConcurrencyStep:
    """`task.ContinueWith(lambda, scheduler)`。

    スケジューラが無ければ継続はスレッドプールで走る。
    """
    on_caller = any("FromCurrentSynchronizationContext" in text_of(a) for a in arguments[1:])
    domain = x.domain if on_caller else _THREAD_POOL
    body = _body(x, arguments[0], domain)
    if awaited:
        # 待たれる継続は、待たれる Task.Run と同じく投げ込みで描く
        return QueueDispatch(body, line, "ContinueWith")
    return UnstructuredTask(TaskKind.TASK, body, line)


def _is_thread_creation(node: Node) -> bool:
    return (
        node.type == "object_creation_expression"
        and _simple_name(node.child_by_field_name("type")) == "Thread"
    )


def _thread_creation(call: Node, receiver: Node) -> Node | None:
    """`.Start()` の受け手が `new Thread(...)` か、それを束縛した名前なら、その生成式。

    名前は同じ本体の中で束縛したものに限る。
    """
    if _is_thread_creation(receiver):
        return receiver
    if receiver.type not in ("identifier", "member_access_expression"):
        return None
    variable = text_of(receiver).removeprefix("this.")
    scope = call
    while scope.parent is not None and scope.type not in _MEMBER_BODIES:
        scope = scope.parent
    stack = [scope]
    while stack:
        current = stack.pop()
        stack.extend(reversed(current.children))
        if _is_thread_creation(current) and _delegate_variable(current) == variable:
            return current
    return None


def _thread_start(x: Extractor[None], creation: Node, line: int) -> ConcurrencyStep:
    """`new Thread(lambda).Start()`。ワーカーのスレッドは、スレッドプールと同じ領域で描く。"""
    arguments = _positional(creation)
    argument = arguments[0] if arguments else None
    while argument is not None and argument.type == "object_creation_expression":
        # `new ThreadStart(() => ...)` の包みを剥がす
        inner = _positional(argument)
        argument = inner[0] if inner else None
    # メソッド参照は呼び先を決められないので、本体は空
    body = _body(x, argument, _THREAD_POOL) if argument is not None else Body(_THREAD_POOL, ())
    return UnstructuredTask(TaskKind.TASK, body, line)


def _task_special(
    x: Extractor[None],
    receiver: str | None,
    name: str | None,
    arguments: list[Node],
    line: int,
    *,
    awaited: bool,
) -> list[ConcurrencyStep] | None:
    """Task の呼び出し（`WhenAll`、`WhenAny`、`Task.Run`、`StartNew`、`ContinueWith`）の段。"""
    if receiver in ("Task", "System.Threading.Tasks.Task") and name in ("WhenAll", "WhenAny"):
        kind = StructuredKind.JOIN_ALL if name == "WhenAll" else StructuredKind.RACE
        branches = tuple(_branch(x, a) for a in arguments)
        return [StructuredConcurrency(kind, branches, line, name)]
    is_pool = (name == "Run" and receiver == "Task") or (
        name == "StartNew" and _is_task_factory(receiver)
    )
    if is_pool and arguments and name is not None:
        body = _body(x, arguments[0], _THREAD_POOL)
        if awaited:
            # 待たれる Task.Run は取り消しが伝わるので、構造化されない Task にせず投げ込みで描く
            return [QueueDispatch(body, line, name)]
        return [UnstructuredTask(TaskKind.TASK, body, line)]
    if name == "ContinueWith" and arguments and receiver is not None:
        return [_continuation(x, arguments, line, awaited=awaited)]
    return None


def _thread_special(
    x: Extractor[None],
    call: Node,
    receiver: str | None,
    name: str | None,
    arguments: list[Node],
    *,
    line: int,
) -> list[ConcurrencyStep] | None:
    """スレッドと UI への投げ込み（`Thread.Start`、主スレッドの呼び出し、Dispatcher）の段。"""
    receiver_node = _callee(call)[2]
    if name == "Start" and receiver_node is not None:
        creation = _thread_creation(call, receiver_node)
        if creation is not None:
            return [_thread_start(x, creation, line)]
    if name in _MAIN_THREAD_NAMES and receiver == "MainThread" and arguments:
        return [QueueDispatch(_body(x, arguments[0], _UI_THREAD), line, str(name))]
    # lambda を渡さない `callback.Invoke(x)` はデリゲートの呼び出しなので、投げ込みにしない
    via_ui = receiver is not None and (
        "dispatcher" in receiver.lower() or (bool(arguments) and arguments[0].type in _LAMBDAS)
    )
    if name in _DISPATCH_NAMES and arguments and via_ui:
        return [QueueDispatch(_body(x, arguments[0], _UI_THREAD), line, str(name))]
    return None


def _special(
    x: Extractor[None], call: Node, receiver: str | None, name: str | None, *, awaited: bool
) -> list[ConcurrencyStep] | None:
    line = line_of(call)
    arguments = _positional(call)
    task_steps = _task_special(x, receiver, name, arguments, line, awaited=awaited)
    if task_steps is not None:
        return task_steps
    return _thread_special(x, call, receiver, name, arguments, line=line)


class _Rules(StatelessWalk):
    skipped = _SKIPPED
    type_declarations = _TYPE_DECLARATIONS

    def is_main_thread(self, domain: Isolation) -> bool:
        return is_ui_thread(domain)

    def is_ui_name(self, name: str, _scope: Node | None, /) -> bool:
        return name in _UI_TYPES

    def type_name(self, node: Node | None) -> str | None:
        return _simple_name(node)

    def declared_bases(self, type_node: Node) -> list[str]:
        return _declared_bases(type_node)

    def lambda_body(self, node: Node) -> Node | None:
        return _lambda_body(node) if node.type in _LAMBDAS else None

    def visit(self, extractor: Extractor[None], node: Node) -> list[ConcurrencyStep] | None:
        if node.type == "await_expression":
            return _await(extractor, node)
        if node.type == "invocation_expression":
            return _call(extractor, node, awaited=False)
        if node.type == "assignment_expression":
            return _assignment(extractor, node)
        if node.type == "member_access_expression":
            return _property(extractor, node)
        return None


_RULES = _Rules()


_WORKER_THREAD_TYPES = frozenset({"Thread", "ThreadStart", "ParameterizedThreadStart"})


def _is_worker_dispatch(call: Node) -> bool:
    """スレッドプールかワーカーのスレッドへ仕事を渡す呼び出し。"""
    receiver, name, _ = _callee(call)
    if name == "Run":
        return receiver == "Task"
    if name == "StartNew":
        return _is_task_factory(receiver)
    return receiver == "ThreadPool" and name == "QueueUserWorkItem"


def _enclosing_call(node: Node) -> Node | None:
    """引数として渡されたノードを受ける呼び出し。"""
    argument = node.parent
    arguments = argument.parent if argument is not None else None
    if argument is None or argument.type != "argument" or arguments is None:
        return None
    call = arguments.parent
    return call if call is not None and call.type == "invocation_expression" else None


def _delegate_variable(creation: Node) -> str | None:
    """`new D(M)` を受ける変数の名前。受ける変数が無ければ None。"""
    parent = creation.parent
    if parent is not None and parent.type == "equals_value_clause":
        parent = parent.parent
    if parent is not None and parent.type == "variable_declarator":
        name = parent.child_by_field_name("name")
        return text_of(name) if name is not None else None
    if parent is not None and parent.type == "assignment_expression":
        left = parent.child_by_field_name("left")
        return text_of(left).removeprefix("this.") if left is not None else None
    return None


def _delegate_begin_invoked(declaration: Node, variable: str) -> bool:
    """変数のデリゲートが BeginInvoke でだけ呼ばれ、同期の呼び出しは無い。"""
    begin = False
    stack = [declaration]
    while stack:
        current = stack.pop()
        stack.extend(current.children)
        if current.type != "invocation_expression":
            continue
        receiver, name, _ = _callee(current)
        function = current.child_by_field_name("function")
        if function is not None and text_of(function) == variable:
            return False
        if receiver is None or receiver.removeprefix("this.") != variable:
            continue
        if name == "BeginInvoke":
            begin = True
        elif name == "Invoke":
            return False
    return begin


def _is_invoked_in_worker_lambda(target: Node) -> bool:
    """`Method()` の呼び出しがラムダの中にあって、そのラムダがワーカーへ渡されているか。"""
    lambda_node = target.parent
    while lambda_node is not None and lambda_node.type not in _LAMBDAS | {"method_declaration"}:
        lambda_node = lambda_node.parent
    if lambda_node is None or lambda_node.type not in _LAMBDAS:
        return False
    call = _enclosing_call(lambda_node)
    return call is not None and _is_worker_dispatch(call)


def _is_worker_delegate_argument(target: Node, declaration: Node) -> bool:
    """メソッド名が `new Thread(Method)` や `new Handler(Method)` の引数で、ワーカーで走る形か。"""
    arguments = target.parent.parent if target.parent is not None else None
    creation = arguments.parent if arguments is not None else None
    if creation is None or creation.type != "object_creation_expression":
        return False
    if _simple_name(creation.child_by_field_name("type")) in _WORKER_THREAD_TYPES:
        return True
    variable = _delegate_variable(creation)
    return variable is not None and _delegate_begin_invoked(declaration, variable)


def _is_worker_reference(reference: Node, declaration: Node) -> bool:
    """メソッド名の参照が、ワーカーへ投げ込まれる形か。"""
    target = reference
    parent = target.parent
    if parent is not None and parent.type == "member_access_expression":
        owner = parent.child_by_field_name("expression")
        if owner is None or text_of(owner) != "this":
            return False
        target, parent = parent, parent.parent
    if parent is not None and parent.type == "invocation_expression":
        return _is_invoked_in_worker_lambda(target)
    call = _enclosing_call(target)
    if call is not None:
        return _is_worker_dispatch(call)
    return _is_worker_delegate_argument(target, declaration)


def _only_dispatched(method: Node, context: IsolationContext) -> bool:
    """同じ型の中でこのメソッドが、ワーカーへ投げ込まれる形でだけ参照されているか。

    参照が無い、外から呼ばれうる、UI の経路からも参照される、のときは偽にして型の所属に従わせる。
    """
    name_node = method.child_by_field_name("name")
    owner = method.parent
    while owner is not None and owner.type not in _TYPE_DECLARATIONS:
        owner = owner.parent
    if name_node is None or owner is None:
        return False
    name = text_of(name_node)
    declarations = [owner]
    owner_name = _simple_name(owner.child_by_field_name("name"))
    if owner_name is not None:
        # 部分クラスの別のファイルの宣言も、同じ型の参照として数える
        declarations.extend(s.node for s in context.type_syntaxes(owner_name) if s.node != owner)
    references, worker = 0, 0
    for declaration in declarations:
        stack = [declaration]
        while stack:
            current = stack.pop()
            stack.extend(current.children)
            if current.type != "identifier" or text_of(current) != name or current == name_node:
                continue
            references += 1
            worker += _is_worker_reference(current, declaration)
    return references > 0 and references == worker


def is_ui_type(type_name: str, context: IsolationContext, scope: Node | None = None) -> bool:
    """型自身か上位の型が WPF、WinForms、MAUI の UI の型なら真。上位は context で辿る。"""
    return core.is_ui_type(type_name, context, _RULES, scope)


def is_main_thread(domain: Isolation) -> bool:
    return core.is_main_thread(domain, _RULES)


def isolation_of(node: Node, context: IsolationContext) -> Isolation:
    domain = core.isolation_of(node, context, _RULES)
    is_method = node.type == "method_declaration"
    if domain == _UI_THREAD and is_method and _only_dispatched(node, context):
        return _THREAD_POOL
    return domain


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
