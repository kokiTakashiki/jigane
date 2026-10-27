import errno
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

# GitHub の owner と repo に使える文字（英数字、-、_、.）に限る。先頭は . と - を除く。
# `.` と `..` だけの部分や `.` で始まる部分がデータの置き場の外を指すのを防ぐ
COMPONENT_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_NUMBER_PATTERN = re.compile(r"[0-9]+")


def default_data_home() -> Path:
    """JIGANE_HOME を無視した既定の置き場。"""
    if sys.platform == "win32":
        # LOCALAPPDATA が無い環境は想定しない。無ければ KeyError で気づかせる
        return Path(os.environ["LOCALAPPDATA"]) / "jigane"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "jigane"


def data_home() -> Path:
    override = os.environ.get("JIGANE_HOME")
    if override:
        return Path(override)
    return default_data_home()


class ReviewKind(StrEnum):
    """レビューの出所の種類。識別子の区切り、キーの規則、保存先はこの種類の性質として持つ。"""

    PULL_REQUEST = "pull_request"
    RANGE = "range"
    FEATURE = "feature"
    DESIGN_DOC = "design_doc"

    @property
    def separator(self) -> str:
        match self:
            case ReviewKind.PULL_REQUEST:
                return "#"
            case ReviewKind.RANGE:
                return "@"
            case ReviewKind.FEATURE:
                return ":"
            case ReviewKind.DESIGN_DOC:
                # シェルの履歴展開（!）やチルダ展開、URL の予約文字と重ならず、
                # GitHub の owner と repo に使えない文字でもある
                return "+"
            case _:
                assert_never(self)

    @property
    def key_pattern(self) -> re.Pattern[str]:
        match self:
            case ReviewKind.PULL_REQUEST:
                return _NUMBER_PATTERN
            case ReviewKind.RANGE:
                return COMPONENT_PATTERN
            case ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return COMPONENT_PATTERN
            case _:
                assert_never(self)

    @property
    def label(self) -> str:
        """利用者に見せる種類の呼び名。"""
        match self:
            case ReviewKind.PULL_REQUEST:
                return "PR"
            case ReviewKind.RANGE:
                return "範囲"
            case ReviewKind.FEATURE:
                return "機能"
            case ReviewKind.DESIGN_DOC:
                return "設計書"
            case _:
                assert_never(self)

    @property
    def is_pull_request(self) -> bool:
        """PR 固有の動作（ボットの材料）をするか。refresh は PR に固有でない。

        reply は差分レビュー式で文を作る。
        """
        match self:
            case ReviewKind.PULL_REQUEST:
                return True
            case ReviewKind.RANGE | ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return False
            case _:
                assert_never(self)

    @property
    def externally_numbered(self) -> bool:
        """キーを外部（GitHub）が採番するか。名前で作る種類は、この種類の保存先と重ならないよう譲る。"""
        match self:
            case ReviewKind.PULL_REQUEST:
                return True
            case ReviewKind.RANGE | ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return False
            case _:
                assert_never(self)

    @property
    def is_diff(self) -> bool:
        """差分レビュー式か。"""
        match self:
            case ReviewKind.PULL_REQUEST | ReviewKind.RANGE:
                return True
            case ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return False
            case _:
                assert_never(self)

    @property
    def mode(self) -> str:
        """review.json に書く mode の文字列。"""
        match self:
            case ReviewKind.PULL_REQUEST | ReviewKind.RANGE:
                return "diff"
            case ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return "feature"
            case _:
                assert_never(self)

    @property
    def directory_prefix(self) -> str:
        """キーの前に付けるディレクトリ名の印。

        範囲と設計書は PR の番号や機能の名前と重ならないよう、識別子の区切りと同じ印を付ける。
        """
        match self:
            case ReviewKind.PULL_REQUEST | ReviewKind.FEATURE:
                return ""
            case ReviewKind.RANGE:
                return "@"
            case ReviewKind.DESIGN_DOC:
                return "+"
            case _:
                assert_never(self)

    @classmethod
    def from_separator(cls, separator: str) -> "ReviewKind":
        for kind in cls:
            if kind.separator == separator:
                return kind
        raise ValueError(f"レビューの識別子の区切りが不正です: {separator!r}")


def check_new_key(kind: ReviewKind, key: str) -> None:
    """新しく作るレビューのキーの検査。作るときにだけ掛け、保存済みの識別子の解釈には掛けない。

    キーの規則に合うことに加え、保存先のディレクトリ名が PR の番号と重ならないことを求める。
    PR の番号は GitHub が振るので、名前で作る種類の側が譲る。以前に作った重なる記録は、
    保存した識別子の種類で読む。
    """
    if not kind.key_pattern.fullmatch(key):
        raise ValueError(f"{kind.label}の名前が不正です: {key!r}")
    directory_name = f"{kind.directory_prefix}{key}"
    for other in ReviewKind:
        if other is kind or not other.externally_numbered:
            continue
        if directory_name.startswith(other.directory_prefix) and other.key_pattern.fullmatch(
            directory_name.removeprefix(other.directory_prefix)
        ):
            raise ValueError(
                f"{kind.label}の名前を {key!r} にはできません"
                f"（{other.label}の番号と保存先が重なります）"
            )


def _identifier_patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    """区切りの文字は ReviewKind の性質から集め、識別子の文法をここ以外に写さない。"""
    separators = "".join(re.escape(kind.separator) for kind in ReviewKind)
    segment = rf"[^/{separators}\s]+"
    full = re.compile(rf"^({segment})/({segment})([{separators}])({segment})$")
    short = re.compile(rf"^({segment})([{separators}])({segment})$")
    return full, short


_FULL_PATTERN, _SHORT_PATTERN = _identifier_patterns()


def _kind_of(text: str, separator: str, key: str) -> ReviewKind:
    """区切りから種類を引き、その種類のキーの規則で検査する。規則は種類ごとに違い、文法の正規表現では絞れない。"""
    kind = ReviewKind.from_separator(separator)
    if not kind.key_pattern.fullmatch(key):
        raise ValueError(f"レビューの識別子の形が不正です: {text!r}")
    return kind


@dataclass(frozen=True)
class ReviewId:
    owner: str
    repo: str
    # PR は番号、範囲と機能と設計書は名前。ディレクトリ名として同じ位置に入る
    key: str
    kind: ReviewKind

    def __post_init__(self) -> None:
        for label, part in (("owner", self.owner), ("repo", self.repo)):
            if not COMPONENT_PATTERN.fullmatch(part):
                raise ValueError(f"レビューの識別子の {label} が不正です: {part!r}")
        if not self.kind.key_pattern.fullmatch(self.key):
            raise ValueError(f"レビューの識別子の名前または番号が不正です: {self.key!r}")

    @classmethod
    def parse(cls, text: str) -> "ReviewId":
        matched = _FULL_PATTERN.match(text)
        if matched is None:
            raise ValueError(f"レビューの識別子の形が不正です: {text!r}")
        owner, repo, separator, key = matched.groups()
        return cls(owner, repo, key, _kind_of(text, separator, key))

    @staticmethod
    def parse_short(text: str) -> tuple[str, ReviewKind, str]:
        """owner を省いた `repo<区切り>キー` を (repo, 種類, キー) に分ける。"""
        matched = _SHORT_PATTERN.match(text)
        if matched is None:
            raise ValueError(f"レビューの識別子の形が不正です: {text!r}")
        repo, separator, key = matched.groups()
        return repo, _kind_of(text, separator, key), key

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}{self.kind.separator}{self.key}"

    def directory(self) -> Path:
        home = data_home()
        path = home / self.owner / self.repo / f"{self.kind.directory_prefix}{self.key}"
        # 各部分の検査を通っていても、シンボリックリンクで外へ出る場合を拒む二重の守り
        if not path.resolve().is_relative_to(home.resolve()):
            raise ValueError(f"レビューの置き場がデータの場所の外にあります: {path}")
        return path


LOCK_FILE = ".lock"
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


def _try_lock(descriptor: int) -> bool:
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


def _unlock(descriptor: int) -> None:
    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


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

    ロックは OS のファイルロックで取る。持ち主のプロセスが落ちれば OS が外すので、
    古いロックを見分けて奪う処理は要らない。
    """
    lock_path = directory / LOCK_FILE
    # ロックのファイルは解放しても消さない。消すと、消す前に開いていた待ち手が消えた後の
    # inode を握り、新しく作られたファイルを握る別の持ち主と同時に臨界区間に入る
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        deadline = clock() + timeout
        while not _try_lock(descriptor):
            if clock() >= deadline:
                raise LockTimeoutError(f"レビューのロックを握れません: {lock_path}")
            sleep(poll)
        try:
            yield
        finally:
            _unlock(descriptor)
    finally:
        os.close(descriptor)


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
