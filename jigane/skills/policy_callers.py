"""方針のレビューに渡す、足した公開の口と呼ぶ側の表。

Python のファイルだけを ast で読み、他の言語は節ごと省く。

変更で足した、または引数を足した公開の名前を一行ずつ示し、変更の後の木の中の呼ぶ側を名前で照合して数える。
名前と数だけを渡し、コードの文は渡さない。変更前の木を持たないファイルのレビューは、足したものが定まらないので描かない。
"""

from pathlib import Path

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.git.tree_files import read_tree_text, walk_tree_files
from jigane.policy_materials import changed_files
from jigane.reviews.lifecycle import Review
from jigane.skills.policy_callers_ast import PublicName, PublicNameKind, added_public_names

SECTION_CALLERS = "足した公開の口と呼ぶ側"
INTRO = (
    "変更の後の木（after）と前の木（before）を比べ、変更で足した、または引数を足した公開の名前"
    "（先頭が `_` でない関数、メソッド、クラス、列挙の値、引数）を一行ずつ示す。"
    "呼ぶ側は after の木の中で名前の照合で数える。"
    "呼ぶ側が 0 のものは、この変更の木の中に使う箇所が無い。"
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


def _is_python(path: str) -> bool:
    return path.endswith(".py")


def caller_table(review: Review) -> TableFigure | None:
    """足した公開の口と呼ぶ側の表。Python の足した名前が無い、または変更前の木が無ければ None。"""
    if not review.is_diff or review.base_tree_path is None:
        return None
    root = Path(review.repository_root)
    changed = {
        path
        for path, entry in changed_files.file_changes(review).items()
        if entry.exists_after and _is_python(path)
    }
    before_paths = [
        path for path in changed_files.file_changes(review, before=True) if _is_python(path)
    ]
    names = added_public_names(
        _sources(Path(review.base_tree_path), before_paths),
        _sources(root, [path for path in walk_tree_files(root, root) if _is_python(path)]),
        changed,
    )
    rows = tuple(_row(name) for name in names)
    return TableFigure(INTRO, HEADER, rows) if rows else None
