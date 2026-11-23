"""指定されたパスが、レビューのリポジトリの中か外かを分ける。"""

import os
from dataclasses import dataclass
from pathlib import Path

from jigane.git.repository import identify_git
from jigane.git.tree_files import split_input_path


@dataclass(frozen=True)
class Inside:
    """レビューのリポジトリのチェックアウトの中。relative は根からの相対パス。"""

    checkout_root: Path
    relative: str


@dataclass(frozen=True)
class Outside:
    resolved: Path


def classify_path(path: str, owner: str, repo: str) -> Inside | Outside:
    """指定された絶対パスを含むチェックアウトの owner と repo で中か外かを決める。

    木内なら指定された相対パスで起点を読む。比べは大文字小文字を区別しない。
    """
    root, relative = split_input_path(Path.cwd(), Path(path))
    original = root.resolve() / relative
    for directory in original.parents:
        repository = identify_git(directory)
        # リンクを辿った先のリポジトリが返っても、指定の祖先に根が無ければ使わない
        if repository is None or not directory.is_relative_to(repository.root):
            continue
        if (repository.owner.casefold(), repository.repo.casefold()) == (
            owner.casefold(),
            repo.casefold(),
        ):
            return Inside(repository.root, original.relative_to(repository.root).as_posix())
        break
    return Outside(Path(os.path.realpath(path)))
