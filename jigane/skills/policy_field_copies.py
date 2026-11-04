"""方針のレビューに渡す、足した型と同じ項目を持つ型の表。構文木だけから読む。"""

import ast
from dataclasses import dataclass
from pathlib import Path

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.git.tree_files import read_tree_text, work_files
from jigane.policy_materials import changed_files
from jigane.reviews.lifecycle import Review

SECTION_FIELD_COPIES = "足した型と同じ項目を持つ型"
INTRO = (
    "変更で足した、または項目が変わった型（クラスの本体の型注記の項目、dataclass、NamedTuple、TypedDict、"
    "列挙の値）ごとに、変更後の木の別の型で項目の名前を二つ以上共有するものを一行ずつ示す。"
    "項目の名前の照合で数える。意味が同じとは限らない。"
)
HEADER = (
    "足した型",
    "定義の場所",
    "同じ項目を持つ型",
    "その定義の場所",
    "共通の項目の名前",
    "共通の数",
)
# 共有する項目の名前の最小の数
MIN_COMMON = 2


ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})


@dataclass(frozen=True)
class TypeFields:
    """トップレベルの型一つの、宣言した項目の名前（宣言の順）。"""

    name: str
    path: str
    line: int
    fields: tuple[str, ...]


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


def _types_of(root: Path, path: str) -> list[TypeFields]:
    """Python のファイルのトップレベルの型。Python 以外と、読めないファイルは空。"""
    if not path.endswith(".py"):
        return []
    text = read_tree_text(root, path)
    if text is None:
        return []
    try:
        module = ast.parse(text)
    except (SyntaxError, RecursionError, MemoryError, ValueError):
        # 構文として読めないファイルの型は、表に載せられない。
        # 入れ子の深い式は RecursionError、巨大な入力は MemoryError、
        # NUL 文字は ValueError で落ちる。
        # parse の後は module.body の直下しか読まないので、ここで囲めば足りる
        return []
    return [
        TypeFields(node.name, path, node.lineno, fields)
        for node in module.body
        if isinstance(node, ast.ClassDef) and (fields := _field_names(node))
    ]


def _public(fields: tuple[str, ...]) -> frozenset[str]:
    return frozenset(name for name in fields if not name.startswith("_"))


@dataclass(frozen=True)
class _Copy:
    added: TypeFields
    other: TypeFields
    common: tuple[str, ...]


def _copies(review: Review) -> list[_Copy]:
    if not review.is_diff or review.base_tree_path is None:
        # 「足した」は変更の前後の比べで決まるので、差分の無いレビューには行が無い
        return []
    root = Path(review.repository_root)
    base_root = Path(review.base_tree_path)
    after_changed = sorted(
        path for path, entry in changed_files.file_changes(review).items() if entry.exists_after
    )
    before: dict[tuple[str, str], frozenset[str]] = {}
    for path in changed_files.file_changes(review, before=True):
        for item in _types_of(base_root, path):
            before[(item.path, item.name)] = _public(item.fields)
    # 比べる相手は、変更後の木の追跡されたファイルすべての型
    everything = [item for path in work_files(root).files for item in _types_of(root, path)]
    copies: list[_Copy] = []
    for path in after_changed:
        for item in _types_of(root, path):
            mine = _public(item.fields)
            if not mine or before.get((item.path, item.name)) == mine:
                continue
            for other in everything:
                if (other.path, other.name) == (item.path, item.name):
                    continue
                shared = mine & _public(other.fields)
                common = tuple(name for name in item.fields if name in shared)
                if len(common) >= MIN_COMMON:
                    copies.append(_Copy(item, other, common))
    return copies


def _place(item: TypeFields) -> str:
    return f"{item.path}:{item.line}"


def field_copy_table(review: Review) -> TableFigure | None:
    """足した型と同じ項目を持つ型の表。該当する型が無ければ None。"""
    copies = _copies(review)
    if not copies:
        return None
    rows = tuple(
        TableRow(
            (
                f"`{copy.added.name}`",
                _place(copy.added),
                f"`{copy.other.name}`",
                _place(copy.other),
                "、".join(f"`{name}`" for name in copy.common),
                str(len(copy.common)),
            ),
            RowChange.UNCHANGED,
        )
        for copy in copies
    )
    return TableFigure(INTRO, HEADER, rows)
