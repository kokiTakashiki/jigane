import os
import stat
from pathlib import Path

from jigane.storage.symlink_guard import StoredFileSymlinkError, is_link_status


class NotARealDirectoryError(OSError):
    """置き場のパスが、通常のディレクトリではないファイルなどだった。

    symlink は StoredFileSymlinkError で扱う。
    """


def require_real_directory(path: Path) -> None:
    """既にあるパスが通常のディレクトリでなければ止める。無いパスは、これから作るので通す。"""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        # 無いものは検査の対象外。作るのは呼び出し側で、作った後に必要ならもう一度呼ぶ
        return
    if is_link_status(info):
        raise StoredFileSymlinkError(path)
    if not stat.S_ISDIR(info.st_mode):
        raise NotARealDirectoryError(f"{path} はディレクトリではありません。")
