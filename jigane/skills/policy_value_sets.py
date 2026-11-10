"""方針のレビューに渡す、値の組の表。

追跡されたファイルから値の組を集め（`policy_materials.value_sets`）、変更したファイルが
組の定義、値を含むファイル、文字列で比べている箇所のどれかに当たる組だけを並べる。
同じ値を何か所にも書き写している手がかりとして読む。
"""

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.judgment_map.changes import CHANGES_FILE, split_file_sections
from jigane.languages.base import ComparisonKind, DefinitionKind
from jigane.policy_materials.value_sets import (
    FileKind,
    ValueSetLocation,
    ValueSetRow,
    kind_of,
    value_set_rows,
)
from jigane.reviews.catalog import scope_files
from jigane.reviews.lifecycle import Review
from jigane.reviews.stored_pairs import load_pairs
from jigane.skills.assembly import AssemblyInput

SECTION_VALUE_SETS = "値の組の表"
INTRO = (
    "同じ値の組を、複数のファイルに書き写していないかの手がかり。"
    "追跡されたファイルから、短い文字列だけの並び（tuple、set、list、列挙型）を集め、"
    "リポジトリの中で珍しい値だけを残して、値の重なる組を一つにまとめた。"
    "変更したファイルが、組の定義、値を含むファイル、文字列で比べている箇所のいずれかに当たる組だけを載せた。"
    "定義の無い組は、`if` の連鎖などで同じ式を三つ以上の異なる値と比べているだけの組で、"
    "定義の欄に比べている式と最初の場所を書いた。"
    "定義の欄の場所には、名前に束ねた定義（代入した変数、定数、列挙型）なら続けてその名前を、"
    "名前の無いリテラルの写しなら（リテラル）を添えた。構文から決められない言語と場所は場所だけを出す。"
    "コード、文書、テストは、組の値を半数以上含むファイルの数。"
    "比較は、値との等値、`in`、`startswith` と `endswith`、正規表現の一致の箇所で、"
    "数に続けて `パス:行 形` を並べた。形は、等値、所属、接頭辞、接尾辞、正規表現。"
    "この変更での触れ方は、差分で足した行に、組の定義、参照、文字列での比較のどれがあるかを、"
    "定義、参照、比較の順に並べた。参照は、テスト以外のファイルの足した行に、"
    "値の文字列か組の名前のどちらかが現れること。"
    "名前は、定義と同じファイルの行か、定義のモジュールで修飾した形、"
    "定義のモジュールから取り込んだファイルの行に現れたときに限る。"
    "並びは、テストでないファイルにある定義の数が多い順、同じならコードと文書の数の合計が多い順。"
)
HEADER = ("定義", "値", "コード", "文書", "テスト", "文字列での比較", "この変更での触れ方")
NO_TOUCH = "なし"
HUNK_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
# 表に出す行の上限。超えた分は注記に件数だけを出す
MAX_ROWS = 12
# 値の列に出す値の数。残りは件数だけ示す
SHOWN_VALUES = 6
# 定義と比較の列に出す場所の数。残りは件数だけ示す
MAX_LOCATIONS = 3
# 比較の形を表に出す語
COMPARISON_WORDS = {
    ComparisonKind.EQUALS: "等値",
    ComparisonKind.MEMBERSHIP: "所属",
    ComparisonKind.PREFIX: "接頭辞",
    ComparisonKind.SUFFIX: "接尾辞",
    ComparisonKind.REGEX: "正規表現",
}


def _touched_paths(row: ValueSetRow) -> set[str]:
    return (
        {definition.path for definition in row.definitions}
        | row.containing_paths
        | {comparison.path for comparison in row.comparisons}
    )


def _added_lines(review: Review) -> dict[str, dict[int, str]]:
    """ファイルごとの、差分で足した行（新しい側の行番号と本文）。差分のレビューだけが持つ。"""
    if not review.is_diff:
        # ファイルのレビューには差分が無く、足した行という見方が成り立たない
        return {}
    directory = review.id.directory()
    changes = (directory / CHANGES_FILE).read_text(encoding="utf-8")
    pairs = load_pairs(review.id)
    added: dict[str, dict[int, str]] = {}
    for pair, section in zip(pairs, split_file_sections(changes, pairs), strict=True):
        if pair.new_path is None:
            continue
        lines = added.setdefault(pair.new_path, {})
        number = 0
        # 見出しの 2 行は読み飛ばす
        for line in section[2:]:
            hunk = HUNK_PATTERN.match(line)
            if hunk:
                number = int(hunk.group(1))
            elif line.startswith("+"):
                lines[number] = line[1:]
                number += 1
            elif line.startswith(" "):
                number += 1
            else:
                # 削除の行（-）と省略の注記は、新しい側の行を進めない
                pass
    return added


@dataclass(frozen=True)
class ValueSetTouch:
    """一つの値の組が、この変更の差分で足した行に触れられているかどうか。"""

    defined: bool
    referenced: bool
    compared: bool


def _module_of(path: str) -> str:
    return path.rsplit(".", 1)[0].replace("/", ".") if "." in path else path.replace("/", ".")


def _stem_of(path: str) -> str:
    return PurePosixPath(path).stem


def _word(text: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(text)}\b")


def _imports_name(definition: ValueSetLocation, name: str, body: str) -> bool:
    """本文に、定義のモジュールからその名前を取り込む行があるか。"""
    stem = _stem_of(definition.path)
    if definition.path.endswith(".py"):
        pattern = re.compile(
            rf"^\s*from\s+{re.escape(_module_of(definition.path))}\s+import\b[^\n]*\b{re.escape(name)}\b",
            re.MULTILINE,
        )
        return pattern.search(body) is not None
    stem_pattern, name_pattern = _word(stem), _word(name)
    return any(
        line.lstrip().startswith("import")
        and stem_pattern.search(line) is not None
        and name_pattern.search(line) is not None
        for line in body.splitlines()
    )


def _name_referenced(definition: ValueSetLocation, path: str, line_body: str, root: Path) -> bool:
    name = definition.name
    if name is None:
        return False
    if path == definition.path:
        return _word(name).search(line_body) is not None
    if re.search(rf"\b{re.escape(_stem_of(definition.path))}\.{re.escape(name)}\b", line_body):
        return True
    if _word(name).search(line_body) is None:
        return False
    try:
        body = (root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # 本文が読めなければ取り込みを確かめられないので、参照とは数えない
        return False
    return _imports_name(definition, name, body)


def value_set_touch(
    row: ValueSetRow, added: dict[str, dict[int, str]], root: Path
) -> ValueSetTouch:
    def is_added(path: str, line: int) -> bool:
        return line in added.get(path, {})

    defined = any(is_added(d.path, d.line) for d in row.definitions)
    compared = any(is_added(c.path, c.line) for c in row.comparisons)
    counted = {(d.path, d.line) for d in row.definitions} | {
        (c.path, c.line) for c in row.comparisons
    }
    quoted = [f"{quote}{value}{quote}" for value in row.values for quote in ('"', "'")]
    referenced = any(
        (path, number) not in counted
        and (
            any(text in body for text in quoted)
            or any(_name_referenced(d, path, body, root) for d in row.definitions)
        )
        for path, lines in added.items()
        if kind_of(path) != FileKind.TEST
        for number, body in lines.items()
    )
    return ValueSetTouch(defined, referenced, compared)


def _touch_text(touch: ValueSetTouch) -> str:
    names = [
        name
        for name, hit in (
            ("定義", touch.defined),
            ("参照", touch.referenced),
            ("比較", touch.compared),
        )
        if hit
    ]
    return "、".join(names) if names else NO_TOUCH


def _touch_cell(row: ValueSetRow, added: dict[str, dict[int, str]], root: Path) -> str:
    return _touch_text(value_set_touch(row, added, root))


def _with_remainder(places: list[str]) -> str:
    shown = ", ".join(places[:MAX_LOCATIONS])
    rest = len(places) - MAX_LOCATIONS
    return f"{shown} ほか {rest} 件" if rest > 0 else shown


def _definition_cell(row: ValueSetRow) -> str:
    if row.subject:
        first = row.comparisons[0]
        return f"定義なし（{first.path}:{first.line} の `{row.subject}` と比べるだけ）"
    return _with_remainder([_definition_place(d) for d in row.definitions])


def _definition_place(definition: ValueSetLocation) -> str:
    place = f"{definition.path}:{definition.line}"
    if definition.kind == DefinitionKind.NAMED and definition.name:
        return f"{place} {definition.name}"
    if definition.kind == DefinitionKind.LITERAL:
        return f"{place}（リテラル）"
    # 区別を決めていない場所は、場所だけを出す
    return place


def _comparison_cell(row: ValueSetRow) -> str:
    # テストでないファイルを先に、続けてパスと行の順
    ordered = sorted(
        row.comparisons,
        key=lambda c: (kind_of(c.path) == FileKind.TEST, c.path, c.line),
    )
    places = [f"{c.path}:{c.line} {COMPARISON_WORDS[c.kind]}" for c in ordered]
    if not places:
        return "0"
    return f"{len(places)} ({_with_remainder(places)})"


def _values_cell(row: ValueSetRow) -> str:
    shown = ", ".join(f"`{value}`" for value in row.values[:SHOWN_VALUES])
    rest = len(row.values) - SHOWN_VALUES
    return f"{shown} ほか {rest} 個" if rest > 0 else shown


def _selected_rows(
    review: Review, all_rows: tuple[ValueSetRow, ...], max_rows: int
) -> tuple[list[ValueSetRow], int]:
    """表に載せる行（上限まで）と、上限で省いた件数。表と触れ方の一覧で同じ組を使う。"""
    changed = scope_files(review)
    # 値の組は履歴を使わないので、変更後の木だけから読む
    rows = [row for row in all_rows if changed & _touched_paths(row)]
    # テストの数は、コードと文書の合計が並んだ行の順位づけにだけ使う
    ordered = sorted(
        rows,
        key=lambda row: (
            -sum(1 for d in row.definitions if kind_of(d.path) != FileKind.TEST),
            -(row.files.code + row.files.document),
            -row.files.test,
            [(d.path, d.line) for d in row.definitions],
        ),
    )
    return ordered[:max_rows], len(ordered) - max_rows


def value_set_table(review: Review, max_rows: int = MAX_ROWS) -> TableFigure | None:
    """値の組の表。変更したファイルに関わる組が無ければ None。"""
    return _table(review, value_set_rows(Path(review.repository_root)), max_rows)


def value_set_table_of(assembly: AssemblyInput, max_rows: int = MAX_ROWS) -> TableFigure | None:
    """`value_set_table` と同じ表。値の組の読み込みは assembly ごとに一度で済ませる。"""
    root = Path(assembly.review.repository_root)
    rows = assembly.remember(("value_set_rows", root), lambda: value_set_rows(root))
    return _table(assembly.review, rows, max_rows)


def _table(review: Review, all_rows: tuple[ValueSetRow, ...], max_rows: int) -> TableFigure | None:
    """表を組む。all_rows は変更したファイルで絞る前の、リポジトリの全行。"""
    shown, over = _selected_rows(review, all_rows, max_rows)
    if not shown:
        return None
    added = _added_lines(review)
    root = Path(review.repository_root)
    table_rows = tuple(
        TableRow(
            (
                _definition_cell(row),
                _values_cell(row),
                str(row.files.code),
                str(row.files.document),
                str(row.files.test),
                _comparison_cell(row),
                _touch_text(value_set_touch(row, added, root)),
            ),
            RowChange.UNCHANGED,
        )
        for row in shown
    )
    omitted = max(over, 0)
    note = f"上限を超えた {omitted} 件は載せていない。" if omitted else None
    return TableFigure(INTRO, HEADER, table_rows, note)
