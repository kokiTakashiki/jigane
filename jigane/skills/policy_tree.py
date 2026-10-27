"""方針の資料を作る三つの材料が共有する、読み取り専用の木。木の全ファイルは作るときに一度だけ読む。"""

from dataclasses import dataclass
from pathlib import Path

from jigane.git.tree_files import read_tree_text, walk_tree_files
from jigane.languages import registry
from jigane.languages.base import LanguageAdapter
from jigane.reviews.lifecycle import Review

# これより大きいファイルは本文を読まない。バイナリ（NUL を含むもの）も読まない。
# 三つの材料のうち最も厳しい基準に揃えてある
MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class TreeFile:
    path: str
    text: str
    # 言語を判別できないファイルは None
    adapter: LanguageAdapter | None
    is_test: bool


@dataclass(frozen=True)
class PolicyTree:
    root: Path
    # 木の通常のファイルすべて。本文を読まなかったものも含む
    paths: tuple[str, ...]
    # 本文を読めたファイルだけ
    files: dict[str, TreeFile]

    def source_files(self) -> list[TreeFile]:
        """言語を判別できるファイル。テストも含む。"""
        return [f for f in self.files.values() if f.adapter is not None]


@dataclass(frozen=True)
class ReviewTrees:
    after: PolicyTree
    # 変更前の木。機能レビュー式には無い
    before: PolicyTree | None


def read_tree(root: Path) -> PolicyTree:
    adapters = registry.adapters()
    paths = walk_tree_files(root, root)
    files: dict[str, TreeFile] = {}
    for path in paths:
        if (root / path).stat().st_size > MAX_FILE_BYTES:
            continue
        text = read_tree_text(root, path)
        if text is None or "\x00" in text:
            continue
        files[path] = TreeFile(
            path, text, registry.adapter_for(path), registry.is_test_path(path, adapters)
        )
    return PolicyTree(root, tuple(paths), files)


def trees_of(review: Review) -> ReviewTrees:
    """レビューの木。一回の資料づくりで一度だけ作り、三つの材料へ渡す。"""
    after = read_tree(Path(review.repository_root))
    before = None
    base_root = review.base_tree_path
    if base_root is not None:
        before = read_tree(base_root)
    return ReviewTrees(after, before)
