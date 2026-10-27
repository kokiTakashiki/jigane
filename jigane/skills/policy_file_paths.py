"""方針のレビューに渡す、書き出す（または読む）ファイルの名前の組み立ての表。構文木だけから読む。"""

from pathlib import Path

from jigane.figures.models import TableFigure
from jigane.git.tree_files import read_tree_bytes
from jigane.languages import registry
from jigane.languages.base import FilePathAdapter, FilePathPattern, FileUsage, parse
from jigane.reviews.lifecycle import Review
from jigane.skills import policy_tables

SECTION_FILE_PATHS = "ファイルの名前の組み立て"
INTRO = (
    "変更したファイルが書く、または読むファイルの名前の組み立てを、ソースの原文のまま並べた。"
    "式は評価しておらず、`{prefix}` のような置き換えは書かれたまま残る。"
    "受け手が変数のときは、同じ関数の直前の代入の右辺を一段だけ辿った。辿れなければ変数の名前のまま。"
    "「書く」は作る、消すを含み、「読む」は有無の問い合わせを含む。"
    "使い道が「不明」の行は、読み書きの語彙に無い呼び出し（プロジェクトの関数）に渡された式か、"
    "呼び出しに渡らず `Path(...)` や `/` で組まれただけの式。"
    "ファイルの欄の `+` は変更で追加、`-` は削除、`~` は使い道の変更。"
    "テストのファイルは載せない。"
)
HEADER = ("ファイル", "関数", "使い道", "組み立ての原文")
_USAGE_NAMES = {FileUsage.WRITE: "書く", FileUsage.READ: "読む", FileUsage.UNKNOWN: "不明"}
# ファイル、関数、原文の三つ組
type _Key = tuple[str, str, str]


def _patterns_of(root: Path, path: str) -> list[FilePathPattern]:
    # テストの一時ファイルの読み書きは、保存物の名前の契約ではないので載せない
    if registry.is_test_path(path, registry.adapters()):
        return []
    adapter = registry.adapter_for(path)
    data = read_tree_bytes(root, path)
    if not isinstance(adapter, FilePathAdapter) or data is None:
        return []
    return adapter.file_path_patterns(parse(adapter, data).root_node, data, path)


def _key(pattern: FilePathPattern) -> _Key:
    return (pattern.path, pattern.function, pattern.expression)


def _row(pattern: FilePathPattern, mark: str) -> tuple[str, ...]:
    return (
        mark + pattern.path,
        pattern.function or "（モジュール）",
        _USAGE_NAMES[pattern.usage],
        pattern.expression,
    )


def file_path_table(review: Review) -> TableFigure | None:
    """ファイルの名前の組み立ての表。何も拾えなければ None。"""
    return policy_tables.diff_table(
        review,
        extract=_patterns_of,
        key=_key,
        changed=lambda old, new: old.usage != new.usage,
        row=_row,
        sort_key=lambda p: (p.path, p.line),
        heading=policy_tables.TableHeading(INTRO, HEADER),
    )
