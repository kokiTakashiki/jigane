"""Python のモジュール直下で、空でない文字列を代入している定数を構文木から取り出す。

モジュール直下に加えて、最上位の `if` と `try` の内側（`if TYPE_CHECKING:` や `try: ... except:`
の本体、`else`、`finally`）の代入も拾う。その中の入れ子の `if` と `try` も同じ。

`NAME = "x"`、`NAME: str = "x"`、`A = B = "x"` と、括弧や行の継続をまたぐ代入を拾う。
暗黙の連結（`"a" "b"`）は一つの文字列として読む。関数、クラス、ループ、`with` の中の代入、
f 文字列、文字列でない値は拾わない。

排他な `if` と `else` の両方の枝で同じ名前を代入したときは、両方を文書順に返す。
名前で引く側では後に現れた方が残り、実行時にどちらの枝が通るかと食い違いうる。
"""

import ast
from collections.abc import Iterator

from jigane.languages.base import StringConstant
from jigane.languages.python_ast import read_python


def _non_empty_string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return node.value
    return None


def _top_level_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    for statement in body:
        match statement:
            case ast.If(body=inner, orelse=other):
                yield from _top_level_statements(inner)
                yield from _top_level_statements(other)
            case (
                ast.Try(body=inner, handlers=handlers, orelse=other, finalbody=last)
                | ast.TryStar(body=inner, handlers=handlers, orelse=other, finalbody=last)
            ):
                yield from _top_level_statements(inner)
                for handler in handlers:
                    yield from _top_level_statements(handler.body)
                yield from _top_level_statements(other)
                yield from _top_level_statements(last)
            case _:
                yield statement


def _collect(tree: ast.Module) -> list[StringConstant]:
    found: list[StringConstant] = []
    for statement in _top_level_statements(tree.body):
        match statement:
            case ast.Assign(targets=targets, value=value):
                names = [t.id for t in targets if isinstance(t, ast.Name)]
            case ast.AnnAssign(target=ast.Name(id=name), value=value):
                names = [name]
            case _:
                # 代入でない文には、定数の宣言が無い
                continue
        if (text := _non_empty_string(value)) is not None:
            found.extend(StringConstant(name, text, statement.lineno) for name in names)
    return found


def string_constants(source: str) -> list[StringConstant]:
    """モジュール直下の文字列定数。読めないファイルは空。"""
    return read_python(source, _collect) or []
