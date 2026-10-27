"""ソースを ast で読む検査の共通の補助。

正規表現や文字列の一致は書き方の揺れですり抜けと誤検出を持つため、構文木で読む。
"""

import ast
from pathlib import Path

JSON_CALLS = frozenset({"load", "loads", "dump", "dumps"})


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def without_declaration(tree: ast.Module, name: str) -> ast.Module:
    """名前の定義（`NAME = "..."`）は置き場の宣言であり、読み書きではないので除く。"""
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
        )
    ]
    return tree


def names_stored_file(tree: ast.Module, constant: str, literal: str) -> bool:
    """定数名か、そのファイル名の文字列定数を、ソースが使っているか。"""
    return any(
        (isinstance(node, ast.Name) and node.id == constant)
        or (isinstance(node, ast.Constant) and node.value == literal)
        for node in ast.walk(tree)
    )


def calls_json(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "json"
        and node.attr in JSON_CALLS
        for node in ast.walk(tree)
    )


def reads_or_writes_stored_file(source: str, constant: str, literal: str) -> bool:
    """保存物のファイルを、ソースが json で直接読み書きしているか。"""
    tree = without_declaration(ast.parse(source), constant)
    return names_stored_file(tree, constant, literal) and calls_json(tree)


def json_users_of_stored_file(
    root: Path, constant: str, literal: str, exclude: frozenset[str] = frozenset()
) -> list[str]:
    """保存物のファイルを json で直接読み書きするソースの、root からの相対パス。

    exclude に挙げたファイル名のソースは見ない。
    """
    return [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*.py"))
        if path.name not in exclude
        and reads_or_writes_stored_file(path.read_text(encoding="utf-8"), constant, literal)
    ]


def _assigns_state_key(target: ast.expr, keys: frozenset[str]) -> bool:
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_assigns_state_key(element, keys) for element in target.elts)
    if isinstance(target, ast.Starred):
        return _assigns_state_key(target.value, keys)
    current = target
    while isinstance(current, ast.Subscript):
        inner = current.value
        if (
            isinstance(inner, ast.Name)
            and inner.id == "state"
            and isinstance(current.slice, ast.Constant)
            and current.slice.value in keys
        ):
            return True
        current = inner
    return False


def edits_state_by_key(source: str, keys: frozenset[str]) -> bool:
    """`state["<key>"]...` を代入（拡張代入、タプルやリストへの分解を含む）の左辺にしているか。"""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        else:
            continue
        if any(_assigns_state_key(target, keys) for target in targets):
            return True
    return False
