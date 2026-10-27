"""正規表現の書き場所を読める言語のファイルを、木から読む部品。"""

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node

from jigane.git.tree_files import read_tree_bytes
from jigane.languages import registry
from jigane.languages.base import RegexAdapter, RegexScan, parse


@dataclass(frozen=True)
class RegexSource:
    adapter: RegexAdapter
    data: bytes
    root: Node

    def scan(self, path: str) -> RegexScan:
        return self.adapter.regex_literals(self.root, self.data, path)


def read_regex_source(root: Path, path: str) -> RegexSource | None:
    """正規表現の書き場所を読めない言語のファイル、木の外のファイル、読めないファイルは None。"""
    adapter = registry.adapter_for(path)
    data = read_tree_bytes(root, path)
    if not isinstance(adapter, RegexAdapter) or data is None:
        return None
    return RegexSource(adapter, data, parse(adapter, data).root_node)
