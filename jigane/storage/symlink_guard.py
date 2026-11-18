import os
import stat
import sys
from pathlib import Path


class StoredFileSymlinkError(OSError):
    """保存先や錠のファイル、置き場のディレクトリが symlink だった。

    先を辿らず、書き換えも置き換えもしない。
    """

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"{path} は symlink（Windows ではリパースポイント）です。辿らずに止めます。"
        )
        self.path = path


class StoredFileNotRegularError(OSError):
    """保存先のファイルが通常のファイルではなかった（FIFO、デバイス、ディレクトリなど）。

    読まずに止める。FIFO は書き手が現れるまで open や read が戻らないためである。
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} は通常のファイルではありません。読まずに止めます。")
        self.path = path


def is_link_like(path: Path) -> bool:
    """path が symlink か、Windows のリパースポイントか。無ければ False。

    検査するのは path の最終の要素だけである。path の親ディレクトリが symlink に
    差し替えられている場合の検査は呼び出し側の責務とする。jigane が作る置き場の中に
    ある保存物のディレクトリは、この前提で足りる。複製の置き場のように外部が作りうる
    ディレクトリは別途検査する。
    """
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        # 初めて作るときの通常の状態で、検査する対象がまだ無い
        return False
    return is_link_status(status)


def is_link_status(status: os.stat_result) -> bool:
    """lstat の結果が symlink か、Windows のリパースポイントか。"""
    if stat.S_ISLNK(status.st_mode):
        return True
    # ジャンクションなどのリパースポイントは S_ISLNK に現れない
    attributes = getattr(status, "st_file_attributes", 0)
    return (
        bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) if sys.platform == "win32" else False
    )


def raise_if_link(path: Path) -> None:
    """path が symlink か Windows のリパースポイントなら StoredFileSymlinkError を送出する。"""
    if is_link_like(path):
        raise StoredFileSymlinkError(path)
