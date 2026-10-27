"""方針のレビューに渡す資料。判断の地図から決定的に作り、LLM も差分も使わない。"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from jigane.figures.models import (
    NO_FILES_TEXT,
    AddedSignature,
    ChangedSignature,
    DeletedSignature,
    DiffTreeFigure,
    FileState,
    FileSummary,
    MovedSignature,
    Panel,
    SequenceFigure,
    SignatureEntry,
    SignatureFigure,
    TreeFigure,
)
from jigane.git.tree_files import read_tree_bytes
from jigane.judgment_map.classification import FilePair, Segment
from jigane.judgment_map.kinds import Kind
from jigane.judgment_map.region_table import Region
from jigane.languages import registry
from jigane.languages.base import Declaration, Span, parse
from jigane.markdown.cells import markdown_table
from jigane.policy_materials.declarations import ScopedDeclaration, region_declarations
from jigane.policy_materials.moved_declarations import PlacedDeclaration, find_moved
from jigane.preconditions.required import required
from jigane.reviews.lifecycle import Review, ReviewError, load_pairs

_FIRST_PRINTABLE_CODE = 32  # 空白。これより小さい文字は制御文字
_DELETE_CODE = 127

NO_SIGNATURE_TEXT = "（シグネチャなし）"
NO_REGIONS_TEXT = "要判断に分類された箇所は無い。ツリー図と宣言から方針を判断すること。"
# 図の節点に添える判断の箇所の番号の上限。残りは件数にする
TAG_CAP = 3


@dataclass(frozen=True)
class FileChange:
    """変更したファイル一つの状態と、変更の行数の合計。"""

    state: FileState
    lines: int

    @property
    def exists_after(self) -> bool:
        """変更後の木に残るか。削除されたファイルだけが残らない。"""
        return self.state is not FileState.DELETED


@dataclass(frozen=True)
class MovedFileChange(FileChange):
    """別のパスへ移したファイル。path は移動先で、移動元だけをこの型が持つ。

    移動元は移したファイルにしか無いので、FileChange の任意の項目にせず型を分ける。
    """

    moved_from: str


@dataclass(frozen=True)
class MovedAwayFileChange(FileChange):
    """別のパスへ移したファイルを、変更前の木で見た形。path は移動元で、移動先を持つ。"""

    moved_to: str


class _Tally:
    """判断の地図の区間を一ファイル分たどって、状態を決めるまでの途中の数え。"""

    def __init__(self) -> None:
        self.always_added = True
        self.always_deleted = True
        self.lines = 0
        self.moved_from: str | None = None
        self.moved_to: str | None = None

    def settle(self, *, before: bool) -> FileChange:
        if self.moved_from is not None and not before:
            return MovedFileChange(FileState.MOVED, self.lines, self.moved_from)
        if self.moved_to is not None and before:
            return MovedAwayFileChange(FileState.MOVED, self.lines, self.moved_to)
        if self.always_added:
            state = FileState.ADDED
        elif self.always_deleted:
            state = FileState.DELETED
        else:
            state = FileState.CHANGED
        return FileChange(state, self.lines)


def _load_pairs(review: Review) -> list[FilePair] | None:
    """ファイルの対の記録。機能レビュー式と、記録の無い古いレビューは None。"""
    if not review.is_diff:
        return None
    try:
        return load_pairs(review.id)
    except ReviewError:
        return None


def _pair_changes(
    pairs: Sequence[FilePair], segments: Sequence[Segment], *, before: bool
) -> dict[str, FileChange]:
    """ファイルの対から状態を決め、行数は同じ対の区間から数える。区間の無い対は 0 行。"""
    result: dict[str, FileChange] = {}
    for pair in pairs:
        old, new = pair.old_path, pair.new_path
        if before and old is None:
            continue
        lines = 0
        for segment in segments:
            if (segment.old_path, segment.new_path) != (old, new):
                continue
            use_new = not before and segment.new_span
            lines += segment.new_line_count if use_new else segment.old_line_count
        if old is None:
            result[new or ""] = FileChange(FileState.ADDED, lines)
        elif new is None:
            result[old] = FileChange(FileState.DELETED, lines)
        elif old == new:
            result[old] = FileChange(FileState.CHANGED, lines)
        elif before:
            result[old] = MovedAwayFileChange(FileState.MOVED, lines, new)
        else:
            result[new] = MovedFileChange(FileState.MOVED, lines, old)
    return result


def file_changes(review: Review, *, before: bool = False) -> dict[str, FileChange]:
    """変更したファイルごとの、状態と変更の行数の合計。

    ファイルの対の記録があれば状態はそこから決め（中身を変えない移動も含む）、行数は区間から数える。
    対の記録が無いレビューは、区間の旧パスと新パスから一度だけ決める。
    before が真なら変更前の木のパスで数え、追加したファイルは含めない。移動したファイルは
    移動元のパスに置き、移動先は `MovedAwayFileChange` が持つ。
    """
    pairs = _load_pairs(review)
    if pairs is not None:
        return _pair_changes(pairs, review.map_kind.segments, before=before)
    tallies: dict[str, _Tally] = {}
    for segment in review.map_kind.segments:
        if before:
            if segment.old_path is None:
                continue
            tally = tallies.setdefault(segment.old_path, _Tally())
            # 変更前の木に在るファイルは、追加ではない
            tally.always_added = False
            tally.always_deleted = tally.always_deleted and segment.new_path is None
            if segment.new_path not in (None, segment.old_path):
                tally.moved_to = segment.new_path
            tally.lines += segment.old_line_count
            continue
        path = required(segment.new_path or segment.old_path)
        tally = tallies.setdefault(path, _Tally())
        tally.always_added = tally.always_added and segment.old_path is None
        tally.always_deleted = tally.always_deleted and segment.new_path is None
        # 新パスが無い区間は削除なので、移動元にならない
        if segment.old_path is not None and segment.new_path not in (None, segment.old_path):
            tally.moved_from = segment.old_path
        tally.lines += segment.new_line_count if segment.new_span else segment.old_line_count
    return {path: tally.settle(before=before) for path, tally in tallies.items()}


def _label(text: str) -> str:
    """利用者の文字列を、Mermaid の引用符つきラベルに入れられる形にする。

    `<` と `>` は Mermaid がラベルの HTML として解釈するので、実体参照にする。
    `#` は実体参照の始まりなので先に置き換える。
    """
    flat = "".join(
        " " if ord(c) < _FIRST_PRINTABLE_CODE or ord(c) == _DELETE_CODE else c for c in text
    )
    return (
        flat.replace("#", "#35;")
        .replace('"', "'")
        .replace("&", "#amp;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
        .replace("[", "#91;")
        .replace("]", "#93;")
    )


def region_tags(review: Review) -> dict[str, list[str]]:
    """変更したファイルごとの、判断の箇所の番号（J 番号）。箇所の並びの順。"""
    tags: dict[str, list[str]] = {}
    for region in review.map_kind.table.regions:
        tags.setdefault(region.path, []).append(required(region.id))
    return tags


def _tagged(label: str, ids: Sequence[str]) -> str:
    if not ids:
        return label
    text = " ".join(_label(i) for i in ids[:TAG_CAP])
    if len(ids) > TAG_CAP:
        text += f" ほか {len(ids) - TAG_CAP} 件"
    return f"{label}<br/>{text}"


@dataclass
class _TreeDirectory:
    directories: dict[str, "_TreeDirectory"]
    files: dict[str, str]


def _collapse(name: str, node: _TreeDirectory) -> tuple[str, _TreeDirectory]:
    """直下にファイルが無く、子のディレクトリが一つだけなら、子と一つの節点に縮める。"""
    while not node.files and len(node.directories) == 1:
        ((child_name, child),) = node.directories.items()
        name = f"{name}/{child_name}" if name else child_name
        node = child
    return name, node


def _directory_tree(files: Mapping[str, FileChange]) -> _TreeDirectory:
    """ファイルの path を、ディレクトリのツリーに並べ直す。"""
    root = _TreeDirectory({}, {})
    for path in files:
        *parents, name = path.split("/")
        node = root
        for part in parents:
            node = node.directories.setdefault(part, _TreeDirectory({}, {}))
        node.files[name] = path
    return root


def _changed_file_label(name: str, change: FileChange) -> str:
    """変更の印を持つファイルの札。状態、行数、移動の相手を添える。"""
    label = f"{_label(name)}<br/>{change.state.label}"
    # 区間の無い側（挿入の変更前、削除の変更後、移動）は 0 行になる。「0 行」は書かない
    if change.lines:
        label += f"・{change.lines} 行"
    if isinstance(change, MovedFileChange):
        label += f"<br/>移動元: {_label(change.moved_from)}"
    elif isinstance(change, MovedAwayFileChange):
        label += f"<br/>移動先: {_label(change.moved_to)}"
    return label


_TREE_CLASS_DEFINITIONS = [
    # 色は持たせない。Mermaid は classDef を形の要素の style に !important 付きで書き、
    # 画面のテーマから塗り替えられなくなる。状態は枠の形で見分ける
    "    classDef changed stroke-width:1px",
    "    classDef added stroke-width:2px",
    "    classDef deleted stroke-dasharray:4 3",
    "    classDef moved stroke-dasharray:1 3",
]


class _TreeWriter:
    """ディレクトリのツリーを、Mermaid の行に書き出す。節点の id は書いた順に振る。"""

    def __init__(self, files: Mapping[str, FileChange], tags: Mapping[str, Sequence[str]]) -> None:
        self.files = files
        self.tags = tags
        self.lines = ["flowchart LR"]
        self.counter = 0

    def next_id(self) -> str:
        self.counter += 1
        return f"n{self.counter - 1}"

    def file_node(self, name: str, path: str) -> str:
        """ファイルの節点の行を足して、節点の id を返す。"""
        change = self.files[path]
        if change.state is FileState.UNCHANGED:
            node = self.next_id()
            self.lines.append(f'    {node}["{_tagged(_label(name), self.tags.get(path, ()))}"]')
            return node
        label = _changed_file_label(name, change)
        node = self.next_id()
        tagged = _tagged(label, self.tags.get(path, ()))
        self.lines.append(f'    {node}["{tagged}"]:::{change.state.css_class}')
        return node

    def emit(self, name: str, node: _TreeDirectory) -> str:
        name, node = _collapse(name, node)
        own = self.next_id()
        self.lines.append(f'    {own}["{_label(name or ".")}/"]')
        for child_name in sorted(node.directories):
            child_id = self.emit(child_name, node.directories[child_name])
            self.lines.append(f"    {own} --> {child_id}")
        for file_name in sorted(node.files):
            self.lines.append(f"    {own} --> {self.file_node(file_name, node.files[file_name])}")
        return own


def tree_mermaid(
    files: Mapping[str, FileChange], tags: Mapping[str, Sequence[str]] | None = None
) -> str:
    """変更したファイルをディレクトリのツリーに描いた Mermaid の原文（囲みなし）。

    ファイルが無ければ空。木に置くのは渡されたファイルとその祖先のディレクトリだけ。
    移動したファイルは、変更後の木では移動先に置いて札に移動元を、変更前の木では移動元に置いて
    札に移動先を添える。ファイルどうしの辺は描かない。
    状態が変更なし（FileState.UNCHANGED）のファイルは変更の印を持たず、札はファイル名だけになる。
    """
    if not files:
        return ""
    writer = _TreeWriter(files, tags or {})
    writer.emit("", _directory_tree(files))
    if all(change.state is FileState.UNCHANGED for change in files.values()):
        # 印を持つ節点が無く、classDef の参照先が無い
        return "\n".join(writer.lines)
    return "\n".join([*writer.lines, *_TREE_CLASS_DEFINITIONS])


def file_summaries(files: dict[str, FileChange]) -> tuple[FileSummary, ...]:
    """変更したファイルを、資料の一覧に載せる形にする。"""
    return tuple(FileSummary(path, change.state, change.lines) for path, change in files.items())


def current_tree_figure(paths: Sequence[str]) -> TreeFigure:
    """差分を持たない現状の木。ファイルはすべて変更なしの状態で持つ。"""
    files = {path: FileChange(FileState.UNCHANGED, 0) for path in paths}
    return TreeFigure(Panel("木", tree_mermaid(files)), file_summaries(files))


def tree_figure(review: Review) -> TreeFigure:
    """ツリー図の図。画面とプロンプトが同じものを使う。

    差分レビュー式は変更前の木と変更後の木の二枚、機能レビュー式は変更後の木だけ。
    判断の箇所の札は、箇所の path が一致する木の節点に付く。
    """
    files = file_changes(review)
    tags = region_tags(review)
    # 削除したファイルは変更後の木に無い。ファイルの一覧には残す
    after_files = {path: change for path, change in files.items() if change.exists_after}
    after = Panel("変更後の木", tree_mermaid(after_files, tags))
    summaries = file_summaries(files)
    if not review.is_diff:
        return TreeFigure(after, summaries)
    before_files = file_changes(review, before=True)
    before = Panel("変更前の木", tree_mermaid(before_files, tags))
    return DiffTreeFigure(after, summaries, before)


def folded_tree_figure(review: Review) -> TreeFigure:
    """変更前後の二枚を、変更のあった枝だけの一つの木に畳んだツリー図。

    木に置くのは変更したファイルとその祖先だけなので、変更の無い枝は元から無い。
    削除したファイルも同じ木に置き、状態の札で変更前の木にだけあったことを示す。
    """
    files = file_changes(review)
    return TreeFigure(
        Panel("変更の木", tree_mermaid(files, region_tags(review))), file_summaries(files)
    )


def _signature_text(source_lines: list[str], span: Span) -> str:
    text = " ".join(line.strip() for line in source_lines[span.start_line - 1 : span.end_line])
    return text or NO_SIGNATURE_TEXT


def tests_last[T](items: Iterable[T], path_of: Callable[[T], str]) -> list[T]:
    """テストのファイルの要素を後ろに回す。それぞれの中の順は保つ。"""
    adapters = registry.adapters()
    listed = list(items)
    is_test = [registry.is_test_path(path_of(i), adapters) for i in listed]
    return [i for i, t in zip(listed, is_test, strict=True) if not t] + [
        i for i, t in zip(listed, is_test, strict=True) if t
    ]


def _declarations_of(root: Path | None, path: str) -> dict[str, Declaration]:
    """木の中のファイルの宣言を修飾名で引く。木やファイルが無い、解析できないときは空。"""
    adapter = registry.adapter_for(path)
    data = read_tree_bytes(root, path) if root is not None else None
    if adapter is None or data is None:
        return {}
    found = adapter.declarations(parse(adapter, data).root_node, data)
    return {d.qualified_name: d for d in found}


def _declared_signature(
    declarations: dict[str, Declaration], name: str, root: Path | None, path: str
) -> str | None:
    declaration = declarations.get(name)
    if declaration is None or root is None:
        return None
    data = read_tree_bytes(root, path) or b""
    lines = data.decode("utf-8", errors="replace").split("\n")
    return _signature_text(lines, declaration.signature)


def _carries_signature(region: Region) -> bool:
    """箇所が公開の宣言のシグネチャの変更前後を持ちうるか。

    ファイルが残ったまま公開の宣言が消えた箇所は、種類が公開インターフェースではなく
    振る舞いの削除になる。名前を持つので、変更前の木から消えた宣言のシグネチャを拾える。
    """
    if region.kind.value == Kind.PUBLIC_INTERFACE.value:
        return True
    return (
        region.kind.value == Kind.BEHAVIOR_REMOVAL.value
        and bool(region.qualified_name)
        and region.new_span is None
    )


def _is_deleted_file(region: Region) -> bool:
    """ファイルごと消えた区間か。宣言の単位に分かれず、名前が空で新しい側の範囲が無い。"""
    return (
        region.kind.value == Kind.BEHAVIOR_REMOVAL.value
        and not region.qualified_name
        and region.new_span is None
        and region.old_span is not None
    )


def _deleted_file_declarations(
    path: str, root: Path | None
) -> list[PlacedDeclaration[DeletedSignature]]:
    """ファイルごと消えたファイルの公開の宣言を、変更前の木から削除の候補にする。変数は除く。"""
    declarations = _declarations_of(root, path)
    placed: list[PlacedDeclaration[DeletedSignature]] = []
    for name, declaration in declarations.items():
        if not declaration.is_public or declaration.kind == "variable":
            continue
        old = required(_declared_signature(declarations, name, root, path))
        gone = DeletedSignature(name, f"{path}:{declaration.signature.start_line}", old)
        placed.append(PlacedDeclaration(name, declaration.kind, path, old, gone))
    return placed


@dataclass
class _SignatureCollector:
    """箇所を順に読んで集める、シグネチャの項目の置き場。"""

    # シグネチャが変わった宣言を先に並べる。新規と削除は後ろに並べる
    changed: list[SignatureEntry]
    added: list[PlacedDeclaration[AddedSignature]]
    deleted: list[PlacedDeclaration[DeletedSignature]]
    # ファイルごと消えた宣言。移し先と組になったものだけを載せ、残りは載せない
    # （ファイル単位の削除の箇所が判断の地図に出ており、宣言ごとに並べると資料が長くなる）
    file_gone: list[PlacedDeclaration[DeletedSignature]]
    seen: set[tuple[str, str]]


@dataclass(frozen=True)
class _FileSides:
    """ファイル一つの、変更後と変更前の木とその宣言。"""

    path: str
    after_root: Path
    before_root: Path | None
    after_decls: dict[str, Declaration]
    before_decls: dict[str, Declaration]


def _collect_declaration(
    collector: _SignatureCollector, scoped: ScopedDeclaration, sides: _FileSides
) -> None:
    """行の宣言一つを、新規、削除、変更のいずれかとして集める。変わらないものは集めない。"""
    path = sides.path
    old = _declared_signature(sides.before_decls, scoped.name, sides.before_root, path)
    new = _declared_signature(sides.after_decls, scoped.name, sides.after_root, path)
    declaration = sides.after_decls.get(scoped.name) or sides.before_decls.get(scoped.name)
    # シグネチャが同じなら載せない。どちらの木にも宣言が無いときは、載せる行の位置が無い
    if old == new or declaration is None:
        return
    if declaration.kind == "variable":
        # 型の項目、列挙の値、モジュールの定数。シグネチャを持つ宣言ではない
        return
    place = f"{path}:{declaration.signature.start_line}"
    if old is None:
        entry = AddedSignature(scoped.name, place, required(new))
        collector.added.append(
            PlacedDeclaration(scoped.name, declaration.kind, path, entry.new, entry)
        )
    elif new is None:
        gone = DeletedSignature(scoped.name, place, old)
        collector.deleted.append(PlacedDeclaration(scoped.name, declaration.kind, path, old, gone))
    else:
        collector.changed.append(ChangedSignature(scoped.name, place, old, new))


def _collect_region(
    collector: _SignatureCollector, region: Region, trees: tuple[Path, Path | None]
) -> None:
    """箇所一つを読んで、集める。"""
    after_root, before_root = trees
    if _is_deleted_file(region):
        if (region.path, "") not in collector.seen:
            collector.seen.add((region.path, ""))
            collector.file_gone += _deleted_file_declarations(region.path, before_root)
        return
    if not _carries_signature(region):
        return
    path = region.path
    after_data = read_tree_bytes(after_root, path)
    sides = _FileSides(
        path,
        after_root,
        before_root,
        _declarations_of(after_root, path),
        _declarations_of(before_root, path),
    )
    for scoped in region_declarations(region, after_data):
        if scoped.role != "row" or not scoped.name or (path, scoped.name) in collector.seen:
            continue
        collector.seen.add((path, scoped.name))
        _collect_declaration(collector, scoped, sides)


def _arranged_entries(collector: _SignatureCollector) -> tuple[SignatureEntry, ...]:
    """集めた項目を、移しただけのものを一項にして並べる。テストのファイルは後ろに回す。"""
    # 移しただけの宣言は、追加と削除の組ではなく一つの項にする
    moves = find_moved(collector.deleted + collector.file_gone, collector.added)
    moved_ids = {id(side.item) for pair in moves for side in (pair.old, pair.new)}
    introduced: list[SignatureEntry] = [
        MovedSignature(
            pair.new.item.name, pair.old.item.place, pair.new.item.place, pair.new.item.new
        )
        for pair in moves
    ]
    introduced += [p.item for p in collector.added if id(p.item) not in moved_ids]
    introduced += [p.item for p in collector.deleted if id(p.item) not in moved_ids]
    return tuple(tests_last(collector.changed + introduced, lambda e: e.place.rpartition(":")[0]))


def signature_changes(review: Review) -> SignatureFigure:
    """変更した公開の宣言のシグネチャを、変更前と変更後の組にする。本体は含まない。

    関数、メソッド、クラスだけを載せる。型の項目、列挙の値、モジュールの定数（変数の宣言）は載せない。
    シグネチャが変わらない宣言は載せない。変更の種類は型（新規、削除、変更、移した）で持つ。
    別のファイルへ移しただけの宣言（`find_moved`）は、新規と削除に分けず移したとして一項にする。
    テストのファイルの宣言は後ろに並べる。
    """
    trees = (Path(review.repository_root), review.base_tree_path)
    collector = _SignatureCollector([], [], [], [], set())
    for region in review.map_kind.table.regions:
        _collect_region(collector, region, trees)
    return SignatureFigure(_arranged_entries(collector))


def regions_text(review: Review) -> str:
    regions = review.map_kind.table.regions
    if not regions:
        return NO_REGIONS_TEXT
    table: list[list[str]] = []
    for region in regions:
        span = region.new_span or region.old_span
        position = f"{span.start_line}-{span.end_line}" if span else "なし"
        if region.new_span is None:
            position += "（変更前）"
        table.append(
            [
                required(region.id),
                region.key,
                region.kind.value,
                region.path,
                region.qualified_name,
                region.summary,
                position,
            ]
        )
    return markdown_table(
        ["箇所", "key", "種類", "パス", "名前", "要約", "行"],
        table,
        ["raw", "cell", "raw", "cell", "cell", "cell", "raw"],
    )


class PrebuiltSection(StrEnum):
    """一回の実行で一度だけ組む資料の節。値は節の見出し。

    資料の中の置き場でもある。プロンプトでの並びは `policy_figures.PROMPT_ORDER` が決める。
    """

    TREE = "変更のツリー図"
    SEQUENCE = "シーケンス図"
    REGIONS = "判断の箇所の一覧"


SECTION_SIGNATURES = "公開のシグネチャの変更前後"


def _titled(title: str, body: str) -> str:
    return f"### {title}\n\n{body}"


def tree_section_is_empty(section: str) -> bool:
    """事前に組んだツリー図の節が、変更したファイルが無くて描けなかった節か。"""
    return section == _titled(PrebuiltSection.TREE, NO_FILES_TEXT)


def materials_sections(
    review: Review, sequences: SequenceFigure | None = None, *, folded: bool = False
) -> dict[PrebuiltSection, str]:
    """事前に組む節を、見出しつきの本文で返す。

    シーケンス図は渡されたときだけ含める。インターフェースの変更、宣言、背景は含めない。
    folded が真なら、ツリー図は一つの木に畳み、札のあるシーケンス図は変更後の図だけにする。
    """
    tree = folded_tree_figure(review) if folded else tree_figure(review)
    sections = {PrebuiltSection.TREE: _titled(PrebuiltSection.TREE, tree.text())}
    if sequences is not None:
        shown = sequences.without_tagged_before() if folded else sequences
        sections[PrebuiltSection.SEQUENCE] = _titled(PrebuiltSection.SEQUENCE, shown.text())
    sections[PrebuiltSection.REGIONS] = _titled(PrebuiltSection.REGIONS, regions_text(review))
    return sections
