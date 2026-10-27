import io
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import IO, cast

import gate_slots
import pytest


def test_two_slots_then_third_waits_until_one_is_released(tmp_path: Path) -> None:
    first = gate_slots.try_acquire_slot(tmp_path)
    second = gate_slots.try_acquire_slot(tmp_path)
    assert first is not None
    assert second is not None
    assert gate_slots.try_acquire_slot(tmp_path) is None

    first.close()
    third = gate_slots.try_acquire_slot(tmp_path)
    assert third is not None
    second.close()
    third.close()


class _NotifyingStream(io.StringIO):
    """書かれたら合図を立てる流れ。"""

    def __init__(self) -> None:
        super().__init__()
        self.written = threading.Event()

    def write(self, text: str) -> int:
        size = super().write(text)
        self.written.set()
        return size


def test_acquire_prints_one_line_while_waiting_and_takes_released_slot(tmp_path: Path) -> None:
    held = [gate_slots.try_acquire_slot(tmp_path) for _ in range(gate_slots.SLOT_COUNT)]
    first, second = held
    assert first is not None
    assert second is not None
    stream = _NotifyingStream()
    result: list[IO[bytes]] = []

    def wait_for_slot() -> None:
        result.append(gate_slots.acquire_slot(tmp_path, poll_seconds=0.01, stream=stream))

    thread = threading.Thread(target=wait_for_slot)
    thread.start()
    # 時間で区切らず、待ちの一行が書かれた合図を待つ。負荷の下でも揺れない
    assert stream.written.wait(timeout=10)
    assert thread.is_alive()
    assert stream.getvalue() == gate_slots.WAIT_MESSAGE + "\n"

    first.close()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(result) == 1
    result[0].close()
    second.close()


def test_acquire_does_not_print_when_slot_is_free(tmp_path: Path) -> None:
    stream = io.StringIO()
    handle = gate_slots.acquire_slot(tmp_path, stream=stream)
    handle.close()
    assert stream.getvalue() == ""


def test_acquire_creates_missing_directory(tmp_path: Path) -> None:
    directory = tmp_path / "nested" / "slots"
    handle = gate_slots.try_acquire_slot(directory)
    assert handle is not None
    handle.close()
    assert (directory / "slot-0").exists()


@pytest.mark.parametrize(
    ("cpu_count", "expected"),
    [(12, 6), (8, 4), (3, 1), (1, 1), (None, 1)],
)
def test_workers_per_slot(cpu_count: int | None, expected: int) -> None:
    assert gate_slots.workers_per_slot(cpu_count) == expected


def test_worker_side_does_not_take_a_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    taken: list[Path] = []
    monkeypatch.setattr(gate_slots, "acquire_slot", taken.append)
    held_before = list(gate_slots._held)
    worker_config = cast("pytest.Config", SimpleNamespace(workerinput={"workerid": "gw0"}))
    gate_slots.pytest_configure(worker_config)
    assert taken == []
    assert gate_slots._held == held_before


def test_configure_holds_a_slot_until_unconfigure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate_slots, "default_slot_directory", lambda: tmp_path)
    config = cast("pytest.Config", SimpleNamespace())
    gate_slots.pytest_configure(config)
    try:
        # 制御の側が席を一つ持つので、残りの席だけが取れる
        others = [gate_slots.try_acquire_slot(tmp_path) for _ in range(gate_slots.SLOT_COUNT - 1)]
        assert all(handle is not None for handle in others)
        assert gate_slots.try_acquire_slot(tmp_path) is None
        for handle in others:
            assert handle is not None
            handle.close()
    finally:
        gate_slots.pytest_unconfigure()
    released = [gate_slots.try_acquire_slot(tmp_path) for _ in range(gate_slots.SLOT_COUNT)]
    assert all(handle is not None for handle in released)
    for handle in released:
        assert handle is not None
        handle.close()


def test_wait_message_names_the_slot_count() -> None:
    assert f"{gate_slots.SLOT_COUNT} 本" in gate_slots.WAIT_MESSAGE
