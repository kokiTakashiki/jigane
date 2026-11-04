"""値の組の抽出（Python）。構文木（ast）だけから読み、実行も評価もしない。

区別（`ValueSet.kind`）は、
宣言の名前を取り出せた並びを名前に束ねた定義（NAMED）、
名前が取れず、
並びが代入（`=`、
注釈付き、
複合、
`:=`）の右辺にも含まれない並びを名前の無いリテラル（LITERAL）、
名前は取れないが代入の右辺の中にある並び（分解の代入、
辞書の値など）を未決（UNDETERMINED）とする。
代入の左辺（変数、属性）と列挙型のクラス名が名前。
関数の引数や戻り値に直に書いた並びは LITERAL。
名前を取れなかったことは、リテラルである証拠にならない。
名前の綴りは見ない。
"""

import ast
from collections.abc import Iterator

from jigane.languages.base import ValueSet
from jigane.languages.value_set_core import MAX_VALUE_LENGTH, MIN_VALUES, ordered_value_sets

_SEQUENCE_NODES = (ast.Tuple, ast.Set, ast.List)
_SET_CONSTRUCTORS = frozenset({"frozenset", "set", "tuple", "list"})


def _short_string(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return None
    return node.value if 0 < len(node.value) <= MAX_VALUE_LENGTH else None


def _literal_values(elements: list[ast.expr]) -> frozenset[str] | None:
    """要素がすべて短い文字列のリテラルなら、その集合。一つでも外れれば None。"""
    values = [_short_string(element) for element in elements]
    if len(values) < MIN_VALUES or any(value is None for value in values):
        return None
    return frozenset(value for value in values if value is not None)


def _enum_values(node: ast.ClassDef) -> frozenset[str] | None:
    """列挙型の本体で、`名前 = "値"` の代入だけが並ぶとき、その値の集合。"""
    members = [
        statement for statement in node.body if isinstance(statement, ast.Assign | ast.AnnAssign)
    ]
    values = [
        _short_string(member.value) if member.value is not None else None for member in members
    ]
    if len(values) < MIN_VALUES or any(value is None for value in values):
        return None
    return frozenset(value for value in values if value is not None)


def _is_enum(node: ast.ClassDef) -> bool:
    names = {
        base.id if isinstance(base, ast.Name) else getattr(base, "attr", "") for base in node.bases
    }
    return any(name.endswith("Enum") for name in names)


def _constructor_argument(node: ast.Call) -> ast.expr | None:
    """`frozenset({...})` のような、並びのリテラルを一つ受ける呼び出しの引数。"""
    if (
        isinstance(node.func, ast.Name)
        and node.func.id in _SET_CONSTRUCTORS
        and len(node.args) == 1
    ):
        return node.args[0]
    return None


def _target_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _assigned_names(tree: ast.Module) -> dict[int, str]:
    """代入の右辺の節点（の id）から、左辺の名前への対応。分解の代入や名前の無い左辺は入れない。"""
    names: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if (name := _target_name(target)) is not None:
            names[id(value)] = name
    return names


def _assigned_subtree_ids(tree: ast.Module) -> set[int]:
    """代入の右辺の部分木に入る節点の id。ast に親は無いので、右辺から下を集める。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr)
            and node.value is not None
        ):
            ids.update(id(inner) for inner in ast.walk(node.value))
    return ids


def _found(tree: ast.Module) -> Iterator[tuple[frozenset[str], int, str | None, bool]]:
    wrapped: set[int] = set()
    names = _assigned_names(tree)
    assigned = _assigned_subtree_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (argument := _constructor_argument(node)) is not None:
            # 呼び出しの中の並びは、呼び出しの位置で一度だけ数える
            wrapped.add(id(argument))
            if isinstance(argument, _SEQUENCE_NODES) and (
                values := _literal_values(argument.elts)
            ):
                yield values, node.lineno, names.get(id(node)), id(node) in assigned
        elif isinstance(node, _SEQUENCE_NODES) and id(node) not in wrapped:
            if isinstance(getattr(node, "ctx", None), ast.Store):
                # 代入の左辺の分解は値の並びではない
                continue
            if values := _literal_values(node.elts):
                yield values, node.lineno, names.get(id(node)), id(node) in assigned
        elif isinstance(node, ast.ClassDef) and _is_enum(node) and (values := _enum_values(node)):
            yield values, node.lineno, node.name, False


def value_sets(source: str, path: str) -> list[ValueSet]:
    """ファイルの値の組を、定義の行の順に返す。構文として読めないファイルは空。"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError, MemoryError, ValueError):
        # 読めないファイルの組は拾えない。表は他のファイルの組で成り立つので、止めない。
        # 入れ子の深い式は RecursionError、巨大な入力は MemoryError、
        # NUL 文字は ValueError で落ちる。
        # parse の後の走査は ast.walk なので深さで再帰せず、ここで囲めば足りる
        return []
    return ordered_value_sets(_found(tree), path)
