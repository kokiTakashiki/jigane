"""変更前（基点）の木を読み、変更した宣言と本体を持つ宣言の引き表を作る。"""

from dataclasses import dataclass

from jigane.gates.tree_side import TreeSide
from jigane.git.tree_files import walk_tree_files
from jigane.languages.base import SequenceAdapter
from jigane.reviews.lifecycle import Review
from jigane.sequence.index import Index, Member, build_index
from jigane.sequence.roots import Key, changed_members


@dataclass(frozen=True)
class BeforeTree:
    """変更前の木の索引と、そこでの変更した宣言、修飾名から引く本体を持つ宣言。"""

    index: Index
    changed: dict[Key, Member]
    by_name: dict[str, tuple[Member, ...]]


def load_before_tree(
    review: Review, adapter: SequenceAdapter, *, include_tests: bool
) -> BeforeTree | None:
    """基点の木が無いレビューでは None を返す。"""
    base_root = review.base_tree_path
    if base_root is None:
        return None
    index = build_index(base_root, walk_tree_files(base_root, base_root), adapter)
    # 変更前の集合は、判断の地図の segment の変更前の側（old_path と old_span）から作る
    changed = changed_members(
        index, review.map_kind.segments, adapter, include_tests=include_tests, side=TreeSide.BEFORE
    )
    collected: dict[str, list[Member]] = {}
    for member in index.callables.values():
        if member.declaration is not None and member.declaration.body is not None:
            collected.setdefault(member.qualified_name, []).append(member)
    by_name = {name: tuple(members) for name, members in collected.items()}
    return BeforeTree(index, changed, by_name)
