import importlib.util
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_locked.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_locked", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def spawn(lock_dir: Path, log: Path, *command: str) -> subprocess.Popen[bytes]:
    """標準エラーは log へ書かせる。パイプにすると、残った孫が端を持ち続けて読み終わらない。"""
    env = {**os.environ, "JIGANE_GATE_LOCK_DIR": str(lock_dir)}
    with log.open("wb") as stderr:
        return subprocess.Popen([sys.executable, str(SCRIPT), *command], env=env, stderr=stderr)


def wait_until(condition: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_second_command_waits_for_the_first_to_finish(tmp_path: Path) -> None:
    started = tmp_path / "started"
    first_done = tmp_path / "first-done"
    second_done = tmp_path / "second-done"
    first = spawn(
        tmp_path,
        tmp_path / "first.log",
        sys.executable,
        "-c",
        f"import pathlib, time; pathlib.Path({str(started)!r}).touch(); time.sleep(1); "
        f"pathlib.Path({str(first_done)!r}).touch()",
    )
    wait_until(started.exists)
    second = spawn(
        tmp_path,
        tmp_path / "second.log",
        sys.executable,
        "-c",
        f"import pathlib; pathlib.Path({str(second_done)!r}).touch()",
    )
    first.wait(timeout=30)
    second.wait(timeout=30)

    assert first.returncode == 0
    assert second.returncode == 0
    # 二つ目は一つ目の終わりより後に走った。時刻の順で確かめる
    assert second_done.stat().st_mtime_ns >= first_done.stat().st_mtime_ns
    assert "ゲートの錠を待っています" in (tmp_path / "second.log").read_text(encoding="utf-8")


def test_exit_code_of_the_command_is_returned(tmp_path: Path) -> None:
    process = spawn(tmp_path, tmp_path / "log", sys.executable, "-c", "raise SystemExit(7)")
    process.wait(timeout=30)

    assert process.returncode == 7


def test_lock_is_released_when_the_owner_process_dies(tmp_path: Path) -> None:
    started = tmp_path / "started"
    owner = spawn(
        tmp_path,
        tmp_path / "owner.log",
        sys.executable,
        "-c",
        f"import pathlib, time; pathlib.Path({str(started)!r}).touch(); time.sleep(20)",
    )
    wait_until(started.exists)
    owner.kill()
    owner.wait(timeout=30)

    # 持ち主が消えた後は、待たずに取れる
    follower = spawn(tmp_path, tmp_path / "follower.log", sys.executable, "-c", "pass")
    follower.wait(timeout=30)
    assert follower.returncode == 0
    assert (tmp_path / "follower.log").read_text(encoding="utf-8") == ""


def test_windows_runs_the_command_without_taking_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setenv("JIGANE_GATE_LOCK_DIR", str(tmp_path / "locks"))

    code = module.main([sys.executable, "-c", "raise SystemExit(3)"])

    assert code == 3
    assert not (tmp_path / "locks").exists()
