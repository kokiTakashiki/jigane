from collections.abc import Iterable
from pathlib import PurePosixPath

DOCUMENT_SUFFIXES = frozenset({".md", ".rst", ".txt"})

DOCUMENT_ONLY_NOTICE = (
    "変更したファイル（削除を除く）がすべて文書です。方針の群は図と表だけを読み、ファイルの本文を読みません。"
    "設計書は jigane new design-doc <パス> でレビューしてください。"
)


def is_document_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in DOCUMENT_SUFFIXES


def all_documents(paths: Iterable[str]) -> bool:
    """全ファイルが文書か。空は文書だけと見なさない。"""
    listed = list(paths)
    return bool(listed) and all(is_document_path(path) for path in listed)
