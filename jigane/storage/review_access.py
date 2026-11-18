import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from jigane.storage.locking import open_no_follow, review_lock
from jigane.storage.store import CheckedReviewId
from jigane.storage.symlink_guard import (
    StoredFileNotRegularError,
    StoredFileSymlinkError,
    is_link_like,
    raise_if_link,
)


def _stored_path(review_id: CheckedReviewId, name: str) -> Path:
    """保存先の中のファイルのパス。最終の要素が symlink なら StoredFileSymlinkError にする。"""
    path = review_id.directory() / name
    raise_if_link(path)
    return path


def read_stored_file(directory: Path, name: str) -> str:
    """ディレクトリの中のファイルを、symlink を辿らずに読む。

    symlink なら StoredFileSymlinkError、通常のファイルでなければ StoredFileNotRegularError、
    無ければ FileNotFoundError にする。
    """
    path = directory / name
    raise_if_link(path)
    # O_NONBLOCK は、書き手のいない FIFO の open が戻らなくなるのを避ける
    descriptor = open_no_follow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    # 開く前の lstat は差し替えられうるので、開いた fd を調べる
    try:
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
    except BaseException:
        os.close(descriptor)
        raise
    if not regular:
        os.close(descriptor)
        raise StoredFileNotRegularError(path)
    with os.fdopen(descriptor, encoding="utf-8") as file:
        return file.read()


def stored_entry_present(directory: Path, name: str) -> bool:
    """ディレクトリの中に、その名前の通常のファイルか symlink があるか。

    symlink を「無い」にせず read_stored_file に止めさせるために、辿らずに数える。
    """
    path = directory / name
    return is_link_like(path) or path.is_file()


def read_stored_file_if_present(directory: Path, name: str) -> str | None:
    """ディレクトリの中のファイルを、symlink を辿らずに読む。通常のファイルが無ければ None。

    symlink（壊れたものを含む）は「無い」にせず StoredFileSymlinkError にする。
    """
    if not stored_entry_present(directory, name):
        return None
    return read_stored_file(directory, name)


def read_stored_text(review_id: CheckedReviewId, name: str) -> str | None:
    """保存先の中のファイルを読む。無ければ None。

    symlink なら辿らず StoredFileSymlinkError にする。
    """
    path = _stored_path(review_id, name)
    if not path.is_file():
        return None
    return read_stored_file(path.parent, path.name)


def stored_file_exists(review_id: CheckedReviewId, name: str) -> bool:
    """保存先の中に通常のファイルがあるか。symlink は無いものとして扱い、辿らない。"""
    try:
        return _stored_path(review_id, name).is_file()
    except StoredFileSymlinkError:
        # 一覧や絞り込みが、印の symlink 一件で全体ごと止まらないようにする
        return False


@contextmanager
def locked_review(review_id: CheckedReviewId) -> Iterator[Path]:
    """既にあるレビューの状態を排他で書く一続き。保存先を渡す。作成の排他は請求が担う。"""
    directory = review_id.directory()
    with review_lock(directory):
        yield directory
