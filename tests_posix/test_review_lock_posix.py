"""錠が無いと書き込みが失われることを、POSIX の原子的な置き換えで確かめる。

Windows の os.replace は、置き換え先に別の置き換えが重なると WinError 5 で落ち、
同じ現象がこの形では出ない。
"""

import os
import stat
import threading
from functools import partial
from pathlib import Path

import pytest
from lock_support import overlapping_merge, run_in_threads

from jigane.findings.records import load_findings_state
from jigane.storage.locking import (
    hold_replaceable_file_lock,
    open_lock_file,
    remove_file_if_unlocked,
    review_lock,
)
from jigane.storage.symlink_guard import StoredFileSymlinkError


def test_without_lock_a_group_is_lost(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    run_in_threads(
        [
            partial(overlapping_merge, tmp_path, g, locked=False, barrier=barrier)
            for g in ("macro", "security")
        ]
    )
    assert len(load_findings_state(tmp_path).groups) == 1


def test_symlinked_lock_file_is_refused_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    lock_path = tmp_path / ".lock"
    lock_path.symlink_to(target)
    with pytest.raises(StoredFileSymlinkError), review_lock(tmp_path):
        pytest.fail("錠を取れてはならない")
    with pytest.raises(StoredFileSymlinkError), hold_replaceable_file_lock(lock_path):
        pytest.fail("錠を取れてはならない")
    assert not target.exists()
    target.write_text("x")
    target.chmod(0o644)
    with pytest.raises(StoredFileSymlinkError):
        remove_file_if_unlocked(lock_path)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert lock_path.is_symlink()


def test_lstat_check_refuses_symlink_without_o_nofollow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(os, "O_NOFOLLOW")
    target = tmp_path / "outside"
    lock_path = tmp_path / ".lock"
    lock_path.symlink_to(target)
    with pytest.raises(StoredFileSymlinkError):
        open_lock_file(lock_path, os.O_CREAT | os.O_RDWR)
    assert not target.exists()
    regular = tmp_path / "regular"
    descriptor = open_lock_file(regular, os.O_CREAT | os.O_RDWR)
    os.close(descriptor)
    assert regular.exists()


def test_symlink_swapped_right_after_open_closes_descriptor_and_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(os, "O_NOFOLLOW")
    outside = tmp_path / "outside"
    lock_path = tmp_path / ".lock"
    real_open = os.open
    real_close = os.close
    opened: list[int] = []
    closed: list[int] = []

    def swapping_open(path: Path, flags: int) -> int:
        descriptor = real_open(path, flags)
        opened.append(descriptor)
        lock_path.unlink()
        lock_path.symlink_to(outside)
        return descriptor

    def recording_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(os, "open", swapping_open)
    monkeypatch.setattr(os, "close", recording_close)
    with pytest.raises(StoredFileSymlinkError):
        open_lock_file(lock_path, os.O_CREAT | os.O_RDWR)
    assert len(opened) == 1
    assert closed == opened
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(opened[0])
    assert not outside.exists()
