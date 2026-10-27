from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, NoReturn, cast

import pytest
from review_fakes import make_review

from jigane.languages.base import (
    ActorDomain,
    BlockingWait,
    Body,
    ConcurrencyAdapter,
    EventLoopDomain,
    GlobalActorDomain,
    Isolation,
    NonisolatedDomain,
    QueueDomain,
    StructuredConcurrency,
    StructuredKind,
    UiThreadDomain,
)
from jigane.languages.java import JavaAdapter
from jigane.reviews.lifecycle import Review
from jigane.skills import policy_concurrency, policy_figures
from jigane.skills.policy_concurrency import concurrency_diagram
from jigane.storage.store import ReviewKind

if TYPE_CHECKING:
    from tree_sitter import Node

    from jigane.sequence.index import Index

SWIFT = """\
actor Store {
    func load() async -> Int { 1 }
    func save() async {}
}

@MainActor final class Screen {
    func show() {}
    func render() async {}
}

final class Helper {
    func help() async {}
}

final class Model {
    let store: Store
    let screen: Screen
    let helper: Helper
    let queue: DispatchQueue
    let semaphore: DispatchSemaphore

    func plain() async {
        await helper.help()
    }

    func sync() {
        helper.help()
    }

    func run() async {
        await store.load()
        await helper.help()
        async let a = store.load()
        async let b = screen.render()
        Task { await store.save() }
        queue.async { Task { await store.save() } }
        semaphore.wait()
    }

    @MainActor func ui() async {
        await store.load()
        await withTaskGroup(of: Int.self) { g in
            g.addTask { await self.store.load() }
            g.addTask { await self.store.save() }
        }
        await screen.render()
    }
}
"""

FUNCTIONS = ("Model.plain()", "Model.sync()", "Model.run()", "Model.ui()")
JAVA = "class A {\n    void f() { g(); }\n    void g() { }\n}\n"
ASYNCIO = """\
import asyncio
import time


async def fetch():
    pass


async def work():
    await fetch()
    time.sleep(1)
    asyncio.create_task(fetch())
    await asyncio.to_thread(print, "x")


def plain():
    time.sleep(1)
    print("x")


async def only_loop():
    await fetch()
"""


def feature_review(
    root: Path,
    name: str,
    source: str,
    functions: tuple[str, ...],
    others: dict[str, str] | None = None,
) -> Review:
    """others は図の対象にしないが、索引には入れる別ファイル。"""
    (root / name).write_text(source, encoding="utf-8")
    for other, text in (others or {}).items():
        (root / other).write_text(text, encoding="utf-8")
    lines = len(source.splitlines())
    segments = [
        {
            "new_path": name,
            "old_path": name,
            "new_span": {"start_line": 1, "end_line": lines},
            "old_span": {"start_line": 1, "end_line": lines},
            "new_line_count": lines,
            "old_line_count": lines,
            "qualified_name": function,
            "declaration_kind": "method",
        }
        for function in functions
    ]
    return make_review(
        root,
        [name, *(others or {})],
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )


def panels(tmp_path: Path) -> dict[str, str]:
    figure = concurrency_diagram(feature_review(tmp_path, "m.swift", SWIFT, FUNCTIONS))
    assert figure is not None
    return {p.title.split("（")[0]: p.mermaid for p in figure.panels}


def test_only_cross_domain_awaits_become_arrows(tmp_path: Path) -> None:
    found = panels(tmp_path)
    assert "Model.plain" not in found  # 同じ隔離されない領域の中の await だけ
    assert "Model.sync" not in found  # 並行の手順が無い
    run = found["Model.run"]
    assert "participant D0 as 隔離されない" in run
    assert "D0->>D1: load" in run
    assert "help" not in run


def test_par_task_queue_and_blocking_wait(tmp_path: Path) -> None:
    run = panels(tmp_path)["Model.run"]
    assert "par async let" in run
    assert "and async let" in run
    assert "opt Task\n" in run
    assert "注意: 構造化されない Task（親の取り消しが伝わらない）" in run
    assert "->>D3: async" in run or "->>D4: async" in run
    assert "box キュー" in run
    assert "participant D3 as queue" in run or "participant D4 as queue" in run
    assert "注意: ブロックする待ち合わせ semaphore.wait（協調スレッドを塞ぐ）" in run
    assert "rect" not in run


def test_boxes_group_columns_by_kind(tmp_path: Path) -> None:
    run = panels(tmp_path)["Model.run"]
    assert "box actor" in run
    assert "box MainActor" in run
    assert "box 隔離されない領域" in run
    assert "participant D1 as Store" in run


def test_function_domain_comes_first_and_task_group_uses_par(tmp_path: Path) -> None:
    ui = panels(tmp_path)["Model.ui"]
    assert ui.index("participant D0 as MainActor") < ui.index("participant D1 as Store")
    assert "par タスクグループ" in ui
    assert "D0->>D1: load" in ui
    assert "screen.render" not in ui
    assert "D0->>D0" not in ui


def test_panel_title_has_name_and_position(tmp_path: Path) -> None:
    figure = concurrency_diagram(feature_review(tmp_path, "m.swift", SWIFT, FUNCTIONS))
    assert figure is not None
    assert any(p.title.startswith("Model.run（m.swift:") for p in figure.panels)


def test_language_without_the_adapter_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 口を持たない言語の代わりに、口の一つを None にした新しい型を返させる。
    # JavaAdapter そのものから口を外すと、先のテストが isinstance で残した
    # ABC の判定の写しに当たり、並列の実行の順で結果が変わる
    class WithoutIsolation(JavaAdapter):
        # runtime_checkable は None の口を「無い」と見なす
        isolation_of = None  # type: ignore[assignment]

    without = WithoutIsolation()
    assert not isinstance(without, ConcurrencyAdapter)
    monkeypatch.setattr(
        "jigane.skills.policy_concurrency.registry.adapter_for", lambda _path: without
    )
    assert (
        concurrency_diagram(feature_review(tmp_path, "m.java", JAVA, ("A.f()", "A.g()"))) is None
    )


def test_python_asyncio_function_is_drawn_and_sync_function_is_not(tmp_path: Path) -> None:
    figure = concurrency_diagram(
        feature_review(tmp_path, "m.py", ASYNCIO, ("work", "plain", "only_loop"))
    )
    assert figure is not None
    found = {p.title.split("（")[0]: p.mermaid for p in figure.panels}
    assert set(found) == {"m.work"}  # 同期の関数と、イベントループの中で閉じる関数は図にならない
    assert found["m.work"] == (
        "sequenceDiagram\n"
        "    box イベントループ\n"
        "        participant D0 as イベントループ\n"
        "    end\n"
        "    box キュー\n"
        "        participant D1 as executor\n"
        "    end\n"
        "    Note over D0: 注意: ブロックする待ち合わせ time.sleep（協調スレッドを塞ぐ）\n"
        "    Note over D0: 注意: 構造化されない Task（親の取り消しが伝わらない）\n"
        "    D0->>D1: to_thread\n"
    )


KEPT_TASK = """\
import asyncio


def blocking():
    pass


async def work():
    await asyncio.to_thread(blocking)
    asyncio.create_task(job())
    tasks.append(asyncio.create_task(job()))
    self._task = asyncio.create_task(job())


async def job():
    pass
"""


def test_python_task_note_only_where_the_handle_is_dropped(tmp_path: Path) -> None:
    figure = concurrency_diagram(feature_review(tmp_path, "m.py", KEPT_TASK, ("work",)))
    assert figure is not None
    (panel,) = figure.panels
    assert panel.mermaid == (
        "sequenceDiagram\n"
        "    box イベントループ\n"
        "        participant D0 as イベントループ\n"
        "    end\n"
        "    box キュー\n"
        "        participant D1 as executor\n"
        "    end\n"
        "    D0->>D1: to_thread\n"
        "    Note over D0: 注意: 構造化されない Task（親の取り消しが伝わらない）\n"
    )


def test_registry_places_it_after_the_sequence_diagram() -> None:
    keys = [e.key for e in policy_figures.REGISTRY]
    assert keys.index("concurrency") == keys.index("sequence") + 1


DETACHED = """\
actor Store {
    func load() async -> Int { 1 }
}

@MainActor final class Screen {
    let store: Store

    func go() async {
        Task { await store.load() }
        Task.detached { await store.load() }
    }
}
"""


def test_task_is_drawn_in_the_inherited_domain_and_detached_in_nonisolated(tmp_path: Path) -> None:
    figure = concurrency_diagram(feature_review(tmp_path, "s.swift", DETACHED, ("Screen.go()",)))
    assert figure is not None
    go = figure.panels[0].mermaid
    # Task は引き継いだ MainActor のまま Store へ渡り、detached は隔離されない領域を経る
    assert "opt Task\n        D0->>D1: load" in go
    assert "D0->>D2: Task.detached\n        D2->>D1: load" in go
    assert "box 隔離されない領域" in go


EXTENSION = """\
actor Store {
    func load() async -> Int { 1 }
}

extension Screen {
    func go() async {
        await store.load()
    }
}
"""

SCREEN = """\
@MainActor final class Screen {
    let store: Store
}
"""


def test_extension_method_in_another_file_runs_in_the_main_actor(tmp_path: Path) -> None:
    review = feature_review(
        tmp_path, "ext.swift", EXTENSION, ("Screen.go()",), {"screen.swift": SCREEN}
    )
    figure = concurrency_diagram(review)
    assert figure is not None
    go = figure.panels[0].mermaid
    assert "participant D0 as MainActor" in go
    assert "D0->>D1: load" in go


UI_SWIFT = """\
class UIView {}
class UILabel: UIView { var text: String? }
class Plain { var items: Int = 0 }
struct Row: View { func refresh() {} }

@MainActor final class Screen {
    func show() {}
    func render() async {}
}

final class Presenter {
    let screen: Screen
    let label: UILabel
    let row: Row
    let plain: Plain
    let external: UIButton

    func background() {
        screen.show()
        screen.show()
        label.text = "a"
        row.refresh()
        external.setNeedsLayout()
        plain.items = 1
    }

    func awaited() async {
        await screen.render()
    }

    @MainActor func onMain() {
        screen.show()
        label.text = "a"
    }

    func dispatched() {
        DispatchQueue.main.async { screen.show() }
        Task { @MainActor in screen.show() }
        Task { await MainActor.run { label.text = "b" } }
    }
}
"""
UI_FUNCTIONS = (
    "Presenter.background()",
    "Presenter.awaited()",
    "Presenter.onMain()",
    "Presenter.dispatched()",
)
UI_NOTE = "注意: 主スレッドの外からの UI の更新"


def ui_panels(tmp_path: Path) -> dict[str, str]:
    figure = concurrency_diagram(feature_review(tmp_path, "u.swift", UI_SWIFT, UI_FUNCTIONS))
    assert figure is not None
    return {p.title.split("（")[0]: p.mermaid for p in figure.panels}


def test_sync_call_to_main_actor_member_is_flagged_once(tmp_path: Path) -> None:
    background = ui_panels(tmp_path)["Presenter.background"]
    assert background.count(f"{UI_NOTE} show") == 1
    assert "->>" not in background


def test_ui_typed_receivers_are_flagged_and_plain_types_are_not(tmp_path: Path) -> None:
    background = ui_panels(tmp_path)["Presenter.background"]
    assert f"{UI_NOTE} label.text" in background  # UILabel は索引の型。UIView が上位
    assert f"{UI_NOTE} refresh" in background  # SwiftUI の View に準拠する型
    assert f"{UI_NOTE} setNeedsLayout" in background  # 索引に無い外部の型は名前の UI 接頭辞
    assert "plain.items" not in background


def test_no_flag_inside_main_actor_or_with_await(tmp_path: Path) -> None:
    found = ui_panels(tmp_path)
    assert "Presenter.onMain" not in found
    awaited = found.get("Presenter.awaited", "")
    assert UI_NOTE not in awaited
    assert "D0->>D1: render" in awaited


def test_main_queue_and_main_actor_closures_are_not_flagged(tmp_path: Path) -> None:
    dispatched = ui_panels(tmp_path).get("Presenter.dispatched", "")
    assert UI_NOTE not in dispatched


CONTROLLER_SWIFT = """\
@MainActor final class ViewModel {
    init(id: Int) {}
    func load() {}
}

class Detail: UIViewController {
    var model: ViewModel?

    override func viewDidLoad() {
        self.title = "a"
        model = ViewModel(id: 1)
        model?.load()
    }
}

final class Nested: Detail {
    func again() {
        self.title = "b"
    }
}

final class Worker {
    func background() {
        let model = ViewModel(id: 2)
        model.load()
    }
}
"""


def test_uikit_subclass_runs_in_the_main_actor_without_flags(tmp_path: Path) -> None:
    figure = concurrency_diagram(
        feature_review(
            tmp_path,
            "c.swift",
            CONTROLLER_SWIFT,
            ("Detail.viewDidLoad()", "Nested.again()", "Worker.background()"),
        )
    )
    assert figure is not None
    found = {p.title.split("（")[0]: p.mermaid for p in figure.panels}
    assert "Detail.viewDidLoad" not in found
    assert "Nested.again" not in found
    assert UI_NOTE in found["Worker.background"]


BRANCHES = """\
import asyncio


async def fetch():
    pass


async def gather_both():
    await asyncio.gather(fetch(), unknown())
    fetch()


async def grouped():
    async with asyncio.TaskGroup() as tg:
        tg.create_task(fetch())
        tg.create_task(other.go())


async def outside():
    await fetch()
"""


def branch_panels(tmp_path: Path) -> dict[str, str]:
    figure = concurrency_diagram(
        feature_review(tmp_path, "m.py", BRANCHES, ("gather_both", "grouped", "outside"))
    )
    assert figure is not None
    return {p.title.split("（")[0]: p.mermaid for p in figure.panels}


def test_python_gather_in_one_event_loop_is_a_par_of_self_arrows(tmp_path: Path) -> None:
    found = branch_panels(tmp_path)
    assert found["m.gather_both"] == (
        "sequenceDiagram\n"
        "    box イベントループ\n"
        "        participant D0 as イベントループ\n"
        "    end\n"
        "    par gather\n"
        "        D0->>D0: fetch\n"
        "    and gather\n"
        "        D0->>D0: unknown\n"  # 解決できない呼び出しも名前で出る
        "    end\n"  # 塊の外の fetch() は矢印にならない
    )


def test_python_task_group_in_one_event_loop_is_a_par(tmp_path: Path) -> None:
    grouped = branch_panels(tmp_path)["m.grouped"]
    assert "par タスクグループ" in grouped
    assert "and タスクグループ" in grouped
    assert "D0->>D0: fetch" in grouped
    assert "D0->>D0: go" in grouped


def test_calls_outside_a_par_in_the_same_domain_stay_undrawn(tmp_path: Path) -> None:
    assert "m.outside" not in branch_panels(tmp_path)


CSHARP_UI = """\
class Repo
{
    public async Task<int> Load() { return 1; }
}

class Counter
{
    public async Task Bump() { }
}

class MainView : Window
{
    Repo repo;
    Counter counter;
    Label label;

    public async Task Refresh()
    {
        await repo.Load();
        await Task.WhenAll(repo.Load(), counter.Bump());
        Task.Run(() =>
        {
            label.Text = "x";
            Thread.Sleep(10);
        });
        Dispatcher.Invoke(() => { label.Text = "y"; });
        var r = repo.Load().Result;
    }

    public void Later()
    {
        Task.Delay(5).ContinueWith(_ => { label.Text = "c"; });
        new Thread(() => { Thread.Sleep(10); }).Start();
    }

    public void Paint() { label.Text = "z"; }
}
"""


def csharp_panels(tmp_path: Path) -> dict[str, str]:
    figure = concurrency_diagram(
        feature_review(
            tmp_path, "m.cs", CSHARP_UI, ("MainView.Refresh", "MainView.Paint", "MainView.Later")
        )
    )
    assert figure is not None
    return {p.title.split("（")[0]: p.mermaid for p in figure.panels}


def test_csharp_ui_thread_hops_and_flags(tmp_path: Path) -> None:
    found = csharp_panels(tmp_path)
    # Paint は UI スレッドの中だけで閉じる
    assert set(found) == {"MainView.Refresh", "MainView.Later"}
    assert found["MainView.Refresh"] == (
        "sequenceDiagram\n"
        "    box UI スレッド\n"
        "        participant D0 as UI スレッド\n"
        "    end\n"
        "    box 隔離されない領域\n"
        "        participant D1 as 隔離されない\n"
        "    end\n"
        "    box キュー\n"
        "        participant D2 as スレッドプール\n"
        "    end\n"
        "    D0->>D1: Load\n"
        "    par WhenAll\n"
        "        D0->>D1: Load\n"
        "    and WhenAll\n"
        "        D0->>D1: Bump\n"
        "    end\n"
        "    Note over D0: 注意: 構造化されない Task（親の取り消しが伝わらない）\n"
        "    opt Task\n"
        "        D0->>D2: Task\n"
        "        Note over D2: 注意: 主スレッドの外からの UI の更新 label.Text\n"
        "        Note over D2: 注意: ブロックする待ち合わせ Thread.Sleep（協調スレッドを塞ぐ）\n"
        "    end\n"
        "    Note over D0: 注意: ブロックする待ち合わせ repo.Load().Result（主スレッドを塞ぐ）\n"
    )
    assert found["MainView.Later"] == (
        "sequenceDiagram\n"
        "    box UI スレッド\n"
        "        participant D0 as UI スレッド\n"
        "    end\n"
        "    box キュー\n"
        "        participant D1 as スレッドプール\n"
        "    end\n"
        "    Note over D0: 注意: 構造化されない Task（親の取り消しが伝わらない）\n"
        "    opt Task\n"
        "        D0->>D1: Task\n"
        "        Note over D1: 注意: 主スレッドの外からの UI の更新 label.Text\n"
        "    end\n"
        "    Note over D0: 注意: 構造化されない Task（親の取り消しが伝わらない）\n"
        "    opt Task\n"
        "        D0->>D1: Task\n"
        "        Note over D1: 注意: ブロックする待ち合わせ Thread.Sleep（協調スレッドを塞ぐ）\n"
        "    end\n"
    )


JAVA_UI = """\
class Repo {
    int load() { return 1; }
}

class Counter {
    void bump() { }
}

class MainFrame extends JFrame {
    Repo repo;
    Counter counter;
    JLabel label;
    Future<Integer> future;

    void refresh() {
        repo.load();
        executor.invokeAll(List.of(() -> repo.load(), () -> counter.bump()));
        executor.submit(() -> {
            label.setText("x");
            repo.load();
        });
        SwingUtilities.invokeLater(() -> { label.setText("y"); });
        Thread.sleep(10);
        future.get();
    }

    void paint() { label.setText("z"); }
}

class Worker {
    JLabel label;
    Repo repo;

    void run() {
        new Thread(() -> { label.setText("w"); }).start();
        SwingUtilities.invokeLater(() -> { repo.load(); });
        runOnUiThread(() -> { repo.load(); });
    }

    void idle() {
        Thread.sleep(10);
    }
}
"""


def java_panels(tmp_path: Path) -> dict[str, str]:
    figure = concurrency_diagram(
        feature_review(
            tmp_path,
            "m.java",
            JAVA_UI,
            ("MainFrame.refresh", "MainFrame.paint", "Worker.run", "Worker.idle"),
        )
    )
    assert figure is not None
    return {p.title.split("（")[0]: p.mermaid for p in figure.panels}


def test_java_ui_thread_hops_and_flags(tmp_path: Path) -> None:
    found = java_panels(tmp_path)
    assert "MainFrame.paint" not in found  # UI スレッドの中だけで閉じ、並行の手順が無い
    assert found["MainFrame.refresh"] == (
        "sequenceDiagram\n"
        "    box UI スレッド\n"
        "        participant D0 as UI スレッド\n"
        "    end\n"
        "    box 隔離されない領域\n"
        "        participant D2 as 隔離されない\n"
        "    end\n"
        "    box キュー\n"
        "        participant D1 as スレッドプール\n"
        "    end\n"
        "    par invokeAll\n"
        "        D0->>D1: invokeAll\n"
        "        D1->>D2: load\n"
        "    and invokeAll\n"
        "        D0->>D1: invokeAll\n"
        "        D1->>D2: bump\n"
        "    end\n"
        "    Note over D0: 注意: 構造化されない Task（親の取り消しが伝わらない）\n"
        "    opt Task\n"
        "        D0->>D1: Task\n"
        "        Note over D1: 注意: 主スレッドの外からの UI の更新 setText\n"
        "    end\n"
        "    Note over D0: 注意: ブロックする待ち合わせ Thread.sleep（主スレッドを塞ぐ）\n"
        "    Note over D0: 注意: ブロックする待ち合わせ future.get（主スレッドを塞ぐ）\n"
    )
    assert found["Worker.run"] == (
        "sequenceDiagram\n"
        "    box 隔離されない領域\n"
        "        participant D0 as 隔離されない\n"
        "    end\n"
        "    box UI スレッド\n"
        "        participant D2 as UI スレッド\n"
        "    end\n"
        "    box キュー\n"
        "        participant D1 as スレッドプール\n"
        "    end\n"
        "    Note over D0: 注意: 構造化されない Task（親の取り消しが伝わらない）\n"
        "    opt Task\n"
        "        D0->>D1: Task\n"
        "        Note over D1: 注意: 主スレッドの外からの UI の更新 setText\n"
        "    end\n"
        "    D0->>D2: invokeLater\n"
        "    D0->>D2: runOnUiThread\n"
    )


def test_java_thread_updating_a_ui_type_is_flagged(tmp_path: Path) -> None:
    run = java_panels(tmp_path)["Worker.run"]
    assert "注意: 構造化されない Task（親の取り消しが伝わらない）" in run
    assert "注意: 主スレッドの外からの UI の更新 setText" in run


def test_blocking_wait_on_the_main_thread_is_flagged_without_async(tmp_path: Path) -> None:
    found = java_panels(tmp_path)
    # 隔離されない領域の同期の関数の待ち合わせは、協調スレッドも主スレッドも塞がない
    assert "Worker.idle" not in found
    refresh = found["MainFrame.refresh"]
    assert "ブロックする待ち合わせ Thread.sleep（主スレッドを塞ぐ）" in refresh
    assert "ブロックする待ち合わせ future.get（主スレッドを塞ぐ）" in refresh
    assert "協調スレッド" not in refresh


CSHARP_SWITCH = """\
class Repo
{
    public async Task<int> Load() { return 1; }
}

class MainView : Window
{
    Repo repo;
    Label label;

    public async Task Refresh()
    {
        label.Text = "before";
        await repo.Load().ConfigureAwait(false);
        label.Text = "after";
    }

    public async Task Branch()
    {
        await Task.WhenAll(Work(), Work());
        label.Text = "outer";
    }

    async Task Work()
    {
        await repo.Load().ConfigureAwait(false);
        label.Text = "inner";
    }

    public async Task Awaited()
    {
        await Task.Run(async () =>
        {
            await repo.Load().ConfigureAwait(false);
        });
        label.Text = "outer";
    }
}
"""


def csharp_switch_panels(tmp_path: Path, *functions: str) -> dict[str, str]:
    figure = concurrency_diagram(feature_review(tmp_path, "s.cs", CSHARP_SWITCH, functions))
    assert figure is not None
    return {p.title.split("（")[0]: p.mermaid for p in figure.panels}


def test_csharp_configure_await_false_switches_the_domain_for_later_steps(tmp_path: Path) -> None:
    found = csharp_switch_panels(tmp_path, "MainView.Refresh")
    assert found["MainView.Refresh"] == (
        "sequenceDiagram\n"
        "    box UI スレッド\n"
        "        participant D0 as UI スレッド\n"
        "    end\n"
        "    box 隔離されない領域\n"
        "        participant D1 as 隔離されない\n"
        "    end\n"
        "    box キュー\n"
        "        participant D2 as スレッドプール\n"
        "    end\n"
        "    D0->>D1: Load\n"
        "    Note over D2: ConfigureAwait(false) の後の継続\n"
        "    Note over D2: 注意: 主スレッドの外からの UI の更新 label.Text\n"
    )


def test_csharp_switch_inside_a_branch_or_awaited_task_does_not_flag_the_outer_update(
    tmp_path: Path,
) -> None:
    found = csharp_switch_panels(tmp_path, "MainView.Branch", "MainView.Awaited")
    for title in ("MainView.Branch", "MainView.Awaited"):
        diagram = found[title]
        assert "label.Text" not in diagram
        # 外側の UI スレッドの列には切り替えの印が出ない
        assert "Note over D0: ConfigureAwait(false)" not in diagram


JAVA_NAME_ONLY = """\
import java.util.concurrent.Executor;

class Application {
    void register() { }
}

class Hooks {
    Application application;
    Window window;
    Executor executor;

    void run() {
        executor.execute(() -> { application.register(); window.show(); });
    }
}
"""

JAVA_IMPORTED_UI = """\
import java.util.concurrent.Executor;
import javafx.application.Application;

class Hooks {
    Application application;
    Executor executor;

    void run() {
        executor.execute(() -> { application.launch(); });
    }
}
"""


def test_java_ui_update_flag_needs_a_confirmed_ui_type(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = concurrency_diagram(
        feature_review(tmp_path / "a", "m.java", JAVA_NAME_ONLY, ("Hooks.run",))
    )
    assert plain is not None
    assert "UI の更新" not in plain.panels[0].mermaid
    imported = concurrency_diagram(
        feature_review(tmp_path / "b", "m.java", JAVA_IMPORTED_UI, ("Hooks.run",))
    )
    assert imported is not None
    assert "注意: 主スレッドの外からの UI の更新 launch" in imported.panels[0].mermaid


DOMAINS = (
    (ActorDomain("Store"), "actor", "Store"),
    (GlobalActorDomain("MainActor"), "MainActor", "MainActor"),
    (GlobalActorDomain("Pipeline"), "グローバル actor", "Pipeline"),
    (UiThreadDomain(), "UI スレッド", "UI スレッド"),
    (EventLoopDomain(), "イベントループ", "イベントループ"),
    (NonisolatedDomain(), "隔離されない領域", "隔離されない"),
    (QueueDomain("queue"), "キュー", "queue"),
)


@pytest.mark.parametrize(("domain", "box", "label"), DOMAINS)
def test_every_isolation_domain_has_a_box_and_a_label(
    domain: Isolation, box: str, label: str
) -> None:
    assert policy_concurrency._box(domain) == box
    assert policy_concurrency._label(domain) == label


class _Unused:
    """この試験が使わない依存。触れたら、使わない前提が崩れたとして落とす。"""

    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"使わないはずの依存の {name} に触れた")


def _unused_call(*_args: object) -> NoReturn:
    raise AssertionError("使わないはずの依存を呼んだ")


def drawing_with(*, on_main: bool) -> policy_concurrency._Drawing:
    adapter = SimpleNamespace(is_main_thread=lambda _domain: on_main)
    return policy_concurrency._Drawing(
        adapter=cast("ConcurrencyAdapter", adapter),
        index=cast("Index", _Unused()),
        resolve=_unused_call,
        receiver_type=_unused_call,
        scope=cast("Node", _Unused()),
    )


def test_blocking_wait_is_silent_outside_async_and_off_the_main_thread() -> None:
    steps = (BlockingWait("sleep", 1),)
    current = NonisolatedDomain()
    off_main = drawing_with(on_main=False)
    assert off_main.render(steps, current, in_async=False, depth=0) == []
    assert off_main.render(steps, current, in_async=True, depth=0) == [
        "Note over D0: 注意: ブロックする待ち合わせ sleep（協調スレッドを塞ぐ）"
    ]
    assert drawing_with(on_main=True).render(steps, current, in_async=False, depth=0) == [
        "Note over D0: 注意: ブロックする待ち合わせ sleep（主スレッドを塞ぐ）"
    ]


def test_a_par_without_any_drawn_branch_is_not_drawn() -> None:
    empty = Body(isolation=NonisolatedDomain(), steps=())
    par = StructuredConcurrency(StructuredKind.JOIN_ALL, (empty,), 1, "async let")
    drawing = drawing_with(on_main=False)
    assert drawing.render((par,), NonisolatedDomain(), in_async=True, depth=0) == []
