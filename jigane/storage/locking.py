import errno
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_FILE = ".lock"
# 開いているファイルを消せるか。Windows は共有違反（WinError 32）で消せない
CAN_UNLINK_OPEN_FILE = sys.platform != "win32"
# ロックを握れるまで待つ上限。保存の一続きは LLM を呼ばず短いので、これを越えて握られているなら
# 握り続けている側の異常と見て待つのをやめる
LOCK_TIMEOUT_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.05

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class LockTimeoutError(Exception):
    pass


def try_lock(descriptor: int) -> bool:
    """OS のファイルロックを待たずに取る。取れなければ False。"""
    if sys.platform == "win32":
        # msvcrt.locking は現在のファイルの位置から nbytes の範囲を錠にする。
        # 位置 0 から 1 バイトを使う。
        # ファイルの終わりを越える範囲も錠にできるので、中身が空のままでよい
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
    else:
        # fcntl は win32 では import されないので、この腕でだけ使う
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise
    return True


def unlock(descriptor: int) -> None:
    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _same_file(descriptor: int, path: Path) -> bool:
    """握った記述子が、いまも path にあるファイルを指すか。削除で差し替わっていれば False。"""
    try:
        on_disk = path.stat()
    except FileNotFoundError:
        return False
    held = os.fstat(descriptor)
    return (held.st_dev, held.st_ino) == (on_disk.st_dev, on_disk.st_ino)


@contextmanager
def hold_replaceable_file_lock(path: Path) -> Iterator[bool]:
    """remove_file_if_unlocked に消されうるロックのファイルの錠を、待たずに取る。取れたかを返す。

    錠を握った後にファイルが差し替わっていれば、握った錠を外して開き直す。
    取れなかったときは何も握らずに False を返す。
    """
    while True:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR)
        try:
            if not try_lock(descriptor):
                yield False
                return
            if not _same_file(descriptor, path):
                unlock(descriptor)
                continue
            try:
                yield True
            finally:
                unlock(descriptor)
            return
        finally:
            os.close(descriptor)


def remove_file_if_unlocked(path: Path) -> bool:
    """錠を握れたときだけロックのファイルを消す。消せたか、元から無ければ True。

    他が錠を握っていれば消さず False を返す。Windows は開いたままのファイルを消せないため、
    消せない場合もファイルが残って False になる。
    """
    try:
        descriptor = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return True
    try:
        if not try_lock(descriptor):
            return False
        if CAN_UNLINK_OPEN_FILE:
            try:
                path.unlink()
            except OSError:
                return False
            finally:
                unlock(descriptor)
            return True
        # 開いたままのファイルを消せない環境では、錠を外して閉じてから消す。
        # 外してから消すまでの間に他が同じファイルを開けば、
        # 消す側が共有違反で失敗し、ファイルは残って他の錠になる
        unlock(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            path.unlink()
        except OSError:
            return False
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)


# timeout、poll、sleep、clock は、試験が待ちの上限と時刻を差し替える口。本体は既定のまま呼ぶ
@contextmanager
def review_lock(
    directory: Path,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
    poll: float = LOCK_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[None]:
    """レビューのディレクトリの状態のファイルを読み、直し、保存する一続きを排他にする。

    既にあるレビューの更新のための錠で、作成の排他は担わない。
    作成は _creation_claim の請求が担う。

    ロックは OS のファイルロックで取る。持ち主のプロセスが落ちれば OS が外すので、
    古いロックを見分けて奪う処理は要らない。
    """
    lock_path = directory / LOCK_FILE
    # ロックのファイルは解放しても消さない。消すと、消す前に開いていた待ち手が消えた後の
    # inode を握り、新しく作られたファイルを握る別の持ち主と同時に臨界区間に入る
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        deadline = clock() + timeout
        while not try_lock(descriptor):
            if clock() >= deadline:
                raise LockTimeoutError(f"レビューのロックを握れません: {lock_path}")
            sleep(poll)
        try:
            yield
        finally:
            unlock(descriptor)
    finally:
        os.close(descriptor)
