import difflib
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from fnmatch import fnmatchcase
from functools import cached_property
from pathlib import Path
from typing import Any, assert_never

from jigane.git.tree_files import read_tree_bytes
from jigane.judgment_map.configuration import Config
from jigane.judgment_map.kinds import LEVEL_OF_KIND, Kind, Level, StoredMode
from jigane.judgment_map.region_table import CodeRegion, LineCounts, RegionTable
from jigane.languages import registry
from jigane.languages.base import (
    Declaration,
    LanguageAdapter,
    Span,
    Visibility,
    leaves_and_comments,
    parse,
)
from jigane.preconditions.required import required


@dataclass(frozen=True)
class FilePair:
    # リポジトリのルートからの posix の相対パス。
    # 追加では old_path、削除では new_path が None
    old_path: str | None
    new_path: str | None


def is_whole_deletion(pair: FilePair) -> bool:
    """丸ごと削除したファイルか。実装の発見役の単位と検証役の変更の節が、同じ基準で外す。"""
    return pair.new_path is None


class ChangeKind(StrEnum):
    """触れた箇所が、変更前に無かったか、あったか、変更後に無くなったか。"""

    ADDED = "added"
    CHANGED = "changed"
    REMOVED = "removed"


@dataclass(frozen=True)
class Segment:
    old_path: str | None
    new_path: str | None
    old_span: Span | None
    new_span: Span | None
    # 宣言の外では空文字
    qualified_name: str
    kind: Kind
    level: Level
    # 範囲は飛び飛びの変更行を一つにまとめるため、行数は範囲の長さではなくこの欄で数える
    old_line_count: int
    new_line_count: int
    # Declaration.kind の値。宣言の外とアダプタの無いファイルでは None
    declaration_kind: str | None
    # 追加、変更、削除の区別。old_span と new_span は位置だけを表し、この区別は持たない。
    # 範囲が空になる変更（末尾への要素の追加で前の行が変わらない場合など）でも区別が付くよう、
    # 宣言の分類の地点で一度だけ決める
    change_kind: ChangeKind
    # 変更後の範囲が、ボットの指摘の後に触れた行と重なるときの、コメントの id
    bot_comment_ids: tuple[int, ...] = ()
    # 触れた宣言（群）のうち最も広い可視性。要約の「公開の」「内部公開の」を選ぶ。
    # 宣言の外とアダプタの無いファイルでは None
    visibility: Visibility | None = None
    # 変更後の範囲が、静的検査の指摘の行の範囲と交わるときの、指摘の識別子（S1 など）
    static_finding_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """map.json の segments の一行。from_dict と対になる保存の形で、ここ一か所が持つ。"""
        return {key: _plain(value) for key, value in asdict(self).items()}

    @staticmethod
    def from_dict(stored: Mapping[str, Any]) -> "Segment":
        visibility = stored["visibility"]
        return Segment(
            old_path=stored["old_path"],
            new_path=stored["new_path"],
            old_span=_span_from_stored(stored["old_span"]),
            new_span=_span_from_stored(stored["new_span"]),
            qualified_name=stored["qualified_name"],
            kind=Kind(stored["kind"]),
            level=Level(stored["level"]),
            old_line_count=stored["old_line_count"],
            new_line_count=stored["new_line_count"],
            declaration_kind=stored["declaration_kind"],
            change_kind=ChangeKind(stored["change_kind"]),
            bot_comment_ids=tuple(stored["bot_comment_ids"]),
            visibility=None if visibility is None else Visibility(visibility),
            static_finding_ids=tuple(stored["static_finding_ids"]),
        )


def _span_from_stored(stored: Mapping[str, Any] | None) -> Span | None:
    if stored is None:
        return None
    return Span(stored["start_line"], stored["end_line"])


@dataclass(frozen=True)
class BotTouch:
    """ボットのコメント一つについて、コメントの後に変更で触れた head の行。"""

    comment_id: int
    path: str
    lines: frozenset[int]


@dataclass(frozen=True)
class FindingTouch:
    """静的検査の指摘一つの、変更後のファイルでの行の範囲。

    raises は指摘の重さが区間を上げるか（error、warning）。note と none は識別子だけ付ける。
    """

    finding_id: str
    path: str
    start_line: int
    end_line: int
    raises: bool


@dataclass(frozen=True)
class _Leaf:
    row: int
    ancestors: tuple[str, ...]
    text: str
    is_comment: bool


class _Side:
    """ファイルの片側（変更前または変更後）の行、字句、宣言。"""

    def __init__(
        self, root: Path | None, path: str | None, adapter: LanguageAdapter | None
    ) -> None:
        self.has_error = False
        self.leaves: list[_Leaf] = []
        self.declarations: list[Declaration] = []
        self.lines: list[str] = []
        if root is None or path is None:
            return
        data = read_tree_bytes(root, path)
        if data is None:
            # 木の外へ出るリンクなどは、中身を読まず空の側として扱う
            return
        self.lines = data.decode("utf-8", errors="replace").split("\n")
        if self.lines[-1] == "":
            self.lines.pop()
        if adapter is None:
            return
        tree = parse(adapter, data)
        self.has_error = tree.root_node.has_error
        self.declarations = adapter.declarations(tree.root_node, data)
        for node, ancestors in leaves_and_comments(adapter, tree.root_node):
            self.leaves.append(
                _Leaf(
                    node.start_point.row + 1,
                    ancestors,
                    required(node.text).decode(errors="replace"),
                    node.type in adapter.comment_node_types,
                )
            )

    def declarations_named(self, name: str) -> list[Declaration]:
        return [d for d in self.declarations if d.qualified_name == name]

    @cached_property
    def _innermost_by_row(self) -> dict[int, Declaration]:
        # 行ごとに宣言を探すと、行数と宣言の数の積に比例し、大きなファイルで遅い。
        # 長い宣言から順に行を埋め、短い宣言で上書きする。長さが同じなら並びの先の宣言を残す
        by_row: dict[int, Declaration] = {}
        ordered = sorted(
            enumerate(self.declarations),
            key=lambda item: (item[1].span.start_line - item[1].span.end_line, -item[0]),
        )
        for _, declaration in ordered:
            for row in range(declaration.span.start_line, declaration.span.end_line + 1):
                by_row[row] = declaration
        return by_row

    def innermost(self, row: int) -> Declaration | None:
        return self._innermost_by_row.get(row)

    def touched(self, rows: set[int]) -> list[Declaration]:
        """変更した行を含む宣言（innermost が返すもの）を、重複なく返す。"""
        found: dict[Span, Declaration] = {}
        for row in sorted(rows):
            declaration = self.innermost(row)
            if declaration is not None:
                found.setdefault(declaration.span, declaration)
        return list(found.values())

    def span_group(self, declarations: list[Declaration]) -> list[Declaration]:
        # 同じ範囲を持つ宣言は一つの単位。`int a, b;` のように修飾子と型を共有する
        spans = {d.span for d in declarations}
        return [d for d in self.declarations if d.span in spans]

    def owned_rows(self, name: str) -> set[int]:
        # 入れ子の宣言の行は除く。内側の変更で外側が変わったとみなさないため。
        # 範囲が同じ宣言は入れ子ではなく同じ単位の別名なので、除かない
        named = self.declarations_named(name)
        own_spans = {d.span for d in named}
        rows: set[int] = set()
        for declaration in named:
            rows.update(_rows(declaration.span))
        for other in self.declarations:
            if (
                other.qualified_name != name
                and other.span not in own_spans
                and _rows(other.span) <= rows
            ):
                rows -= _rows(other.span)
        return rows

    def tokens(self, rows: set[int], *, with_comments: bool) -> list[tuple[tuple[str, ...], str]]:
        return [
            (leaf.ancestors, leaf.text)
            for leaf in self.leaves
            if leaf.row in rows and (with_comments or not leaf.is_comment)
        ]


def _widest(declarations: Sequence[Declaration]) -> Visibility | None:
    widest: Visibility | None = None
    for declaration in declarations:
        widest = declaration.visibility if widest is None else widest.wider(declaration.visibility)
    return widest


def _rows(span: Span) -> set[int]:
    return set(range(span.start_line, span.end_line + 1))


def _span(rows: set[int]) -> Span | None:
    return Span(min(rows), max(rows)) if rows else None


def _compare(old: _Side, new: _Side, old_rows: set[int], new_rows: set[int]) -> Kind | None:
    """字句列を比べる。ロジックの違いがあるときは None を返す。"""
    # 字句に祖先の節点の種類の列を添えるのは、
    # 字下げで構文上の位置が変わる変更を整形のみと見誤らないため
    if old.tokens(old_rows, with_comments=False) != new.tokens(new_rows, with_comments=False):
        return None
    if old.tokens(old_rows, with_comments=True) == new.tokens(new_rows, with_comments=True):
        return Kind.FORMAT_ONLY
    return Kind.COMMENT_ONLY


def matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


@dataclass(frozen=True)
class _ChangedLines:
    old_span: Span | None
    new_span: Span | None
    old_line_count: int
    new_line_count: int


def _segment(
    pair: FilePair,
    kind: Kind,
    lines: _ChangedLines,
    *,
    qualified_name: str = "",
    declaration_kind: str | None = None,
    visibility: Visibility | None = None,
    change_kind: ChangeKind | None = None,
) -> Segment:
    if change_kind is None:
        # 宣言の分類を経ない箇所（宣言の外、ファイル全体の追加と削除）は、
        # 範囲の有無が区別の全てになる
        if lines.old_span is None:
            change_kind = ChangeKind.ADDED
        elif lines.new_span is None:
            change_kind = ChangeKind.REMOVED
        else:
            change_kind = ChangeKind.CHANGED
    return Segment(
        pair.old_path,
        pair.new_path,
        lines.old_span,
        lines.new_span,
        qualified_name,
        kind,
        LEVEL_OF_KIND[kind],
        lines.old_line_count,
        lines.new_line_count,
        declaration_kind,
        change_kind,
        visibility=visibility,
    )


def _opcodes(old_lines: list[str], new_lines: list[str]) -> list[tuple[int, int, int, int]]:
    # git に頼らず、Windows でも機能レビュー式でも同じ計算にするため difflib を使う
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    return [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _opcode_segments(
    pair: FilePair, opcodes: list[tuple[int, int, int, int]], kind: Kind
) -> list[Segment]:
    return [
        _segment(
            pair,
            kind,
            _ChangedLines(
                Span(i1 + 1, i2) if i2 > i1 else None,
                Span(j1 + 1, j2) if j2 > j1 else None,
                old_line_count=i2 - i1,
                new_line_count=j2 - j1,
            ),
        )
        for i1, i2, j1, j2 in opcodes
    ]


def _signature_text(side: _Side, declaration: Declaration) -> str:
    signature = declaration.signature
    return " ".join(" ".join(side.lines[signature.start_line - 1 : signature.end_line]).split())


def _declaration_change_kind(
    old: _Side,
    new: _Side,
    old_declarations: list[Declaration],
    new_declarations: list[Declaration],
    new_group: list[Declaration],
) -> ChangeKind:
    if not old_declarations:
        return ChangeKind.ADDED
    # 多重定義は修飾名を共有するため、修飾名だけでは新しい多重定義が既存の宣言の変更に見える。
    # 宣言数が増え、かつ変更前に無い署名の宣言に触れているときだけ追加とみなす。
    # 宣言数が増えていなければ、署名の食い違いは既存の宣言の署名の変更である。
    # 照合に使う署名は宣言の先頭から本体の直前までの原文で、型の同値判定はしない
    if len(new_declarations) <= len(old_declarations):
        return ChangeKind.CHANGED
    old_signatures = {_signature_text(old, d) for d in old_declarations}
    unmatched = [d for d in new_group if _signature_text(new, d) not in old_signatures]
    return ChangeKind.ADDED if unmatched else ChangeKind.CHANGED


def _collect_units(
    old: _Side, new: _Side, opcodes: list[tuple[int, int, int, int]]
) -> dict[tuple[str, int], tuple[set[int], set[int]]]:
    # 単位ごとに変更した行を (変更前, 変更後) の組で集める。
    # キーは宣言の修飾名、宣言の外は差分の範囲ごと
    units: dict[tuple[str, int], tuple[set[int], set[int]]] = {}
    for index, (i1, i2, j1, j2) in enumerate(opcodes):
        for side_index, side, rows in (
            (0, old, range(i1 + 1, i2 + 1)),
            (1, new, range(j1 + 1, j2 + 1)),
        ):
            for row in rows:
                declaration = side.innermost(row)
                key = (declaration.qualified_name, -1) if declaration is not None else ("", index)
                units.setdefault(key, (set(), set()))[side_index].add(row)
    return units


def _undeclared_segment(
    pair: FilePair,
    old: _Side,
    new: _Side,
    *,
    old_changed: set[int],
    new_changed: set[int],
    logic_kind: Kind,
) -> Segment:
    kind = _compare(old, new, old_changed, new_changed) or logic_kind
    return _segment(
        pair,
        kind,
        _ChangedLines(
            _span(old_changed),
            _span(new_changed),
            old_line_count=len(old_changed),
            new_line_count=len(new_changed),
        ),
    )


def _removed_declaration_segment(
    pair: FilePair,
    old: _Side,
    name: str,
    *,
    old_changed: set[int],
    new_changed: set[int],
    old_declarations: list[Declaration],
    logic_kind: Kind,
) -> Segment:
    declaration_kind = old_declarations[0].kind
    removed_visibility = _widest(old.span_group(old.touched(old_changed)))
    is_public = removed_visibility is not None and removed_visibility is not Visibility.FILE
    kind = Kind.BEHAVIOR_REMOVAL if is_public else logic_kind
    return _segment(
        pair,
        kind,
        _ChangedLines(
            _span(old_changed),
            _span(new_changed),
            old_line_count=len(old_changed),
            new_line_count=len(new_changed),
        ),
        qualified_name=name,
        declaration_kind=declaration_kind,
        visibility=removed_visibility,
        change_kind=ChangeKind.REMOVED,
    )


def _declaration_segments(
    pair: FilePair,
    old: _Side,
    new: _Side,
    declarations: tuple[list[Declaration], list[Declaration]],
    *,
    old_changed: set[int],
    new_changed: set[int],
    logic_kind: Kind,
) -> list[Segment]:
    segments: list[Segment] = []
    old_declarations, new_declarations = declarations
    name = (new_declarations or old_declarations)[0].qualified_name
    declaration_kind = (new_declarations or old_declarations)[0].kind
    # 公開とシグネチャは、変更した行を含む宣言だけから判定する。
    # 多重定義は修飾名を共有するが、変更の無い多重定義の公開は関係ない。
    # 片側に変更した行が無いときは、その側の同名の宣言全体をシグネチャとみなす
    old_group = old.span_group(old.touched(old_changed)) or old_declarations
    new_group = new.span_group(new.touched(new_changed)) or new_declarations
    widest = _widest(new_group if new_changed else old_group)
    is_public = widest is not None and widest is not Visibility.FILE
    change_kind = _declaration_change_kind(old, new, old_declarations, new_declarations, new_group)
    old_owned = old.owned_rows(name)
    new_owned = new.owned_rows(name)
    old_signature = {r for d in old_group for r in _rows(d.signature)}
    new_signature = {r for d in new_group for r in _rows(d.signature)}
    for is_signature in (True, False):
        old_part = old_owned & old_signature if is_signature else old_owned - old_signature
        new_part = new_owned & new_signature if is_signature else new_owned - new_signature
        old_part_changed = old_changed & old_part
        new_part_changed = new_changed & new_part
        if not old_part_changed and not new_part_changed:
            continue
        compared = _compare(old, new, old_part, new_part)
        if compared is not None:
            kind = compared
        elif is_signature and is_public:
            kind = (
                Kind.PUBLIC_INTERFACE if widest is Visibility.PUBLIC else Kind.INTERNAL_INTERFACE
            )
        else:
            kind = logic_kind
        segments.append(
            _segment(
                pair,
                kind,
                _ChangedLines(
                    _span(old_part_changed),
                    _span(new_part_changed),
                    old_line_count=len(old_part_changed),
                    new_line_count=len(new_part_changed),
                ),
                qualified_name=name,
                declaration_kind=declaration_kind,
                visibility=widest,
                change_kind=change_kind,
            )
        )
    return segments


def _classify_code(
    pair: FilePair,
    old: _Side,
    new: _Side,
    opcodes: list[tuple[int, int, int, int]],
    logic_kind: Kind,
    *,
    has_old_tree: bool,
) -> list[Segment]:
    if old.has_error or new.has_error:
        # 構文を信用できないため、保守的にロジックとして扱う
        return _opcode_segments(pair, opcodes, logic_kind)

    segments: list[Segment] = []
    for (name, _), (old_changed, new_changed) in _collect_units(old, new, opcodes).items():
        if name == "":
            segments.append(
                _undeclared_segment(
                    pair,
                    old,
                    new,
                    old_changed=old_changed,
                    new_changed=new_changed,
                    logic_kind=logic_kind,
                )
            )
            continue
        old_declarations = old.declarations_named(name)
        new_declarations = new.declarations_named(name)
        if not new_declarations and has_old_tree:
            segments.append(
                _removed_declaration_segment(
                    pair,
                    old,
                    name,
                    old_changed=old_changed,
                    new_changed=new_changed,
                    old_declarations=old_declarations,
                    logic_kind=logic_kind,
                )
            )
            continue
        segments.extend(
            _declaration_segments(
                pair,
                old,
                new,
                (old_declarations, new_declarations),
                old_changed=old_changed,
                new_changed=new_changed,
                logic_kind=logic_kind,
            )
        )
    return segments


def _removed_file_segments(
    old_root: Path | None, old_for_path: _Side, pair: FilePair
) -> list[Segment]:
    if old_root is None:
        # 変更前のツリーが無ければ、何が消えたかを言えない
        return []
    length = len(old_for_path.lines)
    return [
        _segment(
            pair,
            Kind.BEHAVIOR_REMOVAL,
            _ChangedLines(
                Span(1, length) if length else None,
                None,
                old_line_count=length,
                new_line_count=0,
            ),
        )
    ]


def _classify_file(
    old_root: Path | None, new_root: Path, pair: FilePair, config: Config
) -> list[Segment]:
    path = pair.new_path or pair.old_path
    path = required(path)
    old_path = pair.old_path if old_root is not None else None
    old_for_path = _Side(old_root, old_path, None)
    new_for_path = _Side(new_root, pair.new_path, None)
    opcodes = _opcodes(old_for_path.lines, new_for_path.lines)

    if matches_any(path, config.exclude):
        return _opcode_segments(pair, opcodes, Kind.GENERATED)
    if pair.new_path is None:
        return _removed_file_segments(old_root, old_for_path, pair)
    registered = registry.adapters()
    if registry.is_build_path(path, registered):
        return _opcode_segments(pair, opcodes, Kind.DEPENDENCY_BUILD)
    logic_kind = Kind.SENSITIVE_LOGIC if matches_any(path, config.sensitive) else Kind.LOGIC
    adapter = registry.adapter_for(path)
    if adapter is None:
        return _opcode_segments(pair, opcodes, logic_kind)
    if registry.is_test_path(path, registered):
        # コメントのみ・整形のみの範囲でも重い方の test を採る。
        # そのため比較せず全範囲を test とする
        return _opcode_segments(pair, opcodes, Kind.TEST)
    old = _Side(old_root, old_path, adapter)
    new = _Side(new_root, pair.new_path, adapter)
    return _classify_code(pair, old, new, opcodes, logic_kind, has_old_tree=old_root is not None)


def classify(
    old_root: Path | None,
    new_root: Path,
    pairs: Sequence[FilePair],
    config: Config,
) -> list[Segment]:
    segments: list[Segment] = []
    for pair in pairs:
        found = _classify_file(old_root, new_root, pair, config)
        found.sort(key=lambda s: (s.new_span or s.old_span or Span(0, 0)).start_line)
        segments.extend(found)
    return segments


@dataclass
class JudgmentMap:
    mode: StoredMode
    segments: list[Segment]
    regions: list[CodeRegion] = field(default_factory=list)
    # 変更後のパスから変更前のパスへ。中身を変えずに移したファイルは区間を持たないので、
    # 区間からは移した元を引けない
    renames: dict[str, str] = field(default_factory=dict)

    def line_counts(self) -> LineCounts:
        by_kind = {kind.value: 0 for kind in Kind}
        by_level = {level.value: 0 for level in Level}
        for segment in self.segments:
            # 変更後の無い削除だけが、変更前の行数で数えられる
            count = segment.new_line_count if segment.new_span else segment.old_line_count
            by_kind[segment.kind.value] += count
            by_level[segment.level.value] += count
        return LineCounts(by_kind, by_level)

    def region_table(self) -> RegionTable:
        return RegionTable(self.regions, self.line_counts())

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "segments": [segment.to_dict() for segment in self.segments],
            **self.region_table().to_dict(),
        }


def file_line_counts(segments: Sequence[Segment]) -> dict[str, dict[str, int]]:
    """segment から、ファイルごとの段別の行数を数える。

    数え方は JudgmentMap.line_counts の by_level と同じで、全ファイルを足せば by_level に一致する。
    """
    by_file: dict[str, dict[str, int]] = {}
    for segment in segments:
        # 削除されたファイルは変更後のパスが無いので、変更前のパスで数える
        path = required(segment.new_path or segment.old_path)
        count = segment.new_line_count if segment.new_span else segment.old_line_count
        levels = by_file.setdefault(path, {level.value: 0 for level in Level})
        levels[segment.level.value] += count
    return by_file


def _plain(value: object) -> object:
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    # asdict は StrEnum をそのまま残すため、JSON にできる形へ直す
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


_KIND_LABEL = {
    "class": "型",
    "function": "関数",
    "method": "メソッド",
    "variable": "変数",
}

_MAX_LISTED_NAMES = 5


def _last_name(qualified_name: str) -> str:
    return qualified_name.rsplit(".", 1)[-1]


def _cover(spans: list[Span]) -> Span | None:
    return (
        Span(min(s.start_line for s in spans), max(s.end_line for s in spans)) if spans else None
    )


def _summarize(members: list[Segment], kind: Kind, *, is_feature: bool) -> str:
    summary = _summarize_kind(members, kind, is_feature=is_feature)
    count = len({i for m in members for i in m.bot_comment_ids})
    if count and kind != Kind.BOT_RESPONSE:
        # 種類を変えない要判断の箇所にも、指摘の後に触れた変更を含むことを残す
        summary += f"。ボットの指摘 {count} 件の後の変更を含む"
    finding_count = len({i for m in members for i in m.static_finding_ids})
    if finding_count and kind != Kind.STATIC_FINDING:
        summary += f"。静的検査の指摘 {finding_count} 件と重なる"
    return summary


def _merged_change_kind(members: list[Segment]) -> ChangeKind:
    # 同じ箇所の部分（シグネチャと本体）の一つでも既存を変えていれば、箇所の全体は変更とする
    found = {m.change_kind for m in members}
    for candidate in (ChangeKind.CHANGED, ChangeKind.REMOVED):
        if candidate in found:
            return candidate
    return ChangeKind.ADDED


def _scope_label(members: list[Segment]) -> str:
    # 同じ箇所に可視性の違う宣言が混ざるときは、最も広い方で書く
    return "公開" if any(m.visibility is Visibility.PUBLIC for m in members) else "内部公開"


def _summarize_feature_interface(members: list[Segment]) -> str:
    # メンバーまで並べると読めないため、モジュールの直下の名前だけを並べる
    names = list(dict.fromkeys(m.qualified_name.split(".")[0] for m in members))
    listed = "、".join(f"`{n}`" for n in names[:_MAX_LISTED_NAMES])
    if len(names) > _MAX_LISTED_NAMES:
        listed += f"、ほか {len(names) - _MAX_LISTED_NAMES} 件"
    return f"{_scope_label(members)}: {listed}"


def _summarize_interface(members: list[Segment], *, is_feature: bool) -> str:
    if is_feature:
        return _summarize_feature_interface(members)
    first = members[0]
    name = _last_name(first.qualified_name)
    label = _KIND_LABEL.get(first.declaration_kind or "", "")
    scope = _scope_label(members)
    change_kind = _merged_change_kind(members)
    match change_kind:
        case ChangeKind.ADDED:
            return f"{scope}の{label} `{name}` を追加"
        case ChangeKind.CHANGED:
            if first.declaration_kind == "variable":
                # 変数のシグネチャは宣言の全体なので、要素の追加などを「シグネチャ」と呼ばない
                return f"{scope}の{label} `{name}` を変更"
            return f"{scope}の{label} `{name}` のシグネチャを変更"
        case ChangeKind.REMOVED:
            return f"{scope}の{label} `{name}` を削除"
        case _:
            assert_never(change_kind)


def _summarize_dependency_build(first: Segment) -> str:
    path = first.new_path or first.old_path or ""
    file_name = path.rsplit("/", 1)[-1]
    verb = "変更" if first.old_path else "追加"
    return f"`{file_name}` を{verb}"


def _summarize_behavior_removal(members: list[Segment]) -> str:
    first = members[0]
    if first.new_path is None:
        return f"ファイル `{first.old_path}` を削除"
    label = _KIND_LABEL.get(first.declaration_kind or "", "")
    name = _last_name(first.qualified_name)
    return f"{_scope_label(members)}の{label} `{name}` を削除"


def _summarize_bot_response(members: list[Segment], *, is_feature: bool) -> str:
    first = members[0]
    count = len({i for m in members for i in m.bot_comment_ids})
    summary = f"ボットの指摘 {count} 件の後の変更"
    if first.qualified_name and not is_feature:
        summary += f"（`{_last_name(first.qualified_name)}`）"
    return summary


def _summarize_static_finding(members: list[Segment], *, is_feature: bool) -> str:
    # 指摘の文は方針の群へ渡る一覧に載せない。件数だけを書く
    first = members[0]
    count = len({i for m in members for i in m.static_finding_ids})
    summary = f"静的検査の指摘 {count} 件"
    if first.qualified_name and not is_feature:
        summary += f"（`{_last_name(first.qualified_name)}`）"
    return summary


def _summarize_sensitive_logic(first: Segment, *, is_feature: bool) -> str:
    if first.qualified_name and not is_feature:
        return f"センシティブなパスのロジックを変更（`{_last_name(first.qualified_name)}`）"
    return "センシティブなパスのロジックを変更"


def _summarize_kind(members: list[Segment], kind: Kind, *, is_feature: bool) -> str:
    if kind in (Kind.PUBLIC_INTERFACE, Kind.INTERNAL_INTERFACE):
        return _summarize_interface(members, is_feature=is_feature)
    if kind == Kind.DEPENDENCY_BUILD:
        return _summarize_dependency_build(members[0])
    if kind == Kind.BEHAVIOR_REMOVAL:
        return _summarize_behavior_removal(members)
    if kind == Kind.BOT_RESPONSE:
        return _summarize_bot_response(members, is_feature=is_feature)
    if kind == Kind.STATIC_FINDING:
        return _summarize_static_finding(members, is_feature=is_feature)
    # 残る種類は sensitive_logic だけ
    return _summarize_sensitive_logic(members[0], is_feature=is_feature)


def _body_hash(root: Path | None, path: str | None, span: Span | None) -> str:
    lines = _Side(root, path, None).lines if span else []
    text = "\n".join(lines[span.start_line - 1 : span.end_line]) if span else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_regions(
    segments: Sequence[Segment], old_root: Path | None, new_root: Path
) -> list[CodeRegion]:
    is_feature = old_root is None
    groups: dict[tuple[str, str, Kind], list[Segment]] = {}
    for segment in segments:
        if segment.level != Level.REQUIRED:
            continue
        path = segment.new_path or segment.old_path
        path = required(path)
        # 機能レビュー式は全行が追加で、宣言ごとだと公開の宣言の数だけ箇所が並ぶ
        name = "" if is_feature else segment.qualified_name
        groups.setdefault((path, name, segment.kind), []).append(segment)
    regions: list[CodeRegion] = []
    for (path, name, kind), members in groups.items():
        old_span = _cover([m.old_span for m in members if m.old_span])
        new_span = _cover([m.new_span for m in members if m.new_span])
        if new_span:
            content_hash = _body_hash(new_root, members[0].new_path, new_span)
        else:
            content_hash = _body_hash(old_root, members[0].old_path, old_span)
        before = next((m.old_path for m in members if m.old_path), None)
        regions.append(
            CodeRegion(
                key=f"{path}::{name}::{kind.value}",
                kind=kind,
                path=path,
                qualified_name=name,
                summary=_summarize(members, kind, is_feature=is_feature),
                old_span=old_span,
                new_span=new_span,
                line_count=sum(
                    m.new_line_count if m.new_span else m.old_line_count for m in members
                ),
                content_hash=content_hash,
                bot_comment_ids=tuple(sorted({i for m in members for i in m.bot_comment_ids})),
                old_path=before if before != path else None,
            )
        )
    return regions


def apply_bot_touches(segments: Sequence[Segment], touches: Sequence[BotTouch]) -> list[Segment]:
    """ボットの指摘の後に触れた行と重なる segment に、コメントの id を持たせる。

    要判断でない segment は bot_response へ上げ、既に要判断のものは種類を変えない。
    行数は segment 単位で数えるため、種類を変えても二重には数えない。
    """
    result: list[Segment] = []
    for segment in segments:
        span = segment.new_span
        ids = (
            tuple(
                sorted(
                    touch.comment_id
                    for touch in touches
                    if touch.path == segment.new_path
                    and any(span.start_line <= n <= span.end_line for n in touch.lines)
                )
            )
            if span is not None
            else ()
        )
        if not ids:
            result.append(segment)
            continue
        base = segment
        if segment.level != Level.REQUIRED:
            base = replace(
                segment,
                kind=Kind.BOT_RESPONSE,
                level=LEVEL_OF_KIND[Kind.BOT_RESPONSE],
            )
        result.append(replace(base, bot_comment_ids=ids))
    return result


@dataclass(frozen=True)
class StaticFindingOverlay:
    """指摘を重ねた後の segment と、どの segment とも交わらなかった指摘（差分の外）の数。"""

    segments: tuple[Segment, ...]
    outside_count: int


def apply_static_findings(
    segments: Sequence[Segment], findings: Sequence[FindingTouch]
) -> StaticFindingOverlay:
    """変更後の行の範囲が指摘の範囲と交わる segment に、指摘の識別子を持たせる。

    上げる指摘（raises）が一つでもあり、要判断でない segment は static_finding へ上げる。
    既に要判断のものは種類を変えない。行数は segment 単位で数えるため、二重には数えない。
    """
    hit: set[str] = set()
    result: list[Segment] = []
    for segment in segments:
        span = segment.new_span
        touching = (
            [
                finding
                for finding in findings
                if finding.path == segment.new_path
                and finding.start_line <= span.end_line
                and span.start_line <= finding.end_line
            ]
            if span is not None
            else []
        )
        if not touching:
            result.append(segment)
            continue
        hit.update(finding.finding_id for finding in touching)
        base = segment
        if segment.level != Level.REQUIRED and any(f.raises for f in touching):
            base = replace(
                segment,
                kind=Kind.STATIC_FINDING,
                level=LEVEL_OF_KIND[Kind.STATIC_FINDING],
            )
        ids = {*segment.static_finding_ids, *(f.finding_id for f in touching)}
        result.append(replace(base, static_finding_ids=tuple(sorted(ids, key=_finding_number))))
    outside = len({finding.finding_id for finding in findings} - hit)
    return StaticFindingOverlay(tuple(result), outside)


def _finding_number(finding_id: str) -> tuple[int, str]:
    # 識別子は S と連番。文字列の順では S10 が S2 より前に来るので、番号で並べる
    digits = finding_id.lstrip("S")
    return (int(digits), finding_id) if digits.isdigit() else (-1, finding_id)


def build_judgment_map(
    old_root: Path | None,
    new_root: Path,
    pairs: Sequence[FilePair],
    config: Config,
    bot_touches: Sequence[BotTouch] = (),
    finding_touches: Sequence[FindingTouch] = (),
) -> JudgmentMap:
    botted = apply_bot_touches(classify(old_root, new_root, pairs, config), bot_touches)
    # 差分の外の件数は、保存した指摘の識別子のうち区間に付かなかったものから数え直せる
    segments = list(apply_static_findings(botted, finding_touches).segments)
    return JudgmentMap(
        mode=StoredMode.FEATURE if old_root is None else StoredMode.DIFF,
        segments=segments,
        regions=build_regions(segments, old_root, new_root),
        renames={
            pair.new_path: pair.old_path
            for pair in pairs
            if pair.new_path and pair.old_path and pair.new_path != pair.old_path
        },
    )
