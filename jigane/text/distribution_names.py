import re


def normalize_distribution(name: str) -> str:
    """配布名の PEP 503 正規化。大文字小文字と `-`、`_`、`.` の違いを同じ配布として扱う。"""
    return re.sub(r"[-_.]+", "-", name).lower()
