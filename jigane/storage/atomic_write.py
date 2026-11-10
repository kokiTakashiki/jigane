import os
import sys
import threading
import time
from pathlib import Path

# Windows で置き換え先を読みの手が開いている間は、os.replace が WinError 5 で落ちる。
# 読みの手は短く閉じるので、間隔を置いて試し直す。待ちの和は COUNT * SECONDS で 2 秒
REPLACE_RETRY_COUNT = 40
REPLACE_RETRY_SECONDS = 0.05
WINDOWS_ACCESS_DENIED = 5


class StoredFileInUseError(OSError):
    """読みの手が開いているため、保存物を置き換えられなかった。"""


def _is_access_denied(error: PermissionError) -> bool:
    """Windows で、置き換え先が開かれているときの WinError 5 か。"""
    if sys.platform == "win32":
        return error.winerror == WINDOWS_ACCESS_DENIED
    # POSIX の rename は開いているファイルを置き換えられるので、権限の誤りは試し直さない
    return False


def _replace_retrying(temporary: Path, path: Path) -> None:
    for attempt in range(REPLACE_RETRY_COUNT + 1):
        try:
            temporary.replace(path)
        except PermissionError as error:
            if not _is_access_denied(error):
                raise
            if attempt == REPLACE_RETRY_COUNT:
                raise StoredFileInUseError(
                    f"保存物を置き換えられません: {path}。"
                    "読む側（show やエディタなど）が開いたままかもしれません"
                ) from error
            time.sleep(REPLACE_RETRY_SECONDS)
        else:
            return


def write_text_atomic(path: Path, text: str) -> None:
    """一時のファイルに書いてから置き換える。途中で落ちても壊れたファイルを残さない。"""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        _replace_retrying(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
