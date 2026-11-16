from pathlib import PurePosixPath


def suffix_of(path: str) -> str:
    """パスの拡張子を小文字で返す。区切りのバックスラッシュもスラッシュとして扱う。"""
    return PurePosixPath(path.replace("\\", "/")).suffix.lower()
