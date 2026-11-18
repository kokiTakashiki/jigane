"""head の木の `.gitattributes` から、`linguist-generated` を書いた行を取り出す。"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.revision import CommitSha
from jigane.git.tree_files import MAX_REVIEW_FILE_BYTES

GITATTRIBUTES = ".gitattributes"
_MARK = "linguist-generated"


@dataclass(frozen=True)
class MarkLine:
    """`.gitattributes` の一行が書いた印。"""

    file: str
    pattern: str
    mark: str


@dataclass(frozen=True)
class AttributeLines:
    """head の木の全ての `.gitattributes` から取った印の行と、大きすぎて読まなかったファイル。"""

    lines: tuple[MarkLine, ...]
    oversized: tuple[str, ...]


def _is_mark(token: str) -> bool:
    return token in (_MARK, f"-{_MARK}") or token.startswith(f"{_MARK}=")


def _mark_lines(file: str, text: str) -> list[MarkLine]:
    """一つの `.gitattributes` の本文から、印を書いた行を取る。マクロ属性は辿らない。"""
    found: list[MarkLine] = []
    for raw in text.splitlines():
        tokens = raw.split()
        if not tokens or tokens[0].startswith("#"):
            continue
        pattern, *attributes = tokens
        found.extend(MarkLine(file, pattern, token) for token in attributes if _is_mark(token))
    return found


def _listed_files(root: Path, head: CommitSha) -> list[tuple[str, int]]:
    """head の木の `.gitattributes` のパスと大きさ。"""
    output = process.run_command_bytes(
        build_git_command(GitSubcommand.LS_TREE, options=["-r", "-l", "-z"], revisions=[head]),
        cwd=root,
    )
    listed = []
    for entry in output.split(b"\0"):
        meta, tab, raw_path = entry.partition(b"\t")
        if not tab:
            continue
        path = raw_path.decode("utf-8", errors="replace")
        size = meta.split()[-1]
        # 大きさの欄が数でないのはサブモジュールなど blob でない項目で、本文を持たない
        if PurePosixPath(path).name == GITATTRIBUTES and size.isdigit():
            listed.append((path, int(size)))
    return listed


def read_attribute_lines(root: Path, head: CommitSha) -> AttributeLines | None:
    """head の木の `.gitattributes` の印の行。git が失敗したら None。"""
    lines: list[MarkLine] = []
    oversized: list[str] = []
    try:
        for path, size in _listed_files(root, head):
            if size > MAX_REVIEW_FILE_BYTES:
                oversized.append(path)
                continue
            blob = process.run_command_bytes(
                build_git_command(
                    GitSubcommand.CAT_FILE, options=["blob"], revision_paths=[(head, path)]
                ),
                cwd=root,
            )
            lines.extend(_mark_lines(path, blob.decode("utf-8", errors="replace")))
    except process.CommandError as error:
        if error.missing_executable:
            raise
        return None
    return AttributeLines(tuple(lines), tuple(oversized))
