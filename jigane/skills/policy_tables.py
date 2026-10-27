"""方針のレビューに渡す、変更したファイルから読み取った項目の表の共通の骨組み。"""

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.policy_materials import changed_files
from jigane.reviews.lifecycle import Review

# 行の欄に付ける、変更の印
MARKS = {
    RowChange.UNCHANGED: "",
    RowChange.ADDED: "+",
    RowChange.REMOVED: "-",
    RowChange.CHANGED: "~",
}


@dataclass(frozen=True)
class TableHeading:
    """表の見出し。description は表の前に置く説明、header は列の名前。"""

    description: str
    header: tuple[str, ...]


def diff_table[Item, Key: Hashable](
    review: Review,
    *,
    extract: Callable[[Path, str], list[Item]],
    key: Callable[[Item], Key],
    changed: Callable[[Item, Item], bool],
    row: Callable[[Item, str], tuple[str, ...]],
    sort_key: Callable[[Item], tuple[str | int, ...]],
    heading: TableHeading,
) -> TableFigure | None:
    """変更したファイルから `extract` で読んだ項目の表。項目が一つも無ければ None。

    差分レビュー式は変更後の木で読み、変更前の木の同じファイルと同じ鍵で比べて印を付ける。
    機能レビュー式は変更後の木だけで、印は付かない。
    `extract` は木の根とファイルのパスを受け、拾わないファイルには空の並びを返す。
    """
    root = Path(review.repository_root)
    files = changed_files.file_changes(review)
    after_paths = sorted(path for path, entry in files.items() if entry.exists_after)
    # 同じ鍵の項目は複数ありうる（同じ変数を読んでから書く、など）。順に一つずつ対応させる
    before: dict[Key, list[Item]] = {}
    if review.base_tree_path is not None:
        base_root = Path(review.base_tree_path)
        for path in sorted(changed_files.file_changes(review, before=True)):
            for item in extract(base_root, path):
                before.setdefault(key(item), []).append(item)
    entries: list[tuple[Item, RowChange]] = []
    matched: set[tuple[Key, int]] = set()
    for path in after_paths:
        for item in extract(root, path):
            item_key = key(item)
            candidates = [
                (index, old)
                for index, old in enumerate(before.get(item_key, []))
                if (item_key, index) not in matched
            ]
            change = RowChange.UNCHANGED
            if not candidates:
                if review.is_diff:
                    change = RowChange.ADDED
            else:
                # 変わっていない相手がいれば変更していない行。いなければ変更前の先頭と対応させる
                same = [c for c in candidates if not changed(c[1], item)]
                index = (same or candidates)[0][0]
                matched.add((item_key, index))
                if not same:
                    change = RowChange.CHANGED
            entries.append((item, change))
    entries += [
        (old, RowChange.REMOVED)
        for k, olds in before.items()
        for index, old in enumerate(olds)
        if (k, index) not in matched
    ]
    if not entries:
        return None
    entries.sort(key=lambda entry: sort_key(entry[0]))
    return TableFigure(
        heading.description,
        heading.header,
        tuple(TableRow(row(item, MARKS[change]), change) for item, change in entries),
    )
