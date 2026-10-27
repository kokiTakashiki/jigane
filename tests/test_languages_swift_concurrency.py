from collections import defaultdict

from tree_sitter import Node

from jigane.languages.base import (
    ActorDomain,
    AwaitCall,
    BlockingWait,
    ConcurrencyAdapter,
    ConcurrencyStep,
    GlobalActorDomain,
    Isolation,
    MemberAssignment,
    NonisolatedDomain,
    QueueDispatch,
    QueueDomain,
    Sharing,
    StructuredConcurrency,
    StructuredKind,
    SyncCall,
    TaskKind,
    TypeDecl,
    TypeSyntax,
    UnstructuredTask,
    parse,
)
from jigane.languages.swift import SwiftAdapter

adapter = SwiftAdapter()


def root_of(code: str) -> tuple[Node, bytes]:
    source = code.encode()
    return parse(adapter, source).root_node, source


def find(node: Node, types: set[str], name: str) -> Node:
    stack = [node]
    while stack:
        current = stack.pop()
        named = current.child_by_field_name("name")
        if current.type in types and named is not None and (named.text or b"").decode() == name:
            return current
        stack.extend(reversed(current.children))
    raise AssertionError(name)


class FilesContext:
    """複数のソースの型の宣言と拡張を単純名で引く文脈。ファイルごとのバイト列を宣言と組にする。"""

    def __init__(self, sources: list[str]) -> None:
        self.syntaxes: dict[str, list[TypeSyntax]] = defaultdict(list)
        self.bases: dict[str, list[str]] = defaultdict(list)
        for code in sources:
            root, source = root_of(code)
            for decl in adapter.type_members(root, source, "f.swift"):
                if isinstance(decl, TypeDecl):
                    self.syntaxes[decl.name].append(TypeSyntax(decl.node, source))
                    self.bases[decl.name].extend(adapter.simple_type_name(b) for b in decl.bases)

    def type_syntaxes(self, type_name: str) -> list[TypeSyntax]:
        return self.syntaxes.get(type_name, [])

    def supertype_names(self, type_name: str) -> list[str]:
        return self.bases.get(type_name, [])


def isolation(
    code: str, name: str, others: tuple[str, ...] = (), *, indexed: bool = True
) -> Isolation:
    """code の宣言 name の領域。

    others は文脈にだけ入るほかのファイル。
    indexed が偽なら文脈は空。
    """
    root, source = root_of(code)
    node = find(root, {"class_declaration", "function_declaration"}, name)
    context = FilesContext([code, *others] if indexed else [])
    return adapter.isolation_of(node, source, context)


def steps(code: str, enclosing: Isolation | None = None) -> list[ConcurrencyStep]:
    root, source = root_of(f"func f() async {{\n{code}\n}}\n")
    body = adapter.body_of(find(root, {"function_declaration"}, "f"))
    assert body is not None
    return adapter.concurrency_steps(body, source, enclosing or NonisolatedDomain())


def test_adapter_protocol() -> None:
    assert isinstance(adapter, ConcurrencyAdapter)
    # 口を持たない言語のアダプタは満たさない
    assert not isinstance(object(), ConcurrencyAdapter)


def test_actor_and_its_members() -> None:
    code = "actor X {\n    func m() {}\n    nonisolated func n() {}\n}\n"
    assert isolation(code, "X") == ActorDomain("X")
    assert isolation(code, "m") == ActorDomain("X")
    assert isolation(code, "n") == NonisolatedDomain()


def test_main_actor_type_and_function() -> None:
    code = (
        "@MainActor final class V {\n    func a() {}\n}\n"
        "class W {\n    @MainActor func b() {}\n    func c() {}\n}\n"
    )
    assert isolation(code, "V") == GlobalActorDomain("MainActor")
    assert isolation(code, "a") == GlobalActorDomain("MainActor")
    assert isolation(code, "b") == GlobalActorDomain("MainActor")
    assert isolation(code, "c") == NonisolatedDomain()
    assert isolation(code, "W") == NonisolatedDomain()


def test_global_actor() -> None:
    code = "@DatabaseActor class D {}\n@DatabaseActor func h() {}\n"
    assert isolation(code, "D") == GlobalActorDomain("DatabaseActor")
    assert isolation(code, "h") == GlobalActorDomain("DatabaseActor")


def test_extension_in_another_file_inherits_the_type_annotation() -> None:
    declared = "@MainActor final class V {}\n"
    extension = "extension V {\n    func e() {}\n}\n"
    assert isolation(extension, "e", (declared,)) == GlobalActorDomain("MainActor")


def test_extension_annotation_applies_to_its_own_members_only() -> None:
    declared = "final class V {\n    func plain() {}\n}\n"
    annotated = "@MainActor extension V {\n    func e() {}\n}\n"
    other = "extension V {\n    func o() {}\n}\n"
    assert isolation(annotated, "e", (declared, other)) == GlobalActorDomain("MainActor")
    assert isolation(other, "o", (declared, annotated)) == NonisolatedDomain()
    assert isolation(declared, "plain", (annotated, other)) == NonisolatedDomain()


def test_subclass_inherits_the_global_actor_of_the_superclass() -> None:
    base = "@MainActor class Base {}\n"
    sub = "class Sub: Base {\n    func s() {}\n}\n"
    grand = "class Grand: Sub {\n    func g() {}\n}\n"
    assert isolation(sub, "s", (base,)) == GlobalActorDomain("MainActor")
    assert isolation(grand, "g", (base, sub)) == GlobalActorDomain("MainActor")


def test_conformance_to_a_global_actor_protocol_is_inherited() -> None:
    protocol = "@MainActor protocol P {}\n"
    conforming = "final class C: P {\n    func c() {}\n}\n"
    assert isolation(conforming, "c", (protocol,)) == GlobalActorDomain("MainActor")
    plain = "protocol Q {}\nfinal class D: Q {\n    func d() {}\n}\n"
    assert isolation(plain, "d") == NonisolatedDomain()


def test_external_uikit_and_swiftui_supertypes_give_the_main_actor() -> None:
    main = GlobalActorDomain("MainActor")
    vc = "class Screen: UIViewController {\n    func s() {}\n}\n"
    sub = "class Detail: Screen {\n    func d() {}\n}\n"
    assert isolation(vc, "s") == main
    assert isolation(sub, "d", (vc,)) == main
    view = "final class Card: UIView {\n    func c() {}\n}\n"
    assert isolation(view, "c") == main
    swiftui = "struct Row: View {\n    func r() {}\n}\n"
    assert isolation(swiftui, "Row") == main
    delegate = "final class Source: NSObject, UITableViewDelegate {\n    func t() {}\n}\n"
    assert isolation(delegate, "t") == main


def test_unrelated_external_supertypes_do_not_give_the_main_actor() -> None:
    plain = "final class Model: NSObject, Codable {\n    func m() {}\n}\n"
    assert isolation(plain, "m") == NonisolatedDomain()
    near = "final class Odd: UIkitLike, Viewer {\n    func o() {}\n}\n"
    assert isolation(near, "o") == NonisolatedDomain()


def test_ui_types_by_name_and_by_supertype() -> None:
    context = FilesContext(
        [
            "class Card: UIView {}\n",
            "struct Row: View {}\n",
            "class Cell: Card {}\n",
            "class Model: NSObject {}\n",
            "struct Root: App {}\n",
        ]
    )
    for name in ("UILabel", "View", "Card", "Row", "Cell"):
        assert adapter.is_ui_type(name, context), name
    for name in ("Model", "Root", "Unknown", "UIkitLike"):
        assert not adapter.is_ui_type(name, context), name


def test_main_thread_is_main_actor_or_main_queue() -> None:
    assert adapter.is_main_thread(GlobalActorDomain("MainActor"))
    assert adapter.is_main_thread(QueueDomain("DispatchQueue.main"))
    assert adapter.is_main_thread(QueueDomain("main"))
    assert not adapter.is_main_thread(QueueDomain("DispatchQueue.global()"))
    assert not adapter.is_main_thread(GlobalActorDomain("OtherActor"))
    assert not adapter.is_main_thread(NonisolatedDomain())


def test_type_outside_the_context_is_nonisolated() -> None:
    declared = "@MainActor final class V {}\n"
    extension = "extension V {\n    func e() {}\n}\n"
    assert isolation(extension, "e", (declared,), indexed=False) == NonisolatedDomain()
    sub = "class Sub: Unknown {\n    func s() {}\n}\n"
    assert isolation(sub, "s") == NonisolatedDomain()


def test_is_async() -> None:
    root, _ = root_of("func a() async {}\nfunc b() {}\n")
    assert adapter.is_async(find(root, {"function_declaration"}, "a"))
    assert not adapter.is_async(find(root, {"function_declaration"}, "b"))


def test_await_calls_in_evaluation_order() -> None:
    found = steps("let r = await foo(await bar())\nawait baz()")
    names = [s.call.name for s in found if isinstance(s, AwaitCall)]
    assert names == ["bar", "foo", "baz"]
    assert isinstance(found[0], AwaitCall)
    assert found[0].line == 2


def test_consecutive_async_let_is_one_group() -> None:
    found = steps(
        "async let a = x()\n// c\nasync let b = await y()\nlet r = await z()\nasync let c = w()\n"
    )
    assert len(found) == 3
    first = found[0]
    assert isinstance(first, StructuredConcurrency)
    assert first.kind == StructuredKind.JOIN_ALL
    assert len(first.branches) == 2
    branch = first.branches[0].steps[0]
    assert isinstance(branch, AwaitCall)
    assert branch.call.name == "x"
    assert isinstance(found[1], AwaitCall)
    assert isinstance(found[2], StructuredConcurrency)
    assert len(found[2].branches) == 1


def test_task_group_branches() -> None:
    found = steps(
        "await withTaskGroup(of: Int.self) { group in\n"
        "    group.addTask { await p() }\n"
        "    group.addTask { await q(); await r() }\n}\n"
        "try await withThrowingTaskGroup(of: Int.self) { g in g.addTask { await s() } }"
    )
    assert len(found) == 2
    first, second = found
    assert isinstance(first, StructuredConcurrency)
    assert first.kind == StructuredKind.SCOPE
    assert [len(b.steps) for b in first.branches] == [1, 2]
    assert isinstance(second, StructuredConcurrency)
    assert len(second.branches) == 1


def test_task_and_detached() -> None:
    found = steps("Task { await w() }\nTask.detached(priority: .low) { sleep(1) }")
    first, second = found
    assert isinstance(first, UnstructuredTask)
    assert first.kind == TaskKind.TASK
    assert isinstance(first.body.steps[0], AwaitCall)
    assert isinstance(second, UnstructuredTask)
    assert second.kind == TaskKind.DETACHED
    assert second.body.steps == (BlockingWait("sleep", 3),)


def test_dispatch_queues() -> None:
    found = steps(
        "DispatchQueue.main.async { u() }\n"
        "DispatchQueue.global().async { sleep(1) }\nqueue.async { }"
    )
    queues = [s.body.isolation for s in found if isinstance(s, QueueDispatch)]
    assert queues == [
        QueueDomain("DispatchQueue.main"),
        QueueDomain("DispatchQueue.global()"),
        QueueDomain("queue"),
    ]


def test_blocking_waits() -> None:
    found = steps(
        "let gate = DispatchSemaphore(value: 0)\ngate.wait()\nloadSemaphore.wait()\n"
        "other.wait()\nThread.sleep(forTimeInterval: 1)\nusleep(5)\nsleep(1)\nfoo.sleep(1)"
    )
    assert [s.name for s in found if isinstance(s, BlockingWait)] == [
        "gate.wait",
        "loadSemaphore.wait",
        "Thread.sleep",
        "usleep",
        "sleep",
    ]


def test_nesting() -> None:
    found = steps(
        "Task {\n    DispatchQueue.main.async {\n        await w()\n    }\n    semaphore.wait()\n}"
    )
    task = found[0]
    assert isinstance(task, UnstructuredTask)
    queue, wait = task.body.steps
    assert isinstance(queue, QueueDispatch)
    assert isinstance(queue.body.steps[0], AwaitCall)
    assert isinstance(wait, BlockingWait)


MAIN = GlobalActorDomain("MainActor")


def test_task_inherits_the_enclosing_isolation() -> None:
    found = steps("Task { await w() }", enclosing=MAIN)
    assert isinstance(found[0], UnstructuredTask)
    assert found[0].body.isolation == MAIN


def test_nested_task_inherits_the_closure_annotation() -> None:
    found = steps("Task { @DatabaseActor in\n    Task { await w() }\n}")
    outer = found[0]
    assert isinstance(outer, UnstructuredTask)
    inner = outer.body.steps[0]
    assert isinstance(inner, UnstructuredTask)
    assert outer.body.isolation == GlobalActorDomain("DatabaseActor")
    assert inner.body.isolation == GlobalActorDomain("DatabaseActor")


def test_detached_is_nonisolated_even_inside_main_actor() -> None:
    found = steps("Task.detached { await w() }", enclosing=MAIN)
    assert isinstance(found[0], UnstructuredTask)
    assert found[0].body.isolation == NonisolatedDomain()


def test_closure_annotation_overrides_the_inheritance() -> None:
    found = steps(
        "Task { @MainActor in await a() }\nTask.detached { @DatabaseActor in await b() }",
        enclosing=ActorDomain("X"),
    )
    first, second = found
    assert isinstance(first, UnstructuredTask)
    assert first.body.isolation == MAIN
    assert isinstance(second, UnstructuredTask)
    assert second.body.isolation == GlobalActorDomain("DatabaseActor")


def test_async_let_and_task_group_children_are_nonisolated() -> None:
    found = steps(
        "async let a = x()\n"
        "await withTaskGroup(of: Int.self) { g in\n"
        "    g.addTask { await p() }\n"
        "    g.addTask { @MainActor in await q() }\n}",
        enclosing=MAIN,
    )
    async_let, group = found
    assert isinstance(async_let, StructuredConcurrency)
    assert [b.isolation for b in async_let.branches] == [NonisolatedDomain()]
    assert isinstance(group, StructuredConcurrency)
    assert [b.isolation for b in group.branches] == [NonisolatedDomain(), MAIN]


def test_main_actor_has_one_spelling() -> None:
    code = "@MainActor func a() {}\n@MainActor final class V {\n    func b() {}\n}\n"
    assert isolation(code, "a") == isolation(code, "b") == MAIN
    assert isolation(code, "a") == GlobalActorDomain("MainActor")
    assert isolation(code, "a") != NonisolatedDomain()


def test_default_isolation_is_an_explicit_value() -> None:
    assert isolation("func f() {}\n", "f") == NonisolatedDomain()


def _steps(code: str) -> list[ConcurrencyStep]:
    root, source = root_of(code)
    body = find(root, {"function_declaration"}, "f").child_by_field_name("body")
    assert body is not None
    return adapter.concurrency_steps(body, source, NonisolatedDomain())


def test_sync_calls_and_member_assignments_become_steps() -> None:
    steps = _steps("func f() {\n    foo(bar())\n    label.text = baz()\n}\n")
    assert [type(s).__name__ for s in steps] == [
        "SyncCall",
        "SyncCall",
        "SyncCall",
        "MemberAssignment",
    ]
    assert [s.call.name for s in steps if isinstance(s, SyncCall)] == ["bar", "foo", "baz"]
    assignment = steps[3]
    assert isinstance(assignment, MemberAssignment)
    assert (assignment.member, (assignment.receiver.text or b"").decode()) == ("text", "label")


def test_calls_under_await_stay_await_calls() -> None:
    steps = _steps("func f() async {\n    let x = await foo(bar())\n}\n")
    assert len(steps) == 2
    assert all(isinstance(s, AwaitCall) for s in steps)


def test_main_actor_run_closure_is_not_counted_as_sync() -> None:
    steps = _steps("func f() async {\n    await MainActor.run { label.text = x }\n}\n")
    assert [type(s).__name__ for s in steps] == ["AwaitCall"]


def sharing(name: str, *sources: str) -> Sharing:
    return adapter.sharing_of(name, FilesContext(list(sources)))


def test_sharing_of_a_type_missing_from_the_index_is_unknown() -> None:
    assert sharing("Missing", "class Other {}") == Sharing.UNKNOWN
    assert sharing("Ext", "extension Ext {}") == Sharing.UNKNOWN


def test_sharing_of_a_non_class_declaration() -> None:
    assert sharing("P", "protocol P {}") == Sharing.UNKNOWN
    assert sharing("S", "struct S { var x = 0 }") == Sharing.SAFE
    assert sharing("E", "enum E { case a }") == Sharing.SAFE
    assert sharing("A", "actor A { var x = 0 }") == Sharing.SAFE


def test_sharing_of_a_global_actor_class_is_safe() -> None:
    assert sharing("C", "@MainActor class C { var x = 0 }") == Sharing.SAFE


def test_sharing_of_a_sendable_class() -> None:
    assert sharing("C", "final class C: Sendable { let x = 0 }") == Sharing.SAFE
    assert sharing("D", "class B: Sendable {}", "class D: B { var x = 0 }") == Sharing.SAFE


def test_sharing_of_a_class_with_an_unresolved_superclass_is_unknown() -> None:
    assert sharing("C", "class C: Unseen { var x = 0 }") == Sharing.UNKNOWN


def test_sharing_of_a_class_without_sendable_depends_on_stored_vars() -> None:
    assert sharing("C", "class C { var x = 0 }") == Sharing.MUTABLE
    assert sharing("C", "class C { let x = 0 }") == Sharing.NOT_SENDABLE
    assert sharing("C", "class C { var x: Int { 0 } }") == Sharing.NOT_SENDABLE
    assert sharing("C", "class C { func f() {} }") == Sharing.NOT_SENDABLE


def test_sharing_of_an_unchecked_sendable_class_trusts_the_author_unless_it_has_vars() -> None:
    assert sharing("C", "class C: @unchecked Sendable { let x = 0 }") == Sharing.SAFE
    assert sharing("C", "class C: @unchecked Sendable { var x = 0 }") == Sharing.MUTABLE
