import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from jigane.storage.review_access import read_stored_file
from jigane.storage.symlink_guard import StoredFileNotRegularError

HANG_LIMIT_SECONDS = 5

# win32 としての型検査には SIGALRM、alarm、os.mkfifo が無いため、名前で引く
SIGALRM = vars(signal)["SIGALRM"]


@contextmanager
def hang_limit() -> Iterator[None]:
    """直しが無いと FIFO の open で止まるので、alarm で上限を付けて CI を止めない。"""

    def fail(_signal: int, _frame: object) -> None:
        raise TimeoutError("read_stored_file が戻らない")

    previous = signal.signal(SIGALRM, fail)
    vars(signal)["alarm"](HANG_LIMIT_SECONDS)
    try:
        yield
    finally:
        vars(signal)["alarm"](0)
        signal.signal(SIGALRM, previous)


def test_read_stored_file_refuses_a_fifo_without_blocking(tmp_path: Path) -> None:
    vars(os)["mkfifo"](tmp_path / "f.txt")
    with hang_limit(), pytest.raises(StoredFileNotRegularError):
        read_stored_file(tmp_path, "f.txt")


def test_read_stored_file_refuses_a_directory(tmp_path: Path) -> None:
    (tmp_path / "f.txt").mkdir()
    with hang_limit(), pytest.raises(StoredFileNotRegularError):
        read_stored_file(tmp_path, "f.txt")


def test_read_stored_file_still_reads_a_regular_file(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("本文", encoding="utf-8")
    with hang_limit():
        assert read_stored_file(tmp_path, "f.txt") == "本文"
