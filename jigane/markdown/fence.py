"""本文を、本文の中のバッククォートで閉じられない柵で囲む。"""

import re
import unicodedata
from collections.abc import Iterable


def fence(text: str, info: str = "") -> str:
    """本文に含まれる最長の連続したバッククォートより長い柵（最低 3）で、本文を囲む。

    info は開きの柵の直後に付ける情報文字列。
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    bar = "`" * max(3, longest + 1)
    return f"{bar}{info}\n{text}\n{bar}"


def one_line(value: str) -> str:
    r"""見出しへ入れる値の制御文字（Unicode の Cc）を `\n` のように見える形にする。

    見出しは柵の外に置くため、リポジトリ由来のパスに改行があると、柵の外へ行を差し込める。
    """
    return "".join(
        char.encode("unicode_escape").decode("ascii")
        if unicodedata.category(char) == "Cc"
        else char
        for char in value
    )


def path_bullets(paths: Iterable[str]) -> str:
    """パスを、制御文字を見える形にして一行ずつの箇条書きにする。柵の外に出すパスの一覧に使う。"""
    return "\n".join(f"- {one_line(path)}" for path in paths)
