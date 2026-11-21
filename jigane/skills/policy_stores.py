"""方針のレビューに渡す、保存物の書く側と読む側の表。鍵の抽出は言語の層（`StoreAccessExtractor`）が行う。

保存物は、JSON と CSV と TSV のファイルの名前の定数で束ねるか、
`to_json` と `from_json` を持つ型で束ねる。
ファイル名が引数で渡るなどで名前では束ねられないものは、
書く側と読む側が同じ列挙型、または同じファイルの定数を鍵に使うときに限り、その語彙で束ねる。
関数の本体は鍵の集合だけを読み、値は読まない。
"""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.git.tree_files import read_tree_text, walk_tree_files
from jigane.languages import registry
from jigane.languages.base import (
    PARAMETER_STORE_MARK,
    StoreAccessExtractor,
    StoreFormat,
    StoreRole,
    StoreSide,
)
from jigane.policy_materials import changed_files
from jigane.policy_materials.compact import ChangeNodes
from jigane.reviews.lifecycle import Review
from jigane.sequence.indexes import Indexes
from jigane.skills.assembly import AssemblyInput

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
    "ファイル名が固定の文字列で、リポジトリの中に相手側の関数が無ければ「（見つからない）」。"
    "テストのファイルは載せない。"
    "ファイル名が関数の引数で渡る保存物は、「関数名 の引数 引数名」の名前で関数ごとに載せた。"
    "「形式」の欄は、JSON、CSV、または独自の形式。"
    "独自の形式は、開いたファイルへ文字列を組んで書き（連結、f 文字列、join）、"
    "`split` で読み戻す形で、区切りや列の並びは載せていない。"
    "独自の形式は、書く側と読む側の片方が特定できないことがある。"
    "ファイル名が引数で渡る保存物も、形式によらず、相手側は呼ぶ側の引数の先にあって拾えない。"
    "そのときは「見つからない」でなく「特定できていない」と書く。"
    "\n\nSwift は、`UserDefaults.standard` の `set` と `removeObject` を書く側、"
    "`string(forKey:)` などの `forKey:` の読み込みを読む側とし、"
    "一つの保存物「UserDefaults」にまとめた。"
    "鍵は、文字列リテラル、型の `static let` の定数、"
    "`String` を生の値に持つ列挙型の case（`.rawValue`）から文字列に解いた。"
    "引数で渡るなど解けない鍵は、鍵の欄に載せていない。"
    "Codable の JSON は、`JSONEncoder()` の `encode` を書く側、"
    "`JSONDecoder()` の `decode(型.self, from:)` を読む側とし、"
    "型の名前から「型 の JSON」の保存物にまとめた。"
    "鍵は、`Codable`、`Encodable`、`Decodable` を継承する構造体とクラスの格納プロパティの名前。"
    "型の注釈の無い値の `encode` は数えていない。"
    "Keychain は、`SecItemAdd`、`SecItemUpdate`、`SecItemDelete` を書く側、"
    "`SecItemCopyMatching` を読む側とし、一つの保存物「Keychain」にまとめた。"
    "鍵は、問い合わせの辞書の `kSecAttrAccount` の値。"
)
HEADER = (
    "保存物",
    "書く側（関数: 鍵）",
    "読む側（関数: 鍵、作る型）",
    "書くだけの鍵",
    "読むだけの鍵",
    "形式",
)
NOT_FOUND = "（見つからない）"
UNIDENTIFIED = "（特定できていない）"
PAIR_LABEL = "（to_json と from_json）"


@dataclass
class _Store:
    writers: list[StoreSide] = field(default_factory=list)
    readers: list[StoreSide] = field(default_factory=list)


def _collect(root: Path, paths: list[str], changed: set[str]) -> dict[str, _Store]:
    stores: dict[str, _Store] = {}
    touched: set[str] = set()
    by_reader: dict[int, tuple[StoreAccessExtractor, dict[str, str]]] = {}
    for path in paths:
        adapter = registry.adapter_for(path)
        text = read_tree_text(root, path)
        if not isinstance(adapter, StoreAccessExtractor) or text is None:
            continue
        by_reader.setdefault(id(adapter), (adapter, {}))[1][path] = text
    for reader_adapter, sources in by_reader.values():
        for access in reader_adapter.store_accesses(sources):
            names = set(access.file_names)
            if access.pair_owner_type_name is not None:
                names.add(f"{access.pair_owner_type_name}{PAIR_LABEL}")
            if not names:
                # ファイル名で束ねられない側は、鍵に使った語彙が相手側と同じときだけ束ねる
                names = {
                    f"{label} の{'列' if side.format is StoreFormat.CSV else '鍵'}"
                    f"を持つ {side.format}"
                    for side in access.sides
                    for label in side.key_vocabulary_names
                }
            for name in names:
                store = stores.setdefault(name, _Store())
                for side in access.sides:
                    (store.writers if side.role is StoreRole.WRITE else store.readers).append(side)
                if access.path in changed:
                    touched.add(name)
    return {name: store for name, store in sorted(stores.items()) if name in touched}


def _cell(
    sides: list[StoreSide], *, with_types: bool, other: list[StoreSide], by_parameter: bool
) -> str:
    if not sides:
        # 独自の形式は読み書きの相手を拾う手がかりが弱く、無いと言い切れない。
        # 引数で渡るファイルの相手は、呼ぶ側の引数の先にあり、この関数の名前からは辿れない
        has_custom = any(s.format is StoreFormat.CUSTOM for s in other)
        return UNIDENTIFIED if has_custom or by_parameter else NOT_FOUND
    parts = []
    for side in sorted(sides, key=lambda s: (s.path, s.function_qualified_name)):
        detail = ", ".join(sorted(side.keys))
        if with_types and side.created_type_names:
            detail += f"（作る型: {', '.join(sorted(side.created_type_names))}）"
        parts.append(
            f"{side.function_qualified_name}: {detail}" if detail else side.function_qualified_name
        )
    return "; ".join(parts)


def _union(sides: list[StoreSide]) -> set[str]:
    return set().union(*(s.keys for s in sides)) if sides else set()


def _touched_stores(review: Review) -> dict[str, _Store]:
    root = Path(review.repository_root)
    files = changed_files.file_changes(review)
    changed = {
        p
        for p, entry in files.items()
        if entry.exists_after and isinstance(registry.adapter_for(p), StoreAccessExtractor)
    }
    if not changed:
        return {}
    adapters = registry.adapters()
    paths = [
        p
        for p in walk_tree_files(root, root)
        if isinstance(registry.adapter_for(p), StoreAccessExtractor)
        and not registry.is_test_path(p, adapters)
    ]
    return _collect(root, paths, changed)


def store_table(review: Review) -> TableFigure | None:
    """保存物の書く側と読む側の表。変更が保存物に触らなければ None。"""
    return _table(_touched_stores(review))


def shared_store_table(assembly: AssemblyInput) -> TableFigure | None:
    return _table(_shared_stores(assembly))


def _shared_stores(assembly: AssemblyInput) -> dict[str, _Store]:
    return assembly.remember("touched_stores", lambda: _touched_stores(assembly.review))


def _table(stores: dict[str, _Store]) -> TableFigure | None:
    rows = []
    for name, store in stores.items():
        by_parameter = PARAMETER_STORE_MARK in name
        written, read = _union(store.writers), _union(store.readers)
        rows.append(
            TableRow(
                (
                    name,
                    _cell(
                        store.writers,
                        with_types=False,
                        other=store.readers,
                        by_parameter=by_parameter,
                    ),
                    _cell(
                        store.readers,
                        with_types=True,
                        other=store.writers,
                        by_parameter=by_parameter,
                    ),
                    ", ".join(sorted(written - read)) if store.readers else "",
                    ", ".join(sorted(read - written)) if store.writers else "",
                    "、".join(sorted({s.format for s in [*store.writers, *store.readers]})),
                ),
                RowChange.UNCHANGED,
            )
        )
    return TableFigure(INTRO, HEADER, tuple(rows)) if rows else None


def _compact_side(
    sides: list[StoreSide], other: list[StoreSide], nodes: ChangeNodes, *, by_parameter: bool
) -> str:
    if not sides:
        return _cell(sides, with_types=False, other=other, by_parameter=by_parameter)
    places = {(nodes.path(side.path), side.function_qualified_name) for side in sides}
    return ", ".join(f"{path} {function}" for path, function in sorted(places))


def _side_locations(indexes: Indexes) -> dict[tuple[str, str], set[int]]:
    locations: dict[tuple[str, str], set[int]] = defaultdict(set)
    for index in indexes.indexes:
        for member in index.callables.values():
            if member.declaration is not None:
                locations[(member.path, member.declaration.qualified_name)].add(member.line)
    return locations


def _side_evidence(
    store: _Store,
    nodes: ChangeNodes,
    locations: dict[tuple[str, str], set[int]],
    *,
    by_parameter: bool,
) -> list[str]:
    lines = []
    for label, sides, other in (
        ("書", store.writers, store.readers),
        ("読", store.readers, store.writers),
    ):
        if not sides:
            absence = _cell(sides, with_types=False, other=other, by_parameter=by_parameter)
            lines.append(f"  {label} {absence}")
        for side in sorted(sides, key=lambda s: (s.path, s.function_qualified_name)):
            name = side.function_qualified_name
            found = locations.get((side.path, name), set())
            line_numbers = ",".join(str(line) for line in sorted(found))
            place = f"{nodes.path(side.path)}:{line_numbers}" if found else nodes.path(side.path)
            keys = ", ".join(sorted(side.keys)) or "（鍵の抽出なし）"
            lines.append(f"  {label} {place} {name}: {keys}")
    return lines


def compact_text(
    assembly: AssemblyInput, nodes: ChangeNodes, *, indexes: Indexes | None = None
) -> str:
    """保存物ごとに鍵を示す。索引を受ける P5 には関数別の鍵と宣言位置も載せる。"""
    stores = _shared_stores(assembly)
    if not stores:
        return ""
    locations = _side_locations(indexes) if indexes is not None else None
    heading = (
        "書く・読む関数と保存物単位の鍵。関数別の鍵、値、入れ子、未解決の鍵、テストは省略。"
        if locations is None
        else "書く・読む関数、照合できた宣言位置、関数別と保存物単位の鍵。"
        "値、入れ子、未解決の鍵、テストは省略。"
    )
    lines = [f"### {SECTION_STORES}", heading]
    for name, store in stores.items():
        by_parameter = PARAMETER_STORE_MARK in name
        written, read = _union(store.writers), _union(store.readers)
        formats = "、".join(sorted({side.format for side in (*store.writers, *store.readers)}))
        lines.append(f"{name} ({formats})")
        if locations is None:
            writers = _compact_side(store.writers, store.readers, nodes, by_parameter=by_parameter)
            readers = _compact_side(store.readers, store.writers, nodes, by_parameter=by_parameter)
            lines.append(f"  書 {writers} / 読 {readers}")
        else:
            lines.extend(_side_evidence(store, nodes, locations, by_parameter=by_parameter))
        for label, keys in (
            ("両", written & read),
            ("書", written - read),
            ("読", read - written),
        ):
            if keys:
                lines.append(f"  鍵 {label} {' '.join(sorted(keys))}")
        created = {name for side in store.readers for name in side.created_type_names}
        if created:
            lines.append(f"  作る型 {' '.join(sorted(created))}")
    return "\n".join(lines)
