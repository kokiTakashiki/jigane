"""POSIX だけにある FIFO を、文書の読み取りで確かめる。"""

import os
import threading
from pathlib import Path

from jigane.repository_docs.kinds import DocumentKind
from jigane.repository_docs.reader import HeadOfCheckout, read_document
from jigane.repository_docs.skip_reason import NotRegularFile


def attempt(path: Path, outcome: list[object]) -> None:
    outcome.append(read_document(DocumentKind.DEVELOPMENT, str(path), "a", "b", HeadOfCheckout()))


def test_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.md"
    # win32 としての型検査には os.mkfifo が無いため、名前で引く
    vars(os)["mkfifo"](fifo)
    outcome: list[object] = []

    worker = threading.Thread(target=attempt, args=(fifo, outcome), daemon=True)
    worker.start()
    worker.join(timeout=10)

    assert outcome == [NotRegularFile()]
