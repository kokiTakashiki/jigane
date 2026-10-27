"""検出が対象にする行の範囲。ファイル全体か、パスごとに変えた行か。"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class AllLines:
    """機能レビュー式。どのパスのどの行も対象。"""

    def covers(self, _path: str, _line: int, /) -> bool:
        return True


@dataclass(frozen=True)
class ChangedLines:
    """差分レビュー式。辞書に無いパスは変えた行が無いものとして扱う。"""

    by_path: Mapping[str, frozenset[int]]

    def covers(self, path: str, line: int) -> bool:
        return line in self.by_path.get(path, frozenset())


LineScope = AllLines | ChangedLines
