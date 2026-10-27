import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent


def load_module() -> ModuleType:
    path = ROOT / "scripts" / "flaky_hunt.py"
    spec = importlib.util.spec_from_file_location("flaky_hunt", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass が自分のモジュールを sys.modules から引くため、実行の前に登録する。
    sys.modules["flaky_hunt"] = module
    spec.loader.exec_module(module)
    return module


flaky_hunt = load_module()

FAILED_OUTPUT = """\
=========================== short test summary info ============================
FAILED tests/test_a.py::test_x - assert 1 == 2
FAILED tests/test_a.py::test_x - assert 1 == 2
ERROR tests/test_b.py::test_y[param] - RuntimeError
1 failed, 1 error in 0.1s
"""


def test_parse_workers_splits_and_validates() -> None:
    assert flaky_hunt.parse_workers("auto, 4,8,") == ["auto", "4", "8"]
    with pytest.raises(ValueError, match="空"):
        flaky_hunt.parse_workers(" , ")
    with pytest.raises(ValueError, match="整数"):
        flaky_hunt.parse_workers("auto,x")


def test_build_plans_cycles_workers_and_pairs_each_seed_once() -> None:
    plans = flaky_hunt.build_plans(5, ["auto", "4"], [10, 11, 12, 13, 14])
    assert [(p.number, p.seed, p.workers) for p in plans] == [
        (1, 10, "auto"),
        (2, 11, "4"),
        (3, 12, "auto"),
        (4, 13, "4"),
        (5, 14, "auto"),
    ]


def test_build_plans_rejects_bad_counts() -> None:
    with pytest.raises(ValueError, match="1 以上"):
        flaky_hunt.build_plans(0, ["auto"], [])
    with pytest.raises(ValueError, match="足りない"):
        flaky_hunt.build_plans(3, ["auto"], [1])


def test_draw_seeds_are_distinct() -> None:
    seeds = flaky_hunt.draw_seeds(50)
    assert len(set(seeds)) == 50


def test_pytest_command_enables_randomly_with_seed_and_workers() -> None:
    plan = flaky_hunt.Plan(1, 99, "4")
    command = flaky_hunt.pytest_command(plan, ["tests/test_a.py"])
    assert command[-1] == "tests/test_a.py"
    assert command[4:9] == ["-p", "randomly", "--randomly-seed=99", "-n", "4"]


def test_parse_failed_tests_dedupes_and_keeps_order() -> None:
    assert flaky_hunt.parse_failed_tests(FAILED_OUTPUT) == (
        "tests/test_a.py::test_x",
        "tests/test_b.py::test_y[param]",
    )


def test_build_table_lists_each_run() -> None:
    plans = flaky_hunt.build_plans(2, ["auto"], [1, 2])
    outcomes = [
        flaky_hunt.Outcome(plans[0], 0, ()),
        flaky_hunt.Outcome(plans[1], 1, ("tests/test_a.py::test_x",)),
    ]
    assert flaky_hunt.build_table(outcomes).splitlines() == [
        "| 回 | 種 | 並列 | 結果 | 落ちた試験 |",
        "|---|---|---|---|---|",
        "| 1 | 1 | auto | 通った | - |",
        "| 2 | 2 | auto | 落ちた(終了コード 1) | tests/test_a.py::test_x |",
    ]


def test_main_returns_nonzero_and_saves_log_when_a_run_fails(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flaky_hunt.tempfile, "tempdir", str(tmp_path))
    calls: list[list[str]] = []

    def runner(command: list[str]) -> tuple[int, str]:
        calls.append(command)
        return (1, FAILED_OUTPUT) if len(calls) == 2 else (0, "ok")

    assert flaky_hunt.main(["--runs", "3", "--workers", "auto,2"], runner) == 1
    out = capsys.readouterr().out
    assert len(calls) == 3
    assert "tests/test_a.py::test_x, tests/test_b.py::test_y[param]" in out
    log_directory = Path(out.split("出力の全文: ")[1].strip())
    logs = list(log_directory.glob("run-002-*.log"))
    assert [log.read_text(encoding="utf-8") for log in logs] == [FAILED_OUTPUT]
    assert len(list(log_directory.iterdir())) == 1


def test_main_returns_zero_when_all_runs_pass(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flaky_hunt.tempfile, "tempdir", str(tmp_path))
    assert flaky_hunt.main(["--runs", "2", "--workers", "auto"], lambda _command: (0, "ok")) == 0
    assert "すべて通った" in capsys.readouterr().out


def test_main_uses_defaults_and_treats_positionals_as_targets() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> tuple[int, str]:
        calls.append(command)
        return 0, "ok"

    assert flaky_hunt.main(["tests/test_a.py"], runner) == 0
    assert len(calls) == flaky_hunt.DEFAULT_RUNS
    assert all(command[-1] == "tests/test_a.py" for command in calls)
    assert [command[8] for command in calls[:3]] == flaky_hunt.parse_workers(
        flaky_hunt.DEFAULT_WORKERS
    )


def test_run_pytest_points_each_run_at_its_own_temporary_jigane_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    homes: list[str] = []

    def fake_run_tool(*_args: object, env: dict[str, str], **_kwargs: object) -> str:
        homes.append(env["JIGANE_HOME"])
        assert Path(env["JIGANE_HOME"]).is_dir()
        return "ok"

    monkeypatch.setattr(flaky_hunt, "run_tool", fake_run_tool)
    assert flaky_hunt.run_pytest(["pytest"]) == (0, "ok")
    assert flaky_hunt.run_pytest(["pytest"]) == (0, "ok")
    assert homes[0] != homes[1]
    assert not any(Path(home).exists() for home in homes)
