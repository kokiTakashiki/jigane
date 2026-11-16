from collections.abc import Iterable

from jigane.text.path_suffix import suffix_of

MARKDOWN_SUFFIX = ".md"
DOCUMENT_SUFFIXES = frozenset({MARKDOWN_SUFFIX, ".rst", ".txt"})

DOCUMENT_ONLY_NOTICE = (
    "変更したファイル（削除を除く）がすべて文書です。方針の群は図と表だけを読み、ファイルの本文を読みません。"
    "設計書は jigane new design-doc <パス> でレビューしてください。"
)


def is_document_path(path: str) -> bool:
    return suffix_of(path) in DOCUMENT_SUFFIXES


def is_markdown_path(path: str) -> bool:
    return suffix_of(path) == MARKDOWN_SUFFIX


def all_documents(paths: Iterable[str]) -> bool:
    """全ファイルが文書か。空は文書だけと見なさない。"""
    listed = list(paths)
    return bool(listed) and all(is_document_path(path) for path in listed)
