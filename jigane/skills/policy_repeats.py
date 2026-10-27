"""方針のレビューに渡す、同じ形の繰り返しの資料。

判断の地図と木から決定的に作り、
LLM は使わない。
"""

import difflib
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from tree_sitter import Node

from jigane.git.tree_files import read_tree_text
from jigane.languages import registry
from jigane.languages.base import LanguageAdapter, comment_nodes, import_rows, parse
from jigane.languages.registry import adapter_for
from jigane.markdown.cells import CellKind, code_cell, markdown_table
from jigane.reviews.lifecycle import Review
from jigane.skills.policy_tree import PolicyTree

MIN_LINE_LENGTH = 30
MIN_BLOCK_LINES = 3
# 方針のレビューの資料で、正規化した行の本文を添える塊の行数の上限
POLICY_LINE_LIMIT = 5
IDIOM_COUNT = 10
LINE_CAP = 20
SEED_CAP = 50
EXAMPLE_CAP = 3
BODY_CAP = 120
# アダプタの無いファイルに対する退避。アダプタのあるファイルには掛けない（構文木で判定する）
UNPARSED_COMMENT_PREFIXES = ("#", "//", "/*", "*", "///")
UNPARSED_IMPORT_PATTERN = re.compile(r"^(import|using|from)\s")
NO_REMAINS_TEXT = "消した行と同じ行は、他の場所に残っていない。"
NO_REPEATS_TEXT = "足した行と同じ並びは、他の場所に無い。"
REMAINS_INTRO = (
    "この変更で消した行と同じ行が、変更後の木の他の場所に残っている箇所。"
    "行の一致で数えたので、偶然の一致を含みうる。"
    "テストのファイルは、変更した側からも他の場所からも外した。"
)
REPEATS_INTRO = (
    "この変更で足した行（機能レビュー式では対象のファイルの全行）が、"
    f"{MIN_BLOCK_LINES} 行以上連続して、他の場所にも同じ並びである塊。"
    "正規化した行の連続一致で数えたので、偶然の一致を含みうる。"
    "空行、括弧だけの行、コメント、import は並びを切らずに飛ばして数えた。"
    "行数は飛ばした行を除いた行数で、範囲は元の行番号で示す。"
    "テストのファイルは、変更した側からも他の場所からも外した。"
)
LINES_JOINER = " ⏎ "

# パスと行番号（1 始まり）
Position = tuple[str, int]
# パス、開始行、終了行（どちらの行も含む）
Block = tuple[str, int, int]


def _has_content(text: str) -> bool:
    """正規化した行に、英数字が一つでもあるか。括弧だけの行などを除く。"""
    return any(character.isalnum() for character in text)


def _eligible(text: str) -> bool:
    """一致に使える行が、一行の一致で数える対象になるか。"""
    return len(text) >= MIN_LINE_LENGTH


def _looks_like_comment_or_import(text: str) -> bool:
    """行頭の見かけだけで、コメントか import かを判定する。構文木を作れないファイル用の退避。"""
    return text.startswith(UNPARSED_COMMENT_PREFIXES) or bool(UNPARSED_IMPORT_PATTERN.match(text))


def _comment_rows(adapter: LanguageAdapter, root: Node, data: bytes) -> set[int]:
    """コメントだけでできている行の番号（1 始まり）。"""
    lines = data.split(b"\n")
    rows: set[int] = set()
    for node in comment_nodes(adapter, root):
        first, last = node.start_point.row, node.end_point.row
        for row in range(first, last + 1):
            before_blank = row > first or not lines[row][: node.start_point.column].strip()
            after_blank = row < last or not lines[row][node.end_point.column :].strip()
            if before_blank and after_blank:
                rows.add(row + 1)
    return rows


def _matchable_lines(adapter: LanguageAdapter | None, text: str) -> list[tuple[int, str]]:
    """一致に使える行を（行番号（1 始まり）, 正規化した行）で返す。

    アダプタのあるファイルでは、コメントと import を構文木で除く。アダプタの無いファイルでは、
    行頭の見かけで除く。
    """
    ignored: set[int] | None = None
    if adapter is not None:
        data = text.encode("utf-8")
        root = parse(adapter, data).root_node
        ignored = _comment_rows(adapter, root, data) | import_rows(adapter, root)
    found: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        normalized = raw.strip()
        if not _has_content(normalized):
            continue
        if ignored is None:
            if _looks_like_comment_or_import(normalized):
                continue
        elif number in ignored:
            continue
        found.append((number, normalized))
    return found


# 一致に使える行だけで途切れなく続く範囲。（元の行番号の列, 正規化した行の列）
Segment = tuple[list[int], list[str]]


def _candidates(path: str, text: str, wanted: set[int]) -> dict[int, str]:
    """wanted の行のうち、一致に使える行を、行番号から正規化した行への写像で返す。"""
    found: dict[int, str] = {}
    for numbers, lines in _segments(path, text, wanted):
        found.update(zip(numbers, lines, strict=True))
    return found


def _segments(path: str, text: str, wanted: set[int]) -> list[Segment]:
    """wanted の行の連続。一致に使えない行（空行、括弧だけの行、コメント）は並びを切らずに飛ばす。

    一致に使える行のうち wanted に無い行（変更していない行）は、そこで並びを切る。
    """
    found: list[Segment] = []
    broken = True
    for number, normalized in _matchable_lines(adapter_for(path), text):
        if number not in wanted:
            broken = True
            continue
        if broken:
            found.append(([], []))
            broken = False
        found[-1][0].append(number)
        found[-1][1].append(normalized)
    return found


class _Tree:
    """変更後の木の、テスト以外のファイルの行。共有の木の本文から作る。"""

    def __init__(self, tree: PolicyTree) -> None:
        # 一致に使える行だけの列と、各行の元の行番号（1 始まり）。
        # 一致に使えない行は並びを切らず、列から取り除く
        self.lines: dict[str, list[str]] = {}
        self.numbers: dict[str, list[int]] = {}
        # 一致に使える行から（パス, 上の列の添字）への索引
        self.matchable: dict[str, list[Position]] = defaultdict(list)
        # 一行の一致で数える行（長さの下限あり）から位置への索引
        self.eligible: dict[str, list[Position]] = defaultdict(list)
        for file in tree.source_files():
            if file.is_test:
                continue
            path = file.path
            text = file.text
            kept: list[str] = []
            numbers: list[int] = []
            for number, line in _matchable_lines(file.adapter, text):
                self.matchable[line].append((path, len(kept)))
                if _eligible(line):
                    self.eligible[line].append((path, number))
                kept.append(line)
                numbers.append(number)
            self.lines[path] = kept
            self.numbers[path] = numbers


def _test_paths(review: Review, adapters: Sequence[LanguageAdapter]) -> set[str]:
    """変更した側のパスのうち、テストのファイル。"""
    paths = set(review.paths)
    for segment in review.map_kind.segments:
        paths.update(p for p in (segment.old_path, segment.new_path) if p)
    return {p for p in paths if registry.is_test_path(p, adapters)}


def _feature_added(review: Review, tree: PolicyTree, tests: set[str]) -> dict[str, list[Segment]]:
    added: dict[str, list[Segment]] = {}
    for path in review.paths:
        file = tree.files.get(path)
        if file is None or file.adapter is None or path in tests:
            continue
        added[path] = _segments(path, file.text, set(range(1, len(file.text.splitlines()) + 1)))
    return added


def _diff_lines(
    review: Review, tests: set[str]
) -> tuple[dict[str, list[Position]], dict[str, list[Segment]]]:
    """（消した行, 足した行）。

    消した行は、正規化した行から変更前の木の位置への写像。足した行は、パスから、
    一致に使える行の連続の列への写像で、変更後の木のもの。変更前の木は、判断の地図の
    segments が指す対のファイルを、地図と同じ difflib の比べ方で比べて求める。
    segments の範囲は飛び飛びの変更行を一つにまとめるため、行の特定には使えない。
    """
    root = Path(review.repository_root)
    removed: dict[str, list[Position]] = defaultdict(list)
    added: dict[str, list[Segment]] = {}
    base_root = review.base_tree_path
    if base_root is None:
        return removed, added
    pairs = {(s.old_path, s.new_path) for s in review.map_kind.segments}
    for old_path, new_path in sorted(pairs, key=lambda pair: (pair[0] or "", pair[1] or "")):
        old_text = read_tree_text(base_root, old_path) if old_path else ""
        new_text = read_tree_text(root, new_path) if new_path else ""
        if old_text is None or new_text is None:
            continue
        matcher = difflib.SequenceMatcher(
            None, old_text.splitlines(), new_text.splitlines(), autojunk=False
        )
        old_wanted: set[int] = set()
        new_wanted: set[int] = set()
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                old_wanted.update(range(i1 + 1, i2 + 1))
                new_wanted.update(range(j1 + 1, j2 + 1))
        if old_path and old_path not in tests and adapter_for(old_path) is not None:
            for number, line in _candidates(old_path, old_text, old_wanted).items():
                if _eligible(line):
                    removed[line].append((old_path, number))
        if new_path and new_path not in tests and adapter_for(new_path) is not None:
            added[new_path] = _segments(new_path, new_text, new_wanted)
    return removed, added


def _body(line: str) -> str:
    return line[:BODY_CAP] + ("…" if len(line) > BODY_CAP else "")


def _examples(texts: list[str]) -> str:
    shown = ", ".join(texts[:EXAMPLE_CAP])
    rest = len(texts) - EXAMPLE_CAP
    return f"{shown}（ほか {rest} 件）" if rest > 0 else shown


def _positions(positions: list[Position]) -> str:
    return _examples([f"{path}:{number}" for path, number in positions])


def _span_text(block: Block) -> str:
    path, start, end = block
    return f"{path}:{start}-{end}"


def _intro(line_limit: int | None) -> str:
    """塊の表の冒頭の文。行の本文をどの塊に添えたかを、line_limit に合わせて足す。"""
    if line_limit is None:
        return f"{REPEATS_INTRO}代表の行は、塊の中で最初の {MIN_LINE_LENGTH} 文字以上の行。"
    if line_limit < MIN_BLOCK_LINES:
        return REPEATS_INTRO
    return (
        f"{REPEATS_INTRO}{line_limit} 行以下の塊だけ、正規化した行を「行の本文」に添えた"
        f"（行は {LINES_JOINER.strip()} でつなぐ）。"
    )


def _remains_table(
    removed: dict[str, list[Position]],
    tree: _Tree,
    excluded: set[Position],
    line_limit: int | None,
) -> str:
    rows: list[tuple[int, str, list[Position], list[Position]]] = []
    idioms = 0
    for line, here in removed.items():
        others = [p for p in tree.eligible.get(line, []) if p not in excluded]
        if len(others) > IDIOM_COUNT:
            idioms += 1
        elif others:
            rows.append((len(others), line, sorted(here), others))
    note = (
        f"\n\n他の場所が {IDIOM_COUNT} 件を超える行は慣用句として外した（{idioms} 件）。"
        if idioms
        else ""
    )
    if not rows:
        return NO_REMAINS_TEXT + note
    rows.sort(key=lambda row: (-row[0], row[2][0]))
    if line_limit is not None:
        # 消した行の表の行は一行なので、本文は line_limit が None のときだけ載せる。
        # 外形だけを渡す形は、行の本文を載せず、場所だけを示す
        table = markdown_table(
            ["変更した側", "他の場所の数", "他の場所の例"],
            [
                [_positions(here), str(count), _positions(others)]
                for count, _, here, others in rows
            ],
            ["code", "raw", "code"],
            cap=LINE_CAP,
        )
        return table + note
    table = markdown_table(
        ["行", "変更した側", "他の場所の数", "他の場所の例"],
        [
            [_body(line), _positions(here), str(count), _positions(others)]
            for count, line, here, others in rows
        ],
        ["codespan", "code", "raw", "code"],
        cap=LINE_CAP,
    )
    return table + note


def _extent(segment: list[str], other: list[str], sample: int, offset: int) -> tuple[int, int]:
    """種の行から前後へ、他の場所と一致が続く限り広げた範囲（開始, 終了）。"""
    first = sample
    while (
        first > 0 and first - 1 + offset >= 0 and other[first - 1 + offset] == segment[first - 1]
    ):
        first -= 1
    last = sample
    while (
        last + 1 < len(segment)
        and last + 1 + offset < len(other)
        and other[last + 1 + offset] == segment[last + 1]
    ):
        last += 1
    return first, last


def _overlaps_itself(
    numbers: list[int], other_numbers: list[int], first: int, last: int, offset: int
) -> bool:
    """同じファイルの中で、一致の範囲が相手の範囲と元の行番号で重なるか。"""
    return (
        other_numbers[first + offset] <= numbers[last]
        and numbers[first] <= other_numbers[last + offset]
    )


def _runs(
    path: str, numbers: list[int], segment: list[str], tree: _Tree
) -> list[tuple[int, int, Block]]:
    """連続の中で、他の場所と連続して一致する範囲。（連続内の開始, 終了, 他の場所）の列。

    連続も木も一致に使える行だけの列なので、添字は元の行番号ではない。
    他の場所は元の行番号で返す。
    """
    found: set[tuple[int, int, str, int]] = set()
    # 相手のパスと offset が同じ対角線上で、既に延長した範囲（開始, 終了）
    extended: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)
    # 塊は eligible な行を一行は含むので、種にするのはその行だけ。
    # 他の場所に SEED_CAP を超えて出現する行は慣用句とみなし、種にしない
    for sample, seed in enumerate(segment):
        places = tree.matchable.get(seed, [])
        if not _eligible(seed) or len(places) > SEED_CAP:
            continue
        for other_path, other_index in places:
            other = tree.lines[other_path]
            # 連続の j 番目の行に対する他の場所の添字は j + offset
            offset = other_index - sample
            if any(a <= sample <= b for a, b in extended[(other_path, offset)]):
                continue
            first, last = _extent(segment, other, sample, offset)
            extended[(other_path, offset)].append((first, last))
            if last - first + 1 < MIN_BLOCK_LINES:
                continue
            # 同じファイルで自分自身に重なる一致は、自分の写しではない
            if other_path == path and _overlaps_itself(
                numbers, tree.numbers[other_path], first, last, offset
            ):
                continue
            found.add((first, last, other_path, offset))
    results: list[tuple[int, int, Block]] = []
    for first, last, other_path, offset in found:
        if any(_eligible(line) for line in segment[first : last + 1]):
            other_numbers = tree.numbers[other_path]
            block = (other_path, other_numbers[first + offset], other_numbers[last + offset])
            results.append((first, last, block))
    return results


def _merged(runs: list[tuple[int, int, Block]]) -> list[tuple[int, int, set[Block]]]:
    """まとまりの中で重なる一致を一つの塊にまとめる。"""
    merged: list[tuple[int, int, set[Block]]] = []
    for first, last, block in sorted(runs):
        if merged and first <= merged[-1][1]:
            previous = merged[-1]
            merged[-1] = (previous[0], max(previous[1], last), previous[2] | {block})
        else:
            merged.append((first, last, {block}))
    return merged


def _repeat_blocks(
    added: dict[str, list[Segment]], tree: _Tree
) -> list[tuple[Block, int, list[Block], list[str]]]:
    """（変更した側, 行数, 同じ並びの場所, 正規化した行の列）の列。行数は一致に使える行の数。"""
    rows: list[tuple[Block, int, list[Block], list[str]]] = []
    for path in sorted(added):
        for numbers, segment in added[path]:
            for first, last, locations in _merged(_runs(path, numbers, segment, tree)):
                own: Block = (path, numbers[first], numbers[last])
                rows.append((own, last - first + 1, sorted(locations), segment[first : last + 1]))
    return rows


def _overlaps(first: Block, second: Block) -> bool:
    return first[0] == second[0] and first[1] <= second[2] and second[1] <= first[2]


def _repeats_table(
    added: dict[str, list[Segment]], tree: _Tree, line_limit: int | None = None
) -> str:
    candidates = _repeat_blocks(added, tree)
    if not candidates:
        return NO_REPEATS_TEXT
    candidates.sort(key=lambda row: (-row[1] * len(row[2]), row[0]))
    # 同じ塊を複数の場所に足していれば、互いが相手の場所になる。
    # 先に残した行の場所に重なる塊は、同じ塊として省く
    rows: list[tuple[Block, int, list[Block], list[str]]] = []
    claimed: list[Block] = []
    for row in candidates:
        if any(_overlaps(row[0], block) for block in claimed):
            continue
        rows.append(row)
        claimed.append(row[0])
        claimed.extend(row[2])
    head = ["変更した側", "行数", "同じ並びの場所の数", "同じ並びの場所の例"]
    kinds: list[CellKind] = ["code", "raw", "raw", "code"]
    cells = [
        [
            _span_text(own),
            str(length),
            str(len(locations)),
            _examples([_span_text(block) for block in locations]),
        ]
        for own, length, locations, _ in rows
    ]
    if line_limit is None:
        # 代表の行は、塊の中で最初の eligible な行。_runs は eligible な行を含む範囲だけを返す
        for cell_row, (_, _, _, texts) in zip(cells, rows, strict=True):
            cell_row.append(_body(next(line for line in texts if _eligible(line))))
        return markdown_table([*head, "代表の行"], cells, [*kinds, "codespan"], cap=LINE_CAP)
    if line_limit >= MIN_BLOCK_LINES:
        for cell_row, (_, length, _, texts) in zip(cells, rows, strict=True):
            # 添えない塊のセルは空にする。空の codespan は `` になるため、囲みは自分で付ける
            body = LINES_JOINER.join(_body(line) for line in texts)
            cell_row.append(f"`{code_cell(body)}`" if length <= line_limit else "")
        return markdown_table([*head, "行の本文"], cells, [*kinds, "raw"], cap=LINE_CAP)
    return markdown_table(head, cells, kinds, cap=LINE_CAP)


def repeats_text(review: Review, tree: PolicyTree, line_limit: int | None = None) -> str:
    """直した形が残る箇所と、同じ並びが他にもある塊。

    line_limit は、正規化した行の本文を添える塊の行数の上限。
    None は全部の塊に添え（代表の行も載せる）、MIN_BLOCK_LINES 未満は添えない。
    消した行の表の行は一行なので、本文は None のときだけ載せる。
    """
    index = _Tree(tree)
    tests = _test_paths(review, registry.adapters())
    sections: list[str] = []
    if review.is_diff:
        removed, added = _diff_lines(review, tests)
        # 足した行の位置は、消した行の「他の場所」に数えない。移しただけの行を残りと見誤るため
        all_added = {
            (path, number)
            for path, segments in added.items()
            for numbers, _ in segments
            for number in numbers
        }
        remains = _remains_table(removed, index, all_added, line_limit)
        sections.append(f"#### 直した形が残る箇所\n\n{REMAINS_INTRO}\n\n{remains}")
    else:
        added = _feature_added(review, tree, tests)
    repeats = _repeats_table(added, index, line_limit)
    sections.append(f"#### 同じ並びが他にもある塊\n\n{_intro(line_limit)}\n\n{repeats}")
    return "\n\n".join(sections)
