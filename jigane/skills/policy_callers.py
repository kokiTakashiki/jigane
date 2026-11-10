"""方針のレビューに渡す、足した公開の口と呼ぶ側の表。

公開の名前の読みは言語のアダプタの口（`PublicNameReader`）に任せ、口を持たない言語のファイルは読まない。

変更で足した、または引数を足した公開の名前を一行ずつ示し、変更の後の木の中の呼ぶ側を名前で照合して数える。
名前と数だけを渡し、コードの文は渡さない。変更前の木を持たないファイルのレビューは、足したものが定まらないので描かない。
"""

from collections.abc import Iterable
from pathlib import Path

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.git.tree_files import read_tree_text, walk_tree_files
from jigane.languages import registry
from jigane.languages.base import PublicName, PublicNameKind, PublicNameReader
from jigane.policy_materials import changed_files
from jigane.reviews.lifecycle import Review

SECTION_CALLERS = "足した公開の口と呼ぶ側"
INTRO = (
    "変更の後の木（after）と前の木（before）を比べ、変更で足した、または引数を足した公開の名前"
    "（先頭が `_` でない関数、メソッド、クラス、列挙の値、引数）を一行ずつ示す。"
    "呼ぶ側は after の木の中で名前だけを照合して数え、型は解決しないので、"
    "同じ名前の別の定義を使う箇所も数える。"
    "クラスは、生成と継承に加えて、型注記と式の中で名前を書いた箇所を数える。"
    "省略形の列挙の値（Swift の `.a`）と、文字列で書いた型注記（Python）は数えない。"
    "C# のメソッドグループ（呼び出さずに名前だけ渡す形）も数えない。"
    "Java の `switch` の裸の列挙の定数（`case RED:`）とメソッド参照も数えない。"
    "Python の入れ子のクラスは行にならない。"
    "呼ぶ側が 0 でも、数えない形で使っていることがある。"
)
HEADER = ("名前", "種類", "定義の場所", "呼ぶ側の数", "呼ぶ側")
NO_CALLER = "なし"
_KIND_WORDS = {
    PublicNameKind.FUNCTION: "関数",
    PublicNameKind.METHOD: "メソッド",
    PublicNameKind.CLASS: "クラス",
    PublicNameKind.ENUM_VALUE: "列挙の値",
    PublicNameKind.PARAMETER: "引数",
}


def _sources(root: Path, paths: list[str]) -> dict[str, str]:
    texts = {}
    for path in paths:
        text = read_tree_text(root, path)
        if text is not None:
            texts[path] = text
    return texts


def _row(name: PublicName) -> TableRow:
    callers = "、".join(f"`{caller}`" for caller in name.callers) or NO_CALLER
    return TableRow(
        (
            f"`{name.name}`",
            _KIND_WORDS[name.kind],
            f"{name.path}:{name.line}",
            str(len(name.callers)),
            callers,
        ),
        RowChange.UNCHANGED,
    )


def _by_reader(paths: Iterable[str]) -> dict[str, tuple[PublicNameReader, list[str]]]:
    groups: dict[str, tuple[PublicNameReader, list[str]]] = {}
    for path in paths:
        adapter = registry.adapter_for(path)
        if isinstance(adapter, PublicNameReader):
            groups.setdefault(adapter.name, (adapter, []))[1].append(path)
    return groups


def caller_table(review: Review) -> TableFigure | None:
    """足した公開の口と呼ぶ側の表。足した名前が無い、または変更前の木が無ければ None。"""
    if not review.is_diff or review.base_tree_path is None:
        return None
    root = Path(review.repository_root)
    changed_by_reader = _by_reader(
        path for path, entry in changed_files.file_changes(review).items() if entry.exists_after
    )
    before_by_reader = _by_reader(changed_files.file_changes(review, before=True))
    tree_by_reader = _by_reader(walk_tree_files(root, root))
    names: list[PublicName] = []
    for key, (reader, changed) in changed_by_reader.items():
        before_paths = before_by_reader[key][1] if key in before_by_reader else []
        tree_paths = tree_by_reader[key][1] if key in tree_by_reader else []
        names += reader.added_public_names(
            _sources(Path(review.base_tree_path), before_paths),
            _sources(root, tree_paths),
            set(changed),
        )
    rows = tuple(_row(name) for name in names)
    return TableFigure(INTRO, HEADER, rows) if rows else None
