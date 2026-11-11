from pathlib import Path

from tree_sitter import Tree

from jigane.git.tree_files import read_tree_bytes
from jigane.languages.base import LanguageAdapter, parse


def read_and_parse(root: Path, path: str, adapter: LanguageAdapter) -> tuple[bytes, Tree] | None:
    """木の中のファイルを読み、アダプタで構文解析する。地図の組み立てと読めない記述の検出が共有する。

    木の外へ出るリンクなど、読めないファイルは None。
    """
    data = read_tree_bytes(root, path)
    if data is None:
        return None
    return data, parse(adapter, data)
