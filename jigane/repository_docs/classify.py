"""パスの先が、レビューのリポジトリの中か外かを分ける。"""

import os
from dataclasses import dataclass
from pathlib import Path

from jigane.git.repository import identify_git


@dataclass(frozen=True)
class Inside:
    """レビューのリポジトリのチェックアウトの中。relative は根からの相対パス。"""

    checkout_root: Path
    relative: str


@dataclass(frozen=True)
class Outside:
    resolved: Path


def classify_path(path: str, owner: str, repo: str) -> Inside | Outside:
    """絶対パスを realpath で解き、親ディレクトリの git の owner と repo で中か外かを決める。

    path は絶対パスであること。比べは大文字小文字を区別しない。
    """
    resolved = Path(os.path.realpath(path))
    repository = identify_git(resolved.parent)
    if repository is None:
        return Outside(resolved)
    if (repository.owner.casefold(), repository.repo.casefold()) != (
        owner.casefold(),
        repo.casefold(),
    ):
        return Outside(resolved)
    try:
        relative = resolved.relative_to(repository.root).as_posix()
    except ValueError:
        # identify_git は親を解いた根を返すので通常は来ない。根の外なら外として読む
        return Outside(resolved)
    return Inside(repository.root, relative)
