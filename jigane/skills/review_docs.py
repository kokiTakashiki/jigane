"""レビューの入口で、そのレビューの読み先を決めて文書を読む。"""

from jigane.groups.definitions import GroupId
from jigane.repository_docs.reader import (
    BaseSource,
    BaseTree,
    HeadOfCheckout,
    RepositoryDocs,
    read_repository_docs,
)
from jigane.reviews.lifecycle import Review


def read_docs_for_review(review: Review, group: GroupId) -> RepositoryDocs:
    """PR と範囲は起点の木から、ファイルと設計書は HEAD のコミットから読む。"""
    base_tree = review.base_tree_path
    base: BaseSource = HeadOfCheckout() if base_tree is None else BaseTree(base_tree)
    return read_repository_docs([group], review.id.owner, review.id.repo, base)
