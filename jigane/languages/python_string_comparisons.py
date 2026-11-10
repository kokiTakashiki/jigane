"""文字列のリテラルとの比較の抽出（Python）。構文木（ast）だけから読み、実行も評価もしない。"""

import ast
from collections.abc import Iterator

from jigane.languages.base import ComparisonKind, StringComparison
from jigane.languages.python_ast import read_python

_SEQUENCE_NODES = (ast.Tuple, ast.Set, ast.List)
_SET_CONSTRUCTORS = frozenset({"frozenset", "set", "tuple", "list"})
_REGEX_FUNCTIONS = frozenset({"match", "search", "fullmatch", "findall", "finditer"})
_AFFIX_KINDS = {"startswith": ComparisonKind.PREFIX, "endswith": ComparisonKind.SUFFIX}


def _string(node: ast.AST) -> str | None:
    """空でない文字列のリテラルならその値。空文字列は何にも一致する接頭辞になるので除く。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return node.value
    return None


def _literal_strings(node: ast.AST) -> list[str] | None:
    """並びのリテラル（`frozenset({...})` のような包みを含む）が全部文字列なら、その要素。"""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _SET_CONSTRUCTORS
        and len(node.args) == 1
    ):
        node = node.args[0]
    if not isinstance(node, _SEQUENCE_NODES):
        return None
    values = [_string(element) for element in node.elts]
    if not values or any(value is None for value in values):
        return None
    return [value for value in values if value is not None]


def _named_sequences(tree: ast.Module) -> dict[str, list[str]]:
    """モジュール直下で `名前 = 並びのリテラル` と書かれた組。`in 名前` の解決に使う。"""
    found: dict[str, list[str]] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            target, value = statement.target, statement.value
        else:
            # 代入以外の文は名前に組を結びつけない
            continue
        if isinstance(target, ast.Name) and (strings := _literal_strings(value)) is not None:
            found[target.id] = strings
    return found


def _compare(
    node: ast.Compare, named: dict[str, list[str]]
) -> Iterator[tuple[str, ComparisonKind, str]]:
    operands = [node.left, *node.comparators]
    for operator, left, right in zip(node.ops, operands, operands[1:], strict=False):
        if isinstance(operator, ast.Eq | ast.NotEq):
            for side, other in ((left, right), (right, left)):
                if (value := _string(side)) is not None:
                    # 両辺がリテラルの比べは、比べている相手の式が無い
                    subject = "" if _string(other) is not None else ast.unparse(other)
                    yield value, ComparisonKind.EQUALS, subject
        elif isinstance(operator, ast.In | ast.NotIn):
            strings = _literal_strings(right)
            if strings is None and isinstance(right, ast.Name):
                strings = named.get(right.id)
            for value in strings or []:
                yield value, ComparisonKind.MEMBERSHIP, ast.unparse(left)
        else:
            # 大小の比較や同一性の判定は、文字列の値の写しの手がかりにならない
            continue


def _call(node: ast.Call) -> Iterator[tuple[str, ComparisonKind, str]]:
    func = node.func
    if not isinstance(func, ast.Attribute) or not node.args:
        return
    first = node.args[0]
    if func.attr in _AFFIX_KINDS:
        strings = _literal_strings(first)
        if strings is None and (single := _string(first)) is not None:
            strings = [single]
        for value in strings or []:
            yield value, _AFFIX_KINDS[func.attr], ast.unparse(func.value)
    elif (
        func.attr in _REGEX_FUNCTIONS
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
        and (pattern := _string(first)) is not None
    ):
        yield (
            pattern,
            ComparisonKind.REGEX,
            ast.unparse(node.args[1]) if len(node.args) > 1 else "",
        )


def _walk_comparisons(tree: ast.Module, path: str) -> list[StringComparison]:
    named = _named_sequences(tree)
    found: list[StringComparison] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            pairs = _compare(node, named)
        elif isinstance(node, ast.Call):
            pairs = _call(node)
        else:
            continue
        found += [
            StringComparison(value, kind, path, node.lineno, subject)
            for value, kind, subject in pairs
        ]
    return sorted(found, key=lambda comparison: comparison.line)


def string_comparisons(source: str, path: str) -> list[StringComparison]:
    """ファイルの文字列との比較を、行の順に返す。構文として読めないファイルは空。"""
    found = read_python(source, lambda tree: _walk_comparisons(tree, path))
    if found is None:
        # 途中まで拾った比較は返さず、そのファイルを丸ごと飛ばす
        return []
    return found
