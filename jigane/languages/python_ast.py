"""Python のファイルを構文木（ast）として読む入口。読めないファイルを飛ばす知識を持つ。"""

import ast
from collections.abc import Callable

# 構文の誤りに加え、入れ子の深い式は parse と ast.unparse と再帰する走査で RecursionError、
# 巨大な入力は MemoryError、NUL 文字は ValueError で落ちる
UNREADABLE = (SyntaxError, RecursionError, MemoryError, ValueError)


def read_python[Collected](
    source: str, collect: Callable[[ast.Module], Collected]
) -> Collected | None:
    """構文木を作って `collect` で集める。作るか集めるかで落ちたら、途中までの結果を返さず None。

    集める側が深い式で落ちるかどうかは、集める走査の中でしか分からない。
    落ちるファイルを別の走査で先に見つけず、本番の走査の例外をここで受ける。
    """
    try:
        return collect(ast.parse(source))
    except UNREADABLE:
        # 読めないファイルの結果は拾えない。表は他のファイルで成り立つので、呼ぶ側は止めない
        return None


def parse_python(source: str) -> ast.Module | None:
    """構文木だけを作る。読めないファイルは None。"""
    return read_python(source, lambda tree: tree)
