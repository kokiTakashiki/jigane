from tree_sitter import Node

from jigane.languages.base import (
    AwaitCall,
    BlockingWait,
    Body,
    ConcurrencyAdapter,
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
    TypeSyntax,
    UnstructuredTask,
    parse,
)
from jigane.languages.python import PythonAdapter

adapter = PythonAdapter()
LOOP = EventLoopDomain()


def find(node: Node, name: str) -> Node:
    stack = [node]
    while stack:
        current = stack.pop()
        named = current.child_by_field_name("name")
        if (
            current.type == "function_definition"
            and named
            and (named.text or b"") == name.encode()
        ):
            parent = current.parent
            decorated = parent is not None and parent.type == "decorated_definition"
            return parent if parent is not None and decorated else current
        stack.extend(reversed(current.children))
    raise AssertionError(name)


def steps(code: str) -> list[ConcurrencyStep]:
    source = f"async def f():\n{code}\n".encode()
    root = parse(adapter, source).root_node
    body = adapter.body_of(find(root, "f"))
    assert body is not None
    return adapter.concurrency_steps(body, source, LOOP)


def indent(code: str) -> str:
    return "\n".join("    " + line for line in code.splitlines())


def names(found: list[ConcurrencyStep]) -> list[str]:
    return [type(s).__name__ for s in found]


def called(found: list[ConcurrencyStep]) -> list[str]:
    return [s.call.name for s in found if isinstance(s, AwaitCall | SyncCall)]


def isolation(code: str, name: str) -> Isolation:
    source = code.encode()
    root = parse(adapter, source).root_node
    return adapter.isolation_of(find(root, name), source, _EmptyContext())


class _EmptyContext:
    def type_syntaxes(self, _type_name: str) -> list[TypeSyntax]:
        return []

    def supertype_names(self, _type_name: str) -> list[str]:
        return []


def test_adapter_protocol() -> None:
    assert isinstance(adapter, ConcurrencyAdapter)


def test_async_function_runs_on_the_event_loop_and_sync_function_is_nonisolated() -> None:
    code = "async def a(): pass\ndef s(): pass\n@deco\nasync def d(): pass\n"
    assert isolation(code, "a") == LOOP
    assert isolation(code, "s") == NonisolatedDomain()
    assert isolation(code, "d") == LOOP


def test_is_async_sees_through_decorators() -> None:
    source = b"@deco\nasync def d(): pass\n\ndef s(): pass\n"
    root = parse(adapter, source).root_node
    assert adapter.is_async(find(root, "d"))
    assert not adapter.is_async(find(root, "s"))


def test_ui_is_never_reported() -> None:
    assert not adapter.is_ui_type("QWidget", _EmptyContext())
    assert not adapter.is_main_thread(LOOP)
    assert not adapter.is_main_thread(QueueDomain("main"))


def test_await_call_and_plain_call() -> None:
    found = steps(indent("await a.load(1)\nprint(2)"))
    assert [type(s) for s in found] == [AwaitCall, SyncCall]
    assert called(found) == ["load", "print"]


def test_arguments_come_before_the_awaited_call() -> None:
    found = steps(indent("await outer(inner())"))
    assert called(found) == ["inner", "outer"]
    assert [type(s) for s in found] == [SyncCall, AwaitCall]


def test_create_task_is_an_unstructured_task_of_one_call() -> None:
    for call in (
        "asyncio.create_task(job())",
        "asyncio.ensure_future(job())",
        "loop.create_task(job())",
    ):
        (task,) = steps(indent(call))
        assert isinstance(task, UnstructuredTask)
        assert task.kind == TaskKind.TASK
        assert task.body.isolation == LOOP
        assert [type(s) for s in task.body.steps] == [AwaitCall]
        assert called(list(task.body.steps)) == ["job"]


def test_create_task_marks_whether_the_handle_is_kept() -> None:
    dropped = ("asyncio.create_task(job())",)
    kept = (
        "t = asyncio.create_task(job())",
        "self._task = asyncio.create_task(job())",
        "app[key] = asyncio.create_task(job())",
        "tasks.append(asyncio.create_task(job()))",
        "await asyncio.create_task(job())",
    )
    for call in dropped:
        (task,) = steps(indent(call))
        assert isinstance(task, UnstructuredTask)
        assert not task.handle_kept
    for call in kept:
        (task,) = [s for s in steps(indent(call)) if isinstance(s, UnstructuredTask)]
        assert task.handle_kept, call


def test_gather_has_one_branch_per_coroutine() -> None:
    (group,) = steps(indent("await asyncio.gather(a(), b(), return_exceptions=True)"))
    assert isinstance(group, StructuredConcurrency)
    assert group.kind == StructuredKind.JOIN_ALL
    assert [called(list(b.steps)) for b in group.branches] == [["a"], ["b"]]


def test_task_group_collects_create_task_of_the_alias() -> None:
    code = (
        "async with asyncio.TaskGroup() as tg:\n"
        "    tg.create_task(a())\n    tg.create_task(b())\n    other()"
    )
    found = steps(indent(code))
    assert names(found) == ["SyncCall", "StructuredConcurrency"]
    group = found[1]
    assert isinstance(group, StructuredConcurrency)
    assert group.kind == StructuredKind.SCOPE
    assert [called(list(b.steps)) for b in group.branches] == [["a"], ["b"]]


def test_create_task_outside_a_group_is_not_structured() -> None:
    code = "async with asyncio.TaskGroup() as tg:\n    pass\nasyncio.create_task(a())"
    assert names(steps(indent(code))) == ["UnstructuredTask"]


def test_to_thread_and_run_in_executor_dispatch_to_the_executor() -> None:
    (first,) = steps(indent("await asyncio.to_thread(work, 1)"))
    (second,) = steps(indent("await loop.run_in_executor(None, work, 1)"))
    for step, label in ((first, "to_thread"), (second, "run_in_executor")):
        assert isinstance(step, QueueDispatch)
        assert step.body == Body(QueueDomain("executor"), ())
        assert step.label == label


def test_blocking_waits() -> None:
    code = (
        "time.sleep(1)\n"
        "threading.Event().wait()\n"
        "done = threading.Event()\n"
        "done.wait()\n"
        "requests.get(url)\n"
        "subprocess.run(cmd)"
    )
    found = [s for s in steps(indent(code)) if isinstance(s, BlockingWait)]
    assert [s.name for s in found] == [
        "time.sleep",
        "threading.Event().wait",
        "done.wait",
        "requests.get",
        "subprocess.run",
    ]


def blocking_names(code: str) -> list[str]:
    return [s.name for s in steps(indent(code)) if isinstance(s, BlockingWait)]


def test_blocking_wait_receivers_each_decide_by_their_own_rule() -> None:
    code = (
        "urlopen(u)\n"
        "urllib.request.urlopen(u)\n"
        "other.urlopen(u)\n"
        "time.time()\n"
        "httpx.stream(u)\n"
        "httpx.post(u)\n"
        "subprocess.Popen(c)\n"
        "subprocess.check_output(c)\n"
        "worker = threading.Thread(target=g)\n"
        "worker.join()\n"
        "stranger.join()\n"
        "worker.wait()\n"
        "guard_lock.acquire()\n"
        "stranger.release()\n"
        "stranger.wait()\n"
        "threading.Thread(target=g).wait()\n"
        "make().wait()\n"
        "make_event().wait()"
    )
    assert blocking_names(code) == [
        "urlopen",
        "urllib.request.urlopen",
        "httpx.post",
        "subprocess.check_output",
        "worker.join",
        "worker.wait",
        "guard_lock.acquire",
        "threading.Thread(target=g).wait",
        "make_event().wait",
    ]


def test_awaited_calls_and_unrelated_calls_do_not_block() -> None:
    code = "await asyncio.sleep(1)\nawait event.wait()\nsession.get(url)\nsleep(1)"
    found = steps(indent(code))
    assert not any(isinstance(s, BlockingWait) for s in found)


def test_nested_definitions_are_not_walked() -> None:
    code = "def inner():\n    time.sleep(1)\nfn = lambda: time.sleep(1)"
    assert steps(indent(code)) == []
