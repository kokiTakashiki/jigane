from collections import defaultdict
from collections.abc import Sequence

from tree_sitter import Node

from jigane.languages.base import (
    AwaitCall,
    BlockingWait,
    ConcurrencyAdapter,
    ConcurrencyStep,
    ContinueOn,
    Isolation,
    MemberAssignment,
    NonisolatedDomain,
    QueueDispatch,
    QueueDomain,
    StructuredConcurrency,
    StructuredKind,
    SyncCall,
    TaskKind,
    TypeSyntax,
    UiThreadDomain,
    UnstructuredTask,
    parse,
)
from jigane.languages.csharp import CSharpAdapter

adapter = CSharpAdapter()
UI = UiThreadDomain()
POOL = QueueDomain("スレッドプール")


class Context:
    """索引の代わりの文脈。code にある型の基底の型の綴りを、上位の型として返す。"""

    def __init__(self, *codes: str) -> None:
        self._bases: dict[str, list[str]] = defaultdict(list)
        for code in codes:
            root = parse(adapter, code.encode()).root_node
            stack = [root]
            while stack:
                node = stack.pop()
                stack.extend(node.children)
                if node.type == "class_declaration":
                    name = node.child_by_field_name("name")
                    base_list = next((c for c in node.children if c.type == "base_list"), None)
                    if name is not None and base_list is not None:
                        self._bases[(name.text or b"").decode()] = [
                            (b.text or b"").decode() for b in base_list.named_children
                        ]

    def type_syntaxes(self, _type_name: str) -> list[TypeSyntax]:
        return []

    def supertype_names(self, type_name: str) -> list[str]:
        return self._bases.get(type_name, [])


def find(node: Node, name: str) -> Node:
    stack = [node]
    while stack:
        current = stack.pop()
        named = current.child_by_field_name("name")
        declared = current.type in {"class_declaration", "method_declaration"}
        if declared and named and (named.text or b"") == name.encode():
            return current
        stack.extend(reversed(current.children))
    raise AssertionError(name)


def isolation(code: str, name: str, *others: str) -> Isolation:
    root = parse(adapter, code.encode()).root_node
    return adapter.isolation_of(find(root, name), code.encode(), Context(code, *others))


def steps(code: str, enclosing: Isolation | None = None) -> list[ConcurrencyStep]:
    source = f"class C {{\n async Task F() {{\n{code}\n}}\n}}\n".encode()
    root = parse(adapter, source).root_node
    body = adapter.body_of(find(root, "F"))
    assert body is not None
    return adapter.concurrency_steps(body, source, enclosing or NonisolatedDomain())


def test_adapter_protocol() -> None:
    assert isinstance(adapter, ConcurrencyAdapter)
    # 口を持たない言語のアダプタは満たさない
    assert not isinstance(object(), ConcurrencyAdapter)


def test_external_ui_base_type_makes_the_type_and_its_members_the_ui_thread() -> None:
    code = "class Main : Window {\n    void M() { }\n}\n"
    assert isolation(code, "Main") == UI
    assert isolation(code, "M") == UI


def test_subtype_of_an_indexed_type_reaches_the_external_ui_type() -> None:
    base = "class BaseView : UserControl { }\n"
    code = "class Leaf : BaseView {\n    void M() { }\n}\n"
    assert isolation(code, "M", base) == UI


def test_non_ui_type_is_nonisolated() -> None:
    code = "class Service : IDisposable {\n    void M() { }\n}\n"
    assert isolation(code, "Service") == NonisolatedDomain()
    assert isolation(code, "M") == NonisolatedDomain()


def test_ui_type_names_and_main_thread() -> None:
    context = Context("class Page2 : ContentPage { }")
    assert adapter.is_ui_type("Form", context)
    assert adapter.is_ui_type("Page2", context)
    assert not adapter.is_ui_type("Service", context)
    assert adapter.is_main_thread(UI)
    assert not adapter.is_main_thread(POOL)
    assert not adapter.is_main_thread(NonisolatedDomain())


def test_is_async() -> None:
    root = parse(adapter, b"class C { async Task A() {} void B() {} }").root_node
    assert adapter.is_async(find(root, "A"))
    assert not adapter.is_async(find(root, "B"))


def test_await_and_plain_calls() -> None:
    found = steps("await Load();\nSave();")
    assert [type(s) for s in found] == [AwaitCall, SyncCall]
    assert [s.call.name for s in found if isinstance(s, AwaitCall | SyncCall)] == ["Load", "Save"]


def test_configure_await_awaits_the_wrapped_call() -> None:
    found = steps("await Load().ConfigureAwait(false);")
    assert [type(s) for s in found] == [AwaitCall]
    assert isinstance(found[0], AwaitCall)
    assert found[0].call.name == "Load"


def test_task_run_without_await_is_an_unstructured_task_in_the_thread_pool() -> None:
    found = steps("Task.Run(() => Work());")
    assert len(found) == 1
    task = found[0]
    assert isinstance(task, UnstructuredTask)
    assert task.kind == TaskKind.TASK
    assert task.body.isolation == POOL
    assert [type(s) for s in task.body.steps] == [SyncCall]


def test_awaited_task_run_and_task_factory_start_new() -> None:
    awaited = steps("await Task.Run(() => { Work(); });")
    assert isinstance(awaited[0], QueueDispatch)
    assert awaited[0].body.isolation == POOL
    started = steps("Task.Factory.StartNew(() => Work());")
    assert isinstance(started[0], UnstructuredTask)


def test_continue_with_is_an_unstructured_task_in_the_pool_unless_given_the_ui_scheduler() -> None:
    plain = steps("Task.Delay(5).ContinueWith(_ => Work());")
    assert len(plain) == 1
    assert isinstance(plain[0], UnstructuredTask)
    assert plain[0].body.isolation == POOL
    assert [type(s) for s in plain[0].body.steps] == [SyncCall]
    scheduled = steps(
        "task.ContinueWith(t => Work(), TaskScheduler.FromCurrentSynchronizationContext());",
        UI,
    )
    assert isinstance(scheduled[0], UnstructuredTask)
    assert scheduled[0].body.isolation == UI
    awaited = steps("await task.ContinueWith(t => Work());")
    assert isinstance(awaited[0], QueueDispatch)
    assert awaited[0].body.isolation == POOL


def test_new_thread_start_is_an_unstructured_task_in_the_pool() -> None:
    for use in (
        "new Thread(() => Work()).Start();",
        "new Thread(new ThreadStart(() => { Work(); })).Start();",
        "var t = new Thread(() => Work()); t.Start();",
        "Thread t; t = new Thread(() => Work()); t.Start();",
    ):
        found = steps(use)
        tasks = [s for s in found if isinstance(s, UnstructuredTask)]
        assert len(tasks) == 1, use
        assert tasks[0].kind == TaskKind.TASK
        assert tasks[0].body.isolation == POOL
        assert [type(s) for s in tasks[0].body.steps] == [SyncCall], use
    by_reference = steps("new Thread(Fetch).Start();")
    assert isinstance(by_reference[0], UnstructuredTask)
    assert by_reference[0].body.steps == ()
    assert not any(isinstance(s, UnstructuredTask) for s in steps("other.Start();"))


def test_when_all_and_when_any_are_structured_with_one_call_per_branch() -> None:
    for name, kind in (("WhenAll", StructuredKind.JOIN_ALL), ("WhenAny", StructuredKind.RACE)):
        found = steps(f"await Task.{name}(A(), B());")
        assert len(found) == 1
        group = found[0]
        assert isinstance(group, StructuredConcurrency)
        assert group.kind == kind
        assert len(group.branches) == 2
        assert [b.steps[0].call.name for b in group.branches] == ["A", "B"]  # type: ignore[union-attr]
        assert all(b.isolation == NonisolatedDomain() for b in group.branches)


def test_dispatcher_and_main_thread_hops_are_queue_dispatches_to_the_ui_thread() -> None:
    code = (
        'Dispatcher.Invoke(() => { label.Text = "x"; });\n'
        "Application.Current.Dispatcher.BeginInvoke(() => Refresh());\n"
        "this.Invoke(() => Refresh());\n"
        "MainThread.BeginInvokeOnMainThread(() => Refresh());"
    )
    found = steps(code)
    assert all(isinstance(s, QueueDispatch) and s.body.isolation == UI for s in found)
    assert [s.label for s in found if isinstance(s, QueueDispatch)] == [
        "Invoke",
        "BeginInvoke",
        "Invoke",
        "BeginInvokeOnMainThread",
    ]
    first = found[0]
    assert isinstance(first, QueueDispatch)
    assert [type(s) for s in first.body.steps] == [MemberAssignment]


def test_blocking_waits() -> None:
    code = "var r = t.Result;\nt.Wait();\nt.GetAwaiter().GetResult();\nThread.Sleep(5);"
    names = [s.name for s in steps(code) if isinstance(s, BlockingWait)]
    assert names == ["t.Result", "t.Wait", "t.GetAwaiter().GetResult", "Thread.Sleep"]


def test_delegate_invoke_without_a_lambda_is_not_a_dispatch() -> None:
    found = steps("callback.Invoke(1);")
    assert [type(s) for s in found] == [SyncCall]


def method_steps(code: str, name: str, *others: str) -> list[ConcurrencyStep]:
    source = code.encode()
    root = parse(adapter, source).root_node
    method = find(root, name)
    body = adapter.body_of(method)
    assert body is not None
    enclosing = adapter.isolation_of(method, source, Context(code, *others))
    return adapter.concurrency_steps(body, source, enclosing)


def continue_steps(found: Sequence[ConcurrencyStep]) -> list[ContinueOn]:
    return [s for s in found if isinstance(s, ContinueOn)]


UI_CLASS = (
    "class MainView : Window\n{\n"
    "    async Task F()\n    {\n"
    "        await repo.Load().ConfigureAwait(false);\n"
    '        label.Text = "after";\n'
    "    }\n}\n"
)


def test_configure_await_false_on_the_ui_thread_continues_in_the_thread_pool() -> None:
    found = method_steps(UI_CLASS, "F")
    assert continue_steps(found) == [ContinueOn(POOL, "ConfigureAwait(false) の後の継続", 5)]
    # 切り替えは待ちの直後に置かれ、以降の代入より前に並ぶ
    kinds = [type(s) for s in found]
    assert kinds.index(AwaitCall) < kinds.index(ContinueOn) < kinds.index(MemberAssignment)


def test_no_continuation_switch_outside_the_ui_thread_or_without_configure_await_false() -> None:
    def body(await_line: str, header: str) -> str:
        return f"{header}\n{{\n    async Task F()\n    {{\n        {await_line}\n    }}\n}}\n"

    non_ui = method_steps(body("await repo.Load().ConfigureAwait(false);", "class V"), "F")
    assert continue_steps(non_ui) == []
    keeps_context = method_steps(
        body("await repo.Load().ConfigureAwait(true);", "class V : Window"), "F"
    )
    assert continue_steps(keeps_context) == []
    plain = method_steps(body("await repo.Load();", "class V : Window"), "F")
    assert continue_steps(plain) == []


def test_switch_inside_a_dispatcher_lambda_does_not_leak_to_the_outer_steps() -> None:
    code = (
        "class MainView : Window\n{\n    async Task F()\n    {\n"
        "        Dispatcher.Invoke(async () => { await repo.Load().ConfigureAwait(false); });\n"
        '        label.Text = "outer";\n'
        "    }\n}\n"
    )
    found = method_steps(code, "F")
    assert continue_steps(found) == []
    dispatch = next(s for s in found if isinstance(s, QueueDispatch))
    assert len(continue_steps(dispatch.body.steps)) == 1


def test_switch_inside_one_when_all_branch_does_not_reach_the_next_branch() -> None:
    code = (
        "class MainView : Window\n{\n    async Task F()\n    {\n"
        "        await Task.WhenAll(Foo(await repo.Load().ConfigureAwait(false)), Bar());\n"
        "    }\n}\n"
    )
    found = method_steps(code, "F")
    assert continue_steps(found) == []
    group = next(s for s in found if isinstance(s, StructuredConcurrency))
    first, second = group.branches
    assert len(continue_steps(first.steps)) == 1
    assert second.isolation == first.isolation == UiThreadDomain()


def window(*members: str) -> str:
    return "class MainView : Window\n{\n" + "\n".join(members) + "\n}\n"


FETCH = "    void Fetch() { Thread.Sleep(5); }"


def test_method_only_dispatched_with_a_delegate_begin_invoke_is_not_the_ui_thread() -> None:
    code = window(
        FETCH,
        "    void Start() {",
        "        fetcher = new NoArgDelegate(this.Fetch); fetcher.BeginInvoke(null, null);",
        "    }",
    )
    assert isolation(code, "Fetch") == POOL
    assert isolation(code, "Start") == UI
    names = [s.name for s in method_steps(code, "Fetch") if isinstance(s, BlockingWait)]
    assert names == ["Thread.Sleep"]


def test_delegate_variable_declared_with_var_is_read() -> None:
    code = window(
        FETCH,
        "    void Start() {",
        "        var fetcher = new NoArgDelegate(Fetch);",
        "        fetcher.BeginInvoke(null, null);",
        "    }",
    )
    assert isolation(code, "Fetch") == POOL


def test_delegate_called_synchronously_keeps_the_ui_thread() -> None:
    code = window(
        FETCH,
        "    void Start() {",
        "        var d = new NoArgDelegate(Fetch); d.BeginInvoke(null, null); d.Invoke();",
        "    }",
    )
    assert isolation(code, "Fetch") == UI


def test_method_also_referenced_from_the_ui_path_stays_the_ui_thread() -> None:
    for ui_use in (
        "void OnClick(object s, EventArgs e) { Fetch(); }",
        "void Hook() { Dispatcher.Invoke(() => { Fetch(); }); }",
        "void Wire() { button.Click += Fetch; }",
    ):
        code = window(FETCH, "    void Pool() { Task.Run(() => Fetch()); }", "    " + ui_use)
        assert isolation(code, "Fetch") == UI


def test_task_run_and_thread_pool_and_thread_forms_are_dispatched_references() -> None:
    for use in (
        "Task.Run(Fetch);",
        "Task.Run(() => Fetch());",
        "Task.Run(() => { this.Fetch(); });",
        "Task.Factory.StartNew(Fetch);",
        "ThreadPool.QueueUserWorkItem(_ => Fetch());",
        "new Thread(Fetch).Start();",
        "new Thread(new ThreadStart(Fetch)).Start();",
    ):
        assert isolation(window(FETCH, f"    void Start() {{ {use} }}"), "Fetch") == POOL, use


def test_unreferenced_or_externally_called_method_follows_the_type() -> None:
    assert isolation(window(FETCH), "Fetch") == UI
    assert isolation(window(FETCH, "    void Start() { other.Fetch(); }"), "Fetch") == UI
