"""POSIX だけにある特殊ファイル（FIFO）を SARIF として渡したときの試験。"""

import os
import threading
from pathlib import Path

import pytest

from jigane.static_analysis.reader import SarifError, read_sarif


def make_fifo(path: Path) -> None:
    # win32 としての型検査には os.mkfifo が無いため、名前で引く
    vars(os)["mkfifo"](path)


def test_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.sarif"
    make_fifo(fifo)
    outcome: list[BaseException] = []

    def attempt() -> None:
        try:
            read_sarif([fifo], tmp_path, tmp_path)
        except SarifError as error:
            outcome.append(error)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "FIFO の読み込みが止まった"
    assert len(outcome) == 1
    assert "通常のファイル" in str(outcome[0])


def test_fifo_behind_symlink_is_rejected(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    make_fifo(fifo)
    link = tmp_path / "link.sarif"
    link.symlink_to(fifo)
    with pytest.raises(SarifError, match="通常のファイル"):
        read_sarif([link], tmp_path, tmp_path)
