"""方針のレビューに渡す、保存物の書く側と読む側の表。鍵の抽出は言語の層（`StoreAccessReader`）が行う。

保存物は、JSON と CSV と TSV のファイルの名前の定数で束ねるか、
`to_json` と `from_json` を持つ型で束ねる。
ファイル名が引数で渡るなどで名前では束ねられないものは、
書く側と読む側が同じ列挙型、または同じファイルの定数を鍵に使うときに限り、その語彙で束ねる。
関数の本体は鍵の集合だけを読み、値は読まない。
"""

from dataclasses import dataclass, field
from pathlib import Path

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.git.tree_files import read_tree_text, walk_tree_files
from jigane.languages import registry
from jigane.languages.base import StoreAccessReader, StoreFormat, StoreRole, StoreSide
from jigane.policy_materials import changed_files
from jigane.reviews.lifecycle import Review

SECTION_STORES = "保存物の書く側と読む側"
INTRO = (
    "変更したファイルの関数が書く、または読む保存物"
    "（JSON、CSV、TSV のファイル、`to_json` と `from_json` を持つ型）ごとに、"
    "書く側と読む側の鍵を並べた。"
    "リポジトリ全体の Python から、同じ保存物の相手側も拾った。"
    "構文木だけから読み、値は評価していない。"
    "ファイル名が引数で渡るなどで名前では束ねられない保存物は、"
    "書く側と読む側が同じ列挙型（またはモジュールの定数）を鍵に使うとき、"
    "その語彙の名前（例: `Column` の列を持つ CSV）で束ねた。"
    "列挙型のメンバーとモジュールの定数で書いた鍵は、リポジトリ内の定義から文字列の値に解いた。"
    "書く側の鍵は、書き出す関数の中の鍵の辞書リテラルと CSV の見出し"
    "（見出しを返す関数の本体と、書き出しを担う関数へ渡した見出しを一段辿る）、"
    "`asdict(self)` の項目。"
    "読む側の鍵は、`json.load` と `csv.DictReader` の結果、"
    'または `from_json` の引数から辿った値への `x["鍵"]`、'
    '`x.get("鍵")`、`"鍵" in x`。入れ子の鍵は区別しない。'
    "「書くだけの鍵」は読む側が参照しない鍵、"
    "「読むだけの鍵」は書く側が書かない鍵で、食い違いはこの二つの欄に出る。"
    "相手側の関数が見つからなければ「（見つからない）」。"
    "テストのファイルは載せない。"
)
HEADER = (
    "保存物",
    "書く側（関数: 鍵）",
    "読む側（関数: 鍵、作る型）",
    "書くだけの鍵",
    "読むだけの鍵",
)
NOT_FOUND = "（見つからない）"
PAIR_LABEL = "（to_json と from_json）"


@dataclass
class _Store:
    writers: list[StoreSide] = field(default_factory=list)
    readers: list[StoreSide] = field(default_factory=list)


def _collect(root: Path, paths: list[str], changed: set[str]) -> dict[str, _Store]:
    stores: dict[str, _Store] = {}
    touched: set[str] = set()
    by_reader: dict[int, tuple[StoreAccessReader, dict[str, str]]] = {}
    for path in paths:
        adapter = registry.adapter_for(path)
        text = read_tree_text(root, path)
        if not isinstance(adapter, StoreAccessReader) or text is None:
            continue
        by_reader.setdefault(id(adapter), (adapter, {}))[1][path] = text
    for reader_adapter, sources in by_reader.values():
        for access in reader_adapter.store_accesses(sources):
            names = set(access.names)
            if access.pair_owner is not None:
                names.add(f"{access.pair_owner}{PAIR_LABEL}")
            if not names:
                # ファイル名で束ねられない側は、鍵に使った語彙が相手側と同じときだけ束ねる
                names = {
                    f"{label} の{'列' if side.kind is StoreFormat.CSV else '鍵'}を持つ {side.kind}"
                    for side in access.sides
                    for label in side.labels
                }
            for name in names:
                store = stores.setdefault(name, _Store())
                for side in access.sides:
                    (store.writers if side.role is StoreRole.WRITE else store.readers).append(side)
                if access.path in changed:
                    touched.add(name)
    return {name: store for name, store in sorted(stores.items()) if name in touched}


def _cell(sides: list[StoreSide], *, with_types: bool) -> str:
    if not sides:
        return NOT_FOUND
    parts = []
    for side in sorted(sides, key=lambda s: (s.path, s.function)):
        detail = ", ".join(sorted(side.keys))
        if with_types and side.types:
            detail += f"（作る型: {', '.join(sorted(side.types))}）"
        parts.append(f"{side.function}: {detail}" if detail else side.function)
    return "; ".join(parts)


def _union(sides: list[StoreSide]) -> set[str]:
    return set().union(*(s.keys for s in sides)) if sides else set()


def store_table(review: Review) -> TableFigure | None:
    """保存物の書く側と読む側の表。変更が保存物に触らなければ None。"""
    root = Path(review.repository_root)
    files = changed_files.file_changes(review)
    changed = {
        p
        for p, entry in files.items()
        if entry.exists_after and isinstance(registry.adapter_for(p), StoreAccessReader)
    }
    if not changed:
        return None
    adapters = registry.adapters()
    paths = [
        p
        for p in walk_tree_files(root, root)
        if isinstance(registry.adapter_for(p), StoreAccessReader)
        and not registry.is_test_path(p, adapters)
    ]
    rows = []
    for name, store in _collect(root, paths, changed).items():
        written, read = _union(store.writers), _union(store.readers)
        rows.append(
            TableRow(
                (
                    name,
                    _cell(store.writers, with_types=False),
                    _cell(store.readers, with_types=True),
                    ", ".join(sorted(written - read)) if store.readers else "",
                    ", ".join(sorted(read - written)) if store.writers else "",
                ),
                RowChange.UNCHANGED,
            )
        )
    return TableFigure(INTRO, HEADER, tuple(rows)) if rows else None
