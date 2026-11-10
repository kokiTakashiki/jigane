from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from jigane.storage.locking import review_lock
from jigane.storage.store import CheckedReviewId


def read_stored_text(review_id: CheckedReviewId, name: str) -> str | None:
    """保存先の中のファイルを読む。無ければ None。"""
    path = review_id.directory() / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def stored_file_exists(review_id: CheckedReviewId, name: str) -> bool:
    return (review_id.directory() / name).is_file()


@contextmanager
def locked_review(review_id: CheckedReviewId) -> Iterator[Path]:
    """既にあるレビューの状態を排他で書く一続き。保存先を渡す。作成の排他は請求が担う。"""
    directory = review_id.directory()
    with review_lock(directory):
        yield directory
