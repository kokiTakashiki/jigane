"""レビュー対象の木の中のファイルを、木の外へ出さずに読む・列挙する。"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jigane.git import process
from jigane.git.repository import EXCLUDED_DIRECTORY_NAMES

# レビューの本文に載せるファイルの大きさの上限。PR の作者が巨大なファイルを足しても、
# 読み込みと解析に無制限のメモリと時間を使わせない。policy_tree の 1 MB と同じ値に揃えてある
MAX_REVIEW_FILE_BYTES = 1_000_000

TreeFileState = Literal["readable", "missing", "refused"]


class TreeFileTooLargeError(Exception):
    """max_bytes を超えるファイルを読もうとした。"""


def tree_file_state(root: Path, relative: str) -> TreeFileState:
    """木の中のパスの扱いを返す。読める通常のファイル、無い、読まない、の三つ。"""
    file = root / relative
    # 木は PR 由来で攻撃者が作れる。シンボリックリンクや、解決すると木の外へ出る道は、
    # レビューする側のマシンの秘密を読み出す経路になるので、読まない
    if Path(relative).is_absolute() or file.is_symlink():
        return "refused"
    if not file.resolve().is_relative_to(root.resolve()):
        return "refused"
    # 木の内側を指していて無い、またはディレクトリ。外へ出る道はここへ来る前に除いてある
    if not file.is_file():
        return "missing"
    return "readable"


def is_inside_tree(root: Path, relative: str) -> bool:
    """木の中の通常のファイルなら True。リンクと、解決すると木の外へ出る道は False。"""
    return tree_file_state(root, relative) == "readable"


def read_tree_bytes(root: Path, relative: str, max_bytes: int | None = None) -> bytes | None:
    """木の中の通常のファイルだけを読む。読まないときは None。

    max_bytes を超えるときは TreeFileTooLargeError を送出する。
    """
    if not is_inside_tree(root, relative):
        return None
    file = root / relative
    if max_bytes is not None and file.stat().st_size > max_bytes:
        raise TreeFileTooLargeError(relative)
    return file.read_bytes()


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
        listed = process.run_command(["git", "ls-files", "-z", "--recurse-submodules"], cwd=root)
        # --others は --recurse-submodules と併用できない。サブモジュールの中の追跡外は集めない:
        # 別のリポジトリの作業中のファイルは親のレビューの対象ではなく、
        # 各根で走らせる費用に合わない
        others = (
            process.run_command(
                ["git", "ls-files", "-z", "--others", "--exclude-standard"], cwd=root
            )
            if include_untracked
            else ""
        )
    except (OSError, process.CommandError):
        # 版管理の外、または git が無い。追跡の線が引けないので全ファイルを見る
        found = walk_tree_files(root, root, skip_hidden=True, skip_nested_repositories=True)
        return WorkFiles(tuple(found), frozenset())
    tracked = [p for p in listed.split("\0") if p and tree_file_state(root, p) == "readable"]
    # 追跡外は隠しディレクトリの下を除く。.claude/worktrees の写しが追跡外のまま入るのを防ぐため。
    # 入れ子の作業木は一つのディレクトリとして返り、末尾が "/" になるので読める判定で落ちる
    untracked = [
        p
        for p in others.split("\0")
        if p and not _is_hidden(p) and tree_file_state(root, p) == "readable"
    ]
    return WorkFiles((*tracked, *untracked), frozenset(untracked))
