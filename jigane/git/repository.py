import re
from dataclasses import dataclass
from pathlib import Path

from jigane.git import process

# 依存や生成物のディレクトリは、宣言の探索にもレビューの対象の列挙にも入れない
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "site-packages",
    }
)

_REMOTE_PATTERN = re.compile(r"[:/]([^/:\s]+)/([^/\s]+?)(?:\.git)?/?$")
_INVALID_IDENTIFIER_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class Repository:
    root: Path
    owner: str
    repo: str


def _git(directory: Path, *arguments: str) -> str | None:
    try:
        output = process.run_command(["git", *arguments], cwd=directory)
    except (OSError, process.CommandError):
        # git が入っていない環境も、git でないディレクトリと同じに扱う
        return None
    return output.strip() or None


def parse_remote_url(url: str) -> tuple[str, str] | None:
    match = _REMOTE_PATTERN.search(url.strip())
    if match is None:
        return None
    return match[1], match[2]


def identify_git(directory: Path) -> Repository | None:
    """git のリポジトリに属するディレクトリのリポジトリを返す。git の外なら None。"""
    directory = directory.resolve()
    top_level = _git(directory, "rev-parse", "--show-toplevel")
    if top_level is None:
        return None
    root = Path(top_level).resolve()
    url = _git(root, "remote", "get-url", "origin")
    parsed = parse_remote_url(url) if url else None
    if parsed is None:
        return _local(root)
    return Repository(root, parsed[0], parsed[1])


def identify(directory: Path) -> Repository:
    repository = identify_git(directory)
    if repository is None:
        return _local(directory.resolve())
    return repository


def _local(directory: Path) -> Repository:
    # ReviewId が受け付けない文字は置き換え、先頭の . と - は取り除く
    name = _INVALID_IDENTIFIER_CHARACTERS.sub("-", directory.name).lstrip(".-") or "root"
    return Repository(directory, "local", name)
