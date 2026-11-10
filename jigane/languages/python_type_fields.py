"""型の項目の名前の抽出（Python）。標準ライブラリの `ast` だけから読み、実行も評価もしない。

項目はクラス本体の型注記の宣言で、
列挙型（`Enum` 系の基底を持つクラス）では代入の左辺の名前も項目とする。
"""

import ast

from jigane.languages.base import TypeFields
from jigane.languages.python_ast import parse_python

ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})


def _is_enum(cls: ast.ClassDef) -> bool:
    names = {
        base.id if isinstance(base, ast.Name) else base.attr
        for base in cls.bases
        if isinstance(base, ast.Name | ast.Attribute)
    }
    return bool(names & ENUM_BASES)


def _field_names(cls: ast.ClassDef) -> tuple[str, ...]:
    enum = _is_enum(cls)
    names: list[str] = []
    for item in cls.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            names.append(item.target.id)
        elif enum and isinstance(item, ast.Assign):
            names += [t.id for t in item.targets if isinstance(t, ast.Name)]
    return tuple(dict.fromkeys(names))


def type_fields_of(text: str, path: str) -> list[TypeFields]:
    """ファイルのトップレベルの型を、文書順に返す。読めないファイルは空。"""
    module = parse_python(text)
    if module is None:
        # parse の後は module.body の直下しか読まないので、parse の失敗だけを見れば足りる
        return []
    return [
        TypeFields(node.name, path, node.lineno, fields)
        for node in module.body
        if isinstance(node, ast.ClassDef) and (fields := _field_names(node))
    ]
