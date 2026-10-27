import subprocess
from pathlib import Path

import pytest

from jigane.git.repository import identify, parse_remote_url


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/shop.git",
        "https://github.com/acme/shop",
        "git@github.com:acme/shop.git",
        "ssh://git@github.com/acme/shop.git",
    ],
)
def test_parse_remote_url(url: str) -> None:
    assert parse_remote_url(url) == ("acme", "shop")


def test_directory_without_git_is_local(tmp_path: Path) -> None:
    directory = tmp_path / "plain"
    directory.mkdir()
    repository = identify(directory)
    assert (repository.owner, repository.repo) == ("local", "plain")
    assert repository.root == directory.resolve()


def test_git_repository_with_remote(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "git@github.com:acme/shop.git",
        ],
        check=True,
    )
    nested = tmp_path / "sub"
    nested.mkdir()
    repository = identify(nested)
    assert (repository.owner, repository.repo) == ("acme", "shop")
    assert repository.root == tmp_path.resolve()
