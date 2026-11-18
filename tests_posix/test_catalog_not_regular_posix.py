import os
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_review_relations import save

from jigane.reviews.catalog import (
    check_stored_id,
    list_review_summaries_counting_skipped,
    read_review_at,
)
from jigane.reviews.lifecycle import Review
from jigane.storage.review_error import ReviewError
from jigane.storage.store import ReviewKind

HANG_LIMIT_SECONDS = 5

# win32 としての型検査には SIGALRM、alarm、os.mkfifo が無いため、名前で引く
SIGALRM = vars(signal)["SIGALRM"]


@contextmanager
def hang_limit() -> Iterator[None]:
    """直しが無いと FIFO の open で止まるので、alarm で上限を付けて CI を止めない。"""

    def fail(_signal: int, _frame: object) -> None:
        raise TimeoutError("review.json の読みが戻らない")

    previous = signal.signal(SIGALRM, fail)
    vars(signal)["alarm"](HANG_LIMIT_SECONDS)
    try:
        yield
    finally:
        vars(signal)["alarm"](0)
        signal.signal(SIGALRM, previous)


def _make_fifo(path: Path) -> None:
    vars(os)["mkfifo"](path)


def _make_directory(path: Path) -> None:
    path.mkdir()


@pytest.fixture(params=[_make_fifo, _make_directory], ids=["fifo", "directory"])
def broken_review(project: Path, request: pytest.FixtureRequest) -> Review:
    """review.json を通常でないファイルに差し替えたレビュー。"""
    review = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")
    stored = review.id.directory() / "review.json"
    stored.unlink()
    make: Callable[[Path], None] = request.param
    make(stored)
    return review


def test_read_review_at_ends_in_review_error(broken_review: Review) -> None:
    with hang_limit(), pytest.raises(ReviewError, match="通常でない"):
        read_review_at(broken_review.id.directory())


@pytest.mark.usefixtures("broken_review")
def test_list_counts_the_review_as_skipped() -> None:
    with hang_limit():
        result = list_review_summaries_counting_skipped()
    assert result.skipped == 1
    assert result.summaries == ()


def test_check_stored_id_lets_the_review_through(broken_review: Review) -> None:
    with hang_limit():
        checked = check_stored_id(broken_review.id)
    assert checked.directory() == broken_review.id.directory()
