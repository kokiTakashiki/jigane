"""凍結した dataclass の欄に list の注記が戻らないことを縛る。

凍結が止めるのは欄の再代入だけで、欄の list の中身は書き換えられる。確定した値の欄は
tuple[X, ...] で持ち、組み立ての途中だけ list で溜める。

既製の ruff の規則では足りない。RUF008、RUF009、RUF012 は
既定値（可変の値、関数の呼び出し）を見る規則で、既定値の無い `list[X]` の注記は
捕まえない。これを `ruff rule` の説明で確かめた。
"""

import ast
from pathlib import Path

from ast_support import parse

import jigane

PACKAGE_ROOT = Path(jigane.__file__).parent

# 除く欄は (クラス名, 欄の名前) の組で、理由を添える。実在しない組は試験が落とす。
EXCLUDED_FIELDS = {
    # Callable の戻り値の list は欄の入れ物ではなく、関数の型である。
    ("ManifestKind", "dependencies"): "Callable の戻り値が list",
    ("ManifestKind", "platforms"): "Callable の戻り値が list",
}


def _is_frozen_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name == "dataclass" and any(
            keyword.arg == "frozen"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        ):
            return True
    return False


def _mentions_list(annotation: ast.expr) -> bool:
    """注記のどこかに list か typing.List が現れるか。入れ子と添字の無い形も見る。"""
    return any(
        (isinstance(node, ast.Name) and node.id in {"list", "List"})
        or (isinstance(node, ast.Attribute) and node.attr == "List")
        for node in ast.walk(annotation)
    )


def _list_annotated_fields() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        for node in ast.walk(parse(path)):
            if not (isinstance(node, ast.ClassDef) and _is_frozen_dataclass(node)):
                continue
            for statement in node.body:
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and _mentions_list(statement.annotation)
                ):
                    found.add((node.name, statement.target.id))
    return found


def test_frozen_dataclass_fields_have_no_list_annotation() -> None:
    assert _list_annotated_fields() - set(EXCLUDED_FIELDS) == set()


def test_excluded_fields_still_exist() -> None:
    assert set(EXCLUDED_FIELDS) - _list_annotated_fields() == set()
