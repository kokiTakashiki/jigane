"""レビュー対象の木の中のファイルを、木の外へ出さずに読む・列挙する。"""

import errno
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from jigane.git import process
from jigane.git.command_line import GitSubcommand
from jigane.git.invocation import git_command
from jigane.git.repository import EXCLUDED_DIRECTORY_NAMES

# レビューの本文に載せるファイルの大きさの上限。PR の作者が巨大なファイルを足しても、
# 読み込みと解析に無制限のメモリと時間を使わせない。policy_tree の 1 MB と同じ値に揃えてある
MAX_REVIEW_FILE_BYTES = 1_000_000

# 追跡外で ignore もされていないファイルを列挙する `git ls-files` の引数。scripts も使う
UNTRACKED_FILES_ARGS = ("-z", "--others", "--exclude-standard")


class TreeFileState(StrEnum):
    READABLE = "readable"
    MISSING = "missing"
    REFUSED = "refused"
    # ディレクトリ、FIFO、デバイスなど。存在はするが通常のファイルではない
    NOT_REGULAR = "not_regular"
    # 権限や I/O の失敗で開けない、または読めない。無いこととは区別する
    UNREADABLE = "unreadable"


class TreeFileTooLargeError(Exception):
    """max_bytes を超えるファイルを読もうとした。"""


@dataclass(frozen=True)
class Opened:
    """通常のファイルを開けた。閉じる責任は受け取った側が持つ。"""

    descriptor: int


@dataclass(frozen=True)
class NotOpened:
    """開かなかった。state は READABLE 以外。detail は OS の原因の文で、無いときは空。"""

    state: TreeFileState
    detail: str = ""


@dataclass(frozen=True)
class Content:
    data: bytes


@dataclass(frozen=True)
class TooLarge:
    """上限を超えた。"""


OpenOutcome = Opened | NotOpened
ReadOutcome = Content | TooLarge | NotOpened

_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
# O_NONBLOCK は、FIFO への差し替えでも open が書き手を待たないための指定
_OPEN_FLAGS = _FILE_FLAGS | getattr(os, "O_NONBLOCK", 0)
_DIRECTORY_FLAGS = _FILE_FLAGS | getattr(os, "O_DIRECTORY", 0)
# 根は呼ぶ側が渡す信頼できるパスなので、根そのものがリンクでも辿る。
# O_NOFOLLOW は根の中の要素にだけ付ける
_ROOT_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_BINARY", 0)
# dir_fd と O_NOFOLLOW がそろう環境（POSIX）だけで、要素ごとに辿る。
# Windows など、そろわない環境では、開く前の lstat でリンクを拒み、開いた後に同じファイルかを比べる
_CAN_WALK_BY_DIRECTORY = os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")

_ABSENT_ERRNOS = (errno.ENOENT, errno.ENOTDIR, errno.ELOOP)


def _failed(error: OSError) -> NotOpened:
    state = TreeFileState.MISSING if error.errno in _ABSENT_ERRNOS else TreeFileState.UNREADABLE
    return NotOpened(state, error.strerror or type(error).__name__)


def _walk_by_directory(root: Path, parts: tuple[str, ...]) -> OpenOutcome:
    """根から要素を一つずつ dir_fd と O_NOFOLLOW で開く。内外の確認と open は別操作にならない。"""
    try:
        current = os.open(root, _ROOT_FLAGS)
    except OSError as error:
        return _failed(error)
    try:
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            try:
                descriptor = os.open(
                    part, _OPEN_FLAGS if last else _DIRECTORY_FLAGS, dir_fd=current
                )
            except OSError as error:
                # ELOOP と ENOTDIR は OS によって、リンクにも通常のファイルでないものにも付く。
                # 原因はリンクかどうかで分ける
                try:
                    status = os.stat(part, dir_fd=current, follow_symlinks=False)
                    linked = stat.S_ISLNK(status.st_mode)
                    # Windows ではディレクトリを開くと EACCES になる。種類は開く失敗の後に見直す
                    irregular = last and not linked and not stat.S_ISREG(status.st_mode)
                except OSError:
                    linked = irregular = False
                if linked:
                    return NotOpened(TreeFileState.REFUSED)
                if irregular:
                    return NotOpened(TreeFileState.NOT_REGULAR)
                return _failed(error)
            if last:
                return Opened(descriptor)
            os.close(current)
            current = descriptor
    finally:
        os.close(current)
    return NotOpened(TreeFileState.MISSING)


def _open_same_file(path: Path, before: os.stat_result) -> OpenOutcome:
    """lstat した後に開き、同じファイルを開いたかを (st_dev, st_ino) で比べる。"""
    try:
        descriptor = os.open(path, _OPEN_FLAGS)
    except OSError as error:
        return _failed(error)
    try:
        after = os.fstat(descriptor)
    except OSError as error:
        os.close(descriptor)
        return NotOpened(TreeFileState.UNREADABLE, error.strerror or type(error).__name__)
    # lstat と open の間にリンクなどへ差し替えられたら、別のファイルを開いている
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        os.close(descriptor)
        return NotOpened(TreeFileState.REFUSED)
    return Opened(descriptor)


def _is_link_status(status: os.stat_result) -> bool:
    """シンボリックリンク、または Windows の再解析ポイント（symlink と junction）なら True。"""
    return stat.S_ISLNK(status.st_mode) or bool(
        getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _refusal_before_open(root: Path, path: Path, before: os.stat_result) -> NotOpened | None:
    """開く前に分かる拒否。開いてよいなら None。"""
    # 途中の要素が木の外へ出るリンクでないことは lstat で確かめ済みだが、
    # 根より上へ解決される道（結合点など）はここで拒む
    if not path.resolve().is_relative_to(root.resolve()):
        return NotOpened(TreeFileState.REFUSED)
    # Windows ではディレクトリを開くと EACCES になるので、開く前に種類で分ける
    if not stat.S_ISREG(before.st_mode):
        return NotOpened(TreeFileState.NOT_REGULAR)
    return None


def _walk_by_lstat(root: Path, parts: tuple[str, ...]) -> OpenOutcome:
    """dir_fd が使えない環境用。要素ごとに lstat でリンクを拒み、最後は開いた後に比べる。"""
    # Windows の junction は S_ISLNK にならないので、再解析ポイントの属性でも拒む
    current = root
    for index, part in enumerate(parts):
        current = current / part
        try:
            before = os.lstat(current)
        except OSError as error:
            return _failed(error)
        if _is_link_status(before):
            return NotOpened(TreeFileState.REFUSED)
        if index == len(parts) - 1:
            refused = _refusal_before_open(root, current, before)
            if refused is not None:
                return refused
            return _open_same_file(current, before)
        if not stat.S_ISDIR(before.st_mode):
            # dir_fd の経路が ENOTDIR から返す状態に合わせる。その位置にファイルは無い
            return NotOpened(TreeFileState.MISSING)
    return NotOpened(TreeFileState.MISSING)


def _walk_open(root: Path, parts: tuple[str, ...]) -> OpenOutcome:
    """木の中のファイルを開く本体。OS の違いはこの分岐の一段だけで吸収する。"""
    if _CAN_WALK_BY_DIRECTORY:
        return _walk_by_directory(root, parts)
    return _walk_by_lstat(root, parts)


def _open_tree_file(root: Path, relative: str) -> OpenOutcome:
    """木の中の通常のファイルを開く唯一の口。Opened のときだけ記述子を持つ。

    絶対パスと ".." を含むパスは、木の外へ出うるので読まない。
    開いた後の fstat で通常のファイルかを確かめるので、FIFO とデバイスは Opened にならない。
    """
    path = Path(relative)
    parts = tuple(part for part in path.parts if part != ".")
    if path.is_absolute() or ".." in parts:
        return NotOpened(TreeFileState.REFUSED)
    if not parts:
        return NotOpened(TreeFileState.MISSING)
    opened = _walk_open(root, parts)
    if not isinstance(opened, Opened):
        return opened
    try:
        regular = stat.S_ISREG(os.fstat(opened.descriptor).st_mode)
    except OSError as error:
        os.close(opened.descriptor)
        return NotOpened(TreeFileState.UNREADABLE, error.strerror or type(error).__name__)
    if not regular:
        os.close(opened.descriptor)
        return NotOpened(TreeFileState.NOT_REGULAR)
    return opened


def read_tree(root: Path, relative: str, max_bytes: int | None) -> ReadOutcome:
    """状態と中身を一回の走査で返す公開の入口。他の読み手はこれの薄い包み。

    状態の確認と読み取りを分けて呼ぶと、その間に変わった分を取りこぼすので、分岐はこの結果で行う。
    """
    opened = _open_tree_file(root, relative)
    if not isinstance(opened, Opened):
        return opened
    try:
        with os.fdopen(opened.descriptor, "rb", closefd=False) as stream:
            data = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
    except OSError as error:
        return NotOpened(TreeFileState.UNREADABLE, error.strerror or type(error).__name__)
    finally:
        os.close(opened.descriptor)
    if max_bytes is not None and len(data) > max_bytes:
        return TooLarge()
    return Content(data)


def split_input_path(root: Path, path: Path) -> tuple[Path, str]:
    """明示された入力を、信頼する根と、解決していない相対パスに分ける。"""
    if not path.is_absolute():
        return root, path.as_posix()
    root = root.absolute()
    if path.is_relative_to(root):
        return root, path.relative_to(root).as_posix()
    anchor = Path(path.anchor)
    if anchor == Path("/"):
        # POSIX のシステムが管理する一時領域の入口は根として辿る
        for system_directory in (anchor / "var", anchor / "tmp"):
            if path.is_relative_to(system_directory):
                return system_directory, path.relative_to(system_directory).as_posix()
    return anchor, path.relative_to(anchor).as_posix()


def check_input_parents(root: Path, path: Path) -> Path | NotOpened:
    """明示パスの `..` を、そこまでのディレクトリを検査してから畳む。"""
    directory, relative = split_input_path(root, path)
    parts = Path(relative).parts
    if ".." not in parts:
        return path
    if any(Path(part).anchor for part in parts):
        return NotOpened(TreeFileState.UNREADABLE, "パスの途中にドライブ指定があります")
    last_parent = max(index for index, part in enumerate(parts) if part == "..")
    directory = directory.absolute()
    current = directory
    try:
        for part in parts[: last_parent + 1]:
            if part == "..":
                # 呼び元が信頼した根自身のリンクを辿るのは、木の読み取りと同じ。
                if current == directory:
                    current = Path(os.path.realpath(directory, strict=True))
                current = current.parent
                continue
            current /= part
            status = os.lstat(current)
            if _is_link_status(status):
                return NotOpened(TreeFileState.REFUSED)
            if not stat.S_ISDIR(status.st_mode):
                return NotOpened(TreeFileState.MISSING, os.strerror(errno.ENOTDIR))
    except OSError as error:
        return _failed(error)
    # ここでは名前だけを得る。残るパスの内容は、根から read_tree で開く。
    return current.joinpath(*parts[last_parent + 1 :])


def read_input_text(root: Path, path: Path) -> str:
    """明示された通常ファイルを、UTF-8 と universal newlines で読む。"""
    filename = str(root / path)
    if path.anchor and not path.is_absolute():
        raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), filename)
    checked = check_input_parents(root, path)
    outcome: ReadOutcome
    if isinstance(checked, NotOpened):
        outcome = checked
    else:
        directory, relative = split_input_path(root, checked)
        if relative == ".":
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), filename)
        outcome = read_tree(directory, relative, None)
    if isinstance(outcome, Content):
        return outcome.data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(outcome, TooLarge):
        raise OSError(errno.EFBIG, os.strerror(errno.EFBIG), filename)
    code = {
        TreeFileState.MISSING: errno.ENOENT,
        TreeFileState.NOT_REGULAR: errno.EISDIR,
        TreeFileState.REFUSED: errno.ELOOP,
    }.get(outcome.state, errno.EIO)
    detail = outcome.detail
    if outcome.state is TreeFileState.REFUSED:
        detail = "シンボリックリンクを含むパスは読めません"
    raise OSError(code, detail or os.strerror(code), filename)


def tree_file_state(root: Path, relative: str) -> TreeFileState:
    """木の中のパスの扱いを返す。読める通常のファイル、無い、読まない、読めない。"""
    opened = _open_tree_file(root, relative)
    if isinstance(opened, Opened):
        os.close(opened.descriptor)
        return TreeFileState.READABLE
    return opened.state


def is_inside_tree(root: Path, relative: str) -> bool:
    """木の中の通常のファイルなら True。リンクと、解決すると木の外へ出る道は False。"""
    return tree_file_state(root, relative) is TreeFileState.READABLE


def read_tree_bytes(root: Path, relative: str, max_bytes: int | None = None) -> bytes | None:
    """木の中の通常のファイルだけを読む。読まないときは None。

    max_bytes を超えるときは TreeFileTooLargeError を送出する。
    """
    outcome = read_tree(root, relative, max_bytes)
    if isinstance(outcome, TooLarge):
        raise TreeFileTooLargeError(relative)
    return outcome.data if isinstance(outcome, Content) else None


def read_tree_file(
    root: Path, relative: str, max_bytes: int = MAX_REVIEW_FILE_BYTES
) -> bytes | None:
    """read_tree_bytes の、上限超過も None にする版。理由を区別せず、読まないものはすべて None。

    レビュー対象の木のリンクは辿らず、上限を超えるファイルは読まない。
    """
    outcome = read_tree(root, relative, max_bytes)
    return outcome.data if isinstance(outcome, Content) else None


def read_tree_text(root: Path, relative: str) -> str | None:
    """read_tree_bytes の UTF-8 版。不正なバイトは置換する。読まないときは None。"""
    data = read_tree_bytes(root, relative)
    return None if data is None else data.decode("utf-8", errors="replace")


def walk_tree_files(
    root: Path, start: Path, *, skip_hidden: bool = False, skip_nested_repositories: bool = False
) -> list[str]:
    """start の下の通常のファイルを、root からの相対パスで列挙する。

    シンボリックリンクのファイルとディレクトリは辿らず、結果にも入れない。
    skip_hidden は名前が "." で始まるディレクトリの下を、skip_nested_repositories は
    .git を持つ入れ子のディレクトリ（別の作業木）の下を、それぞれ辿らない。
    """
    found: list[str] = []
    for directory, subdirectories, files in os.walk(start, followlinks=False):
        # os.walk はリンクのディレクトリを一覧に載せるが辿らない。結果にも出さないためここで除く
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in EXCLUDED_DIRECTORY_NAMES
            and not (Path(directory) / name).is_symlink()
            and not (skip_hidden and name.startswith("."))
            and not (skip_nested_repositories and (Path(directory) / name / ".git").exists())
        )
        for name in sorted(files):
            file = Path(directory) / name
            if not file.is_symlink():
                found.append(file.relative_to(root).as_posix())
    return found


@dataclass(frozen=True)
class WorkFiles:
    """作業ディレクトリのファイルの一覧。untracked は files のうち追跡していないもの。"""

    files: tuple[str, ...]
    untracked: frozenset[str]


def _is_hidden(path: str) -> bool:
    return any(part.startswith(".") for part in path.split("/")[:-1])


def work_files(root: Path, *, include_untracked: bool = False) -> WorkFiles:
    """作業ディレクトリの通常のファイルを、root からの相対パスで列挙する。

    版管理の作業木なら、追跡しているファイル（サブモジュールの中の追跡ファイルを含む）。
    include_untracked なら、親の追跡外で ignore もされていないファイルを足す。
    版管理の外なら、隠しディレクトリと入れ子の別の作業木を除いた全ファイル。
    `.claude/worktrees/<名前>/` に入る他の作業の写しは、どちらの場合も読まない。
    追跡していても、作業コピーで消えたものとリンクは読めないので含めない。
    root がディレクトリでなければ FileNotFoundError にする。
    版管理の外と取り違えて、空の一覧を返さないため。
    """
    if not root.is_dir():
        raise FileNotFoundError(f"作業ディレクトリがありません: {root}")
    try:
        # サブモジュールの中の追跡ファイルも、親の根からの綴り（cli/jigane/x.py）で返す。
        # 初期化していないサブモジュールは中が空で、git は何も足さない
        listed = process.run_command(
            git_command(GitSubcommand.LS_FILES, "-z", "--recurse-submodules"), cwd=root
        )
        # --others は --recurse-submodules と併用できない。サブモジュールの中の追跡外は集めない:
        # 別のリポジトリの作業中のファイルは親のレビューの対象ではなく、
        # 各根で走らせる費用に合わない
        others = (
            process.run_command(
                git_command(GitSubcommand.LS_FILES, *UNTRACKED_FILES_ARGS), cwd=root
            )
            if include_untracked
            else ""
        )
    except (OSError, process.CommandError):
        # 版管理の外、または git が無い。追跡の線が引けないので全ファイルを見る
        found = walk_tree_files(root, root, skip_hidden=True, skip_nested_repositories=True)
        return WorkFiles(tuple(found), frozenset())
    tracked = [
        p for p in listed.split("\0") if p and tree_file_state(root, p) is TreeFileState.READABLE
    ]
    # 追跡外は隠しディレクトリの下を除く。.claude/worktrees の写しが追跡外のまま入るのを防ぐため。
    # 入れ子の作業木は一つのディレクトリとして返り、末尾が "/" になるので読める判定で落ちる
    untracked = [
        p
        for p in others.split("\0")
        if p and not _is_hidden(p) and tree_file_state(root, p) is TreeFileState.READABLE
    ]
    return WorkFiles((*tracked, *untracked), frozenset(untracked))
