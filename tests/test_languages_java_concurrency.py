from collections import defaultdict

from tree_sitter import Node

from jigane.languages.base import (
    BlockingWait,
    ConcurrencyAdapter,
    ConcurrencyStep,
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
from jigane.languages.java import JavaAdapter

adapter = JavaAdapter()
UI = UiThreadDomain()
POOL = QueueDomain("スレッドプール")
_TYPES = {"class_declaration", "method_declaration"}


class Context:
    """索引の代わりの文脈。code にある型の継承元の綴りを、上位の型として返す。"""

    def __init__(self, *codes: str) -> None:
        self._bases: dict[str, list[str]] = defaultdict(list)
        for code in codes:
            stack = [parse(adapter, code.encode()).root_node]
            while stack:
                node = stack.pop()
                stack.extend(node.children)
                superclass = next((c for c in node.children if c.type == "superclass"), None)
                name = node.child_by_field_name("name")
                if node.type == "class_declaration" and name and superclass:
                    self._bases[(name.text or b"").decode()] = [
                        (c.text or b"").decode()
                        for c in superclass.named_children
                        if c.type == "type_identifier"
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
        if current.type in _TYPES and named and (named.text or b"") == name.encode():
            return current
        stack.extend(reversed(current.children))
    raise AssertionError(name)


def isolation(code: str, name: str, *others: str) -> Isolation:
    root = parse(adapter, code.encode()).root_node
    return adapter.isolation_of(find(root, name), code.encode(), Context(code, *others))


def steps(code: str, enclosing: Isolation | None = None) -> list[ConcurrencyStep]:
    source = f"class C {{\n void F() {{\n{code}\n}}\n}}\n".encode()
    root = parse(adapter, source).root_node
    body = adapter.body_of(find(root, "F"))
    assert body is not None
    return adapter.concurrency_steps(body, source, enclosing or NonisolatedDomain())


def only(found: list[ConcurrencyStep]) -> ConcurrencyStep:
    assert len(found) == 1
    return found[0]


def test_adapter_protocol() -> None:
    assert isinstance(adapter, ConcurrencyAdapter)


def test_swing_type_and_its_members_are_the_ui_thread() -> None:
    code = "class Main extends JFrame {\n    void m() { }\n}\n"
    assert isolation(code, "Main") == UI
    assert isolation(code, "m") == UI


def test_javafx_and_android_bases_are_ui_types_when_imported() -> None:
    packages = {
        "Application": "javafx.application",
        "Node": "javafx.scene",
        "Parent": "javafx.scene",
        "Activity": "android.app",
        "Fragment": "androidx.fragment.app",
        "View": "android.view",
    }
    for base, package in packages.items():
        code = f"import {package}.{base};\nclass X extends {base} {{ void m() {{ }} }}"
        assert isolation(code, "m") == UI, base


def test_wildcard_import_of_a_ui_package_confirms_the_name() -> None:
    for package in ("javafx.scene", "java.awt", "javax.swing"):
        code = f"import {package}.*;\nclass X extends Window {{ void m() {{ }} }}"
        assert isolation(code, "m") == UI, package


def test_fully_qualified_base_is_confirmed_without_an_import() -> None:
    code = "class X extends javafx.application.Application { void m() { } }"
    assert isolation(code, "m") == UI
    other = "class X extends com.google.common.Application { void m() { } }"
    assert isolation(other, "m") == NonisolatedDomain()


def test_common_names_without_a_ui_import_are_not_ui_types() -> None:
    for base in ("Application", "Window", "Node", "Container", "Panel", "View"):
        code = f"class X extends {base} {{ void m() {{ }} }}"
        assert isolation(code, "m") == NonisolatedDomain(), base
    unrelated = "import java.util.*;\nclass X extends Window { void m() { } }"
    assert isolation(unrelated, "m") == NonisolatedDomain()
    wrong_package = "import com.example.Window;\nclass X extends Window { void m() { } }"
    assert isolation(wrong_package, "m") == NonisolatedDomain()


def test_type_declared_in_the_same_file_is_not_a_ui_type() -> None:
    code = (
        "import javafx.scene.*;\n"
        "class Application { void m() { } }\n"
        "class Hook extends Application { void n() { } }\n"
    )
    assert isolation(code, "m") == NonisolatedDomain()
    assert isolation(code, "n") == NonisolatedDomain()


def test_swing_j_names_are_ui_types_without_an_import() -> None:
    assert isolation("class X extends JFrame { void m() { } }", "m") == UI


def test_subtype_of_an_indexed_type_reaches_the_external_ui_type() -> None:
    base = "class BaseScreen extends JPanel { }\n"
    code = "class Leaf extends BaseScreen {\n    void m() { }\n}\n"
    assert isolation(code, "m", base) == UI


def test_non_ui_type_is_nonisolated() -> None:
    code = "class Service extends Object implements Runnable {\n    void m() { }\n}\n"
    assert isolation(code, "Service") == NonisolatedDomain()
    assert isolation(code, "m") == NonisolatedDomain()


def test_ui_type_names_and_main_thread() -> None:
    context = Context("class Screen extends JDialog { }")
    scope = parse(
        adapter, b"import javafx.application.Application;\nimport android.app.Activity;"
    ).root_node
    for name in ("JComponent", "JTable", "Screen"):
        assert adapter.is_ui_type(name, context)
    for name in ("Application", "Activity"):
        assert adapter.is_ui_type(name, context, scope)
        assert not adapter.is_ui_type(name, context)
    assert not adapter.is_ui_type("Window", context, scope)
    assert not adapter.is_ui_type("JUnitRunner", context)
    assert not adapter.is_ui_type("Service", context)
    assert adapter.is_main_thread(UI)
    assert not adapter.is_main_thread(POOL)
    assert not adapter.is_main_thread(NonisolatedDomain())


def test_is_async_is_always_false() -> None:
    root = parse(adapter, b"class C { void a() {} }").root_node
    assert not adapter.is_async(find(root, "a"))


def test_plain_calls_are_sync_calls() -> None:
    found = steps("load();\nsave();")
    assert [type(s) for s in found] == [SyncCall, SyncCall]


def test_submit_and_execute_are_unstructured_tasks_on_the_pool() -> None:
    for code in ("executor.submit(() -> { work(); });", "executor.execute(() -> work());"):
        task = only(steps(code))
        assert isinstance(task, UnstructuredTask)
        assert task.kind == TaskKind.TASK
        assert task.body.isolation == POOL
        assert [s.call.name for s in task.body.steps if isinstance(s, SyncCall)] == ["work"]


def test_completable_future_async_factories_are_tasks() -> None:
    for code in (
        "CompletableFuture.supplyAsync(() -> compute());",
        "CompletableFuture.runAsync(() -> compute());",
    ):
        task = only(steps(code))
        assert isinstance(task, UnstructuredTask)
        assert task.body.isolation == POOL


def test_threads_are_tasks_on_the_pool() -> None:
    for code in (
        "new Thread(() -> work()).start();",
        "Thread.ofVirtual().start(() -> work());",
        "Thread.startVirtualThread(() -> work());",
    ):
        task = only(steps(code))
        assert isinstance(task, UnstructuredTask)
        assert task.body.isolation == POOL
        assert [s.call.name for s in task.body.steps if isinstance(s, SyncCall)] == ["work"]


def test_start_on_a_non_thread_is_not_a_task() -> None:
    found = steps("service.start();")
    assert [type(s) for s in found] == [SyncCall]


def test_invoke_all_and_all_of_join_all() -> None:
    invoke = only(steps("executor.invokeAll(List.of(() -> a(), () -> b()));"))
    assert isinstance(invoke, StructuredConcurrency)
    assert invoke.kind == StructuredKind.JOIN_ALL
    assert invoke.label == "invokeAll"
    assert [b.isolation for b in invoke.branches] == [POOL, POOL]
    joined = only(steps("CompletableFuture.allOf(load(), save());"))
    assert isinstance(joined, StructuredConcurrency)
    assert joined.kind == StructuredKind.JOIN_ALL
    assert joined.label == "allOf"
    assert len(joined.branches) == 2


def test_invoke_any_and_any_of_race() -> None:
    invoke = only(steps("executor.invokeAny(List.of(() -> a(), () -> b()));"))
    assert isinstance(invoke, StructuredConcurrency)
    assert invoke.kind == StructuredKind.RACE
    assert invoke.label == "invokeAny"
    raced = only(steps("CompletableFuture.anyOf(load(), save());"))
    assert isinstance(raced, StructuredConcurrency)
    assert raced.kind == StructuredKind.RACE
    assert raced.label == "anyOf"


def test_structured_task_scope_forks_are_one_scope() -> None:
    code = (
        "try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {\n"
        "    scope.fork(() -> a());\n"
        "    scope.fork(() -> b());\n"
        "    scope.join();\n"
        "}"
    )
    found = steps(code)
    scope = found[0]
    assert isinstance(scope, StructuredConcurrency)
    assert scope.kind == StructuredKind.SCOPE
    assert scope.label == "StructuredTaskScope.ShutdownOnFailure"
    assert [b.isolation for b in scope.branches] == [POOL, POOL]
    # fork の呼び出しは枝に取られ、通常の呼び出しとして二重に出ない
    assert [s.call.name for s in found if isinstance(s, SyncCall)] == ["join"]


def test_try_with_other_resources_is_not_a_scope() -> None:
    found = steps("try (var in = open()) { in.read(); }")
    assert not any(isinstance(s, StructuredConcurrency) for s in found)


def test_ui_thread_dispatches() -> None:
    for code, label in (
        ("SwingUtilities.invokeLater(() -> update());", "invokeLater"),
        ("EventQueue.invokeLater(() -> update());", "invokeLater"),
        ("Platform.runLater(() -> update());", "runLater"),
        ("runOnUiThread(() -> update());", "runOnUiThread"),
        ("view.post(() -> update());", "post"),
    ):
        dispatch = only(steps(code))
        assert isinstance(dispatch, QueueDispatch)
        assert dispatch.body.isolation == UI
        assert dispatch.label == label
        assert [s.call.name for s in dispatch.body.steps if isinstance(s, SyncCall)] == ["update"]


def test_invoke_later_on_other_receivers_is_a_plain_call() -> None:
    assert [type(s) for s in steps("scheduler.invokeLater(() -> update());")] == [SyncCall]


def test_blocking_waits_by_receiver() -> None:
    waits = steps(
        "Thread.sleep(10);\n"
        "future.get();\n"
        "result.future.get(1, unit);\n"
        "task.join();\n"
        "latch.await();\n"
        "semaphore.acquire();\n"
        "CompletableFuture.supplyAsync(() -> 1).join();"
    )
    names = [s.name for s in waits if isinstance(s, BlockingWait)]
    assert names == [
        "Thread.sleep",
        "future.get",
        "result.future.get",
        "task.join",
        "latch.await",
        "semaphore.acquire",
        "CompletableFuture.supplyAsync(…).join",
    ]


def test_calls_that_look_like_waits_are_not_waits() -> None:
    found = steps("map.get(key);\nnames.join();\nlist.get();\nqueue.acquire(1, 2);\nlock.await();")
    assert not any(isinstance(s, BlockingWait) for s in found)


def test_blocking_wait_inside_a_pool_task_is_not_reported() -> None:
    task = only(steps("executor.submit(() -> { Thread.sleep(10); future.get(); });"))
    assert isinstance(task, UnstructuredTask)
    assert not any(isinstance(s, BlockingWait) for s in task.body.steps)


def test_blocking_wait_inside_an_ui_dispatch_is_reported() -> None:
    dispatch = only(steps("SwingUtilities.invokeLater(() -> { Thread.sleep(10); });"))
    assert isinstance(dispatch, QueueDispatch)
    assert [type(s) for s in dispatch.body.steps] == [BlockingWait]


def test_member_assignment_is_a_step() -> None:
    found = steps("label.text = x;")
    assert [type(s) for s in found] == [MemberAssignment]
