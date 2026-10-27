"""変更の文。二つのツリーから difflib で作る統一形式の差分。"""

import difflib
from collections.abc import Sequence
from pathlib import Path

from jigane.git.tree_files import MAX_REVIEW_FILE_BYTES, TreeFileTooLargeError, read_tree_bytes
from jigane.judgment_map.classification import FilePair, is_whole_deletion, matches_any
from jigane.judgment_map.configuration import Config
from jigane.preconditions.required import required

CHANGES_FILE = "changes.diff"
# 全体の文字数の上限。超えた後のファイルは見出しと省略の 1 行だけにする。
CHANGES_CHAR_LIMIT = 200_000
OMITTED_GENERATED = "省略（生成物）"
OMITTED_BINARY = "省略（バイナリ）"
OMITTED_LIMIT = "省略（上限）"
OMITTED_TOO_LARGE = "省略（大きすぎる）"


def _read_lines(root: Path, path: str | None) -> list[str] | None:
    """UTF-8 で読めないファイルは None。存在しない側（追加の旧、削除の新）は空。

    MAX_REVIEW_FILE_BYTES を超えるファイルは TreeFileTooLargeError を送出する。
    """
    if path is None:
        return []
    data = read_tree_bytes(root, path, max_bytes=MAX_REVIEW_FILE_BYTES)
    if data is None:
        # 木の外へ出るリンクなどは、中身を読まずバイナリと同じく省く
        return None
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _header_lines(pair: FilePair) -> list[str]:
    return [
        f"--- a/{pair.old_path}" if pair.old_path else "--- /dev/null",
        f"+++ b/{pair.new_path}" if pair.new_path else "+++ /dev/null",
    ]


def file_change(base_root: Path, head_root: Path, pair: FilePair, config: Config) -> list[str]:
    path = pair.new_path or pair.old_path
    path = required(path)
    header = _header_lines(pair)
    if matches_any(path, config.exclude):
        return [*header, OMITTED_GENERATED]
    try:
        old_lines = _read_lines(base_root, pair.old_path)
        new_lines = _read_lines(head_root, pair.new_path)
    except TreeFileTooLargeError:
        return [*header, OMITTED_TOO_LARGE]
    if old_lines is None or new_lines is None:
        return [*header, OMITTED_BINARY]
    body = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    # unified_diff の見出し 2 行は、パスの付け方が上の見出しと違うため捨てる
    return header + body[2:]


def render_changes(
    base_root: Path,
    head_root: Path,
    pairs: Sequence[FilePair],
    config: Config,
) -> tuple[str, int]:
    """変更の文と、上限で本文を省いたファイルの数を返す。"""
    all_lines = [file_change(base_root, head_root, pair, config) for pair in pairs]
    # 上限は削除でないファイルから先に割り振る。出力の並びは pairs のままにする。
    order = sorted(range(len(pairs)), key=lambda index: is_whole_deletion(pairs[index]))
    parts: list[str] = [""] * len(pairs)
    used = 0
    omitted = 0
    for index in order:
        lines = all_lines[index]
        text = "\n".join(lines)
        if used + len(text) > CHANGES_CHAR_LIMIT:
            omitted += 1
            text = "\n".join([*lines[:2], OMITTED_LIMIT])
        used += len(text) + 1
        parts[index] = text
    return "".join(f"{part}\n" for part in parts), omitted


def split_file_sections(changes: str, pairs: Sequence[FilePair]) -> list[list[str]]:
    """変更の文を、pairs と同じ順のファイルごとの行の列に分ける。

    本文の行も `--- ` で始まりうるため、見出しの 2 行を pairs から組み直し、次のファイルの見出しと
    一致する位置で切る。
    """
    lines = changes.split("\n")
    if lines and lines[-1] == "":
        # 各ファイルの文は改行で終わるため、末尾の空要素は行ではない
        lines.pop()
    starts: list[int] = []
    position = 0
    for pair in pairs:
        header = _header_lines(pair)
        while lines[position : position + 2] != header:
            position += 1
            if position >= len(lines):
                raise ValueError(f"変更の文にファイルの見出しが見つからない: {header[0]}")
        starts.append(position)
        position += 2
    ends = [*starts[1:], len(lines)]
    return [lines[start:end] for start, end in zip(starts, ends, strict=True)]


def without_whole_deletions(changes: str, pairs: Sequence[FilePair]) -> tuple[str, list[str]]:
    """変更の文から、丸ごと削除したファイルの節を外す。外したファイルの旧パスも返す。"""
    kept: list[str] = []
    removed: list[str] = []
    for pair, section in zip(pairs, split_file_sections(changes, pairs), strict=True):
        if is_whole_deletion(pair):
            removed.append(required(pair.old_path))
        else:
            kept.extend(section)
    return "".join(f"{line}\n" for line in kept), removed
