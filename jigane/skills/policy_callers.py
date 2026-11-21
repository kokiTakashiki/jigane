"""方針のレビューに渡す、足した公開の口と呼ぶ側の表。

公開の名前の読みは言語のアダプタの口（`AddedPublicNameFinder`）に任せ、口を持たない言語のファイルは読まない。

変更で足した、または引数を足した公開の名前を一行ずつ示し、変更の後の木の中の呼ぶ側を名前で照合して数える。
名前と数だけを渡し、コードの文は渡さない。変更前の木を持たないファイルのレビューは、足したものが定まらないので描かない。
"""

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.git.tree_files import read_tree_text, walk_tree_files
from jigane.languages import registry
from jigane.languages.base import AddedPublicName, AddedPublicNameFinder, PublicNameKind
from jigane.policy_materials import changed_files
from jigane.policy_materials.compact import ChangeNodes
from jigane.reviews.lifecycle import Review

SECTION_CALLERS = "足した公開の口と呼ぶ側"
INTRO = (
    "変更の後の木（after）と前の木（before）を比べ、変更で足した、または引数を足した公開の名前"
    "（先頭が `_` でない関数、メソッド、クラス、列挙の値、引数）を一行ずつ示す。"
    "呼ぶ側は after の木の中で名前だけを照合して数え、型は解決しないので、"
    "同じ名前の別の定義を使う箇所も数える。"
    "クラスは、生成と継承に加えて、型注記と式の中で名前を書いた箇所を数える。"
    "省略形の列挙の値（Swift の `.a`）と、文字列で書いた型注記（Python）は数えない。"
    "Python は、関数を値として渡す参照（`key=fn`）と、"
    "メソッドの属性の読み出し（`@property` の読み出しなど）も数える。"
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


def _row(name: AddedPublicName) -> TableRow:
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


def _by_reader(paths: Iterable[str]) -> dict[str, tuple[AddedPublicNameFinder, list[str]]]:
    groups: dict[str, tuple[AddedPublicNameFinder, list[str]]] = {}
    for path in paths:
        adapter = registry.adapter_for(path)
        if isinstance(adapter, AddedPublicNameFinder):
            groups.setdefault(adapter.name, (adapter, []))[1].append(path)
    return groups


def public_names(review: Review) -> list[AddedPublicName]:
    if not review.is_diff or review.base_tree_path is None:
        return []
    root = Path(review.repository_root)
    changed_by_reader = _by_reader(
        path for path, entry in changed_files.file_changes(review).items() if entry.exists_after
    )
    before_by_reader = _by_reader(changed_files.file_changes(review, before=True))
    tree_by_reader = _by_reader(walk_tree_files(root, root))
    names: list[AddedPublicName] = []
    for key, (reader, changed) in changed_by_reader.items():
        before_paths = before_by_reader[key][1] if key in before_by_reader else []
        tree_paths = tree_by_reader[key][1] if key in tree_by_reader else []
        names += reader.added_public_names(
            _sources(Path(review.base_tree_path), before_paths),
            _sources(root, tree_paths),
            set(changed),
        )
    return names


def caller_table(review: Review) -> TableFigure | None:
    """足した公開の口と呼ぶ側の表。足した名前が無い、または変更前の木が無ければ None。"""
    rows = tuple(_row(name) for name in public_names(review))
    return TableFigure(INTRO, HEADER, rows) if rows else None


def compact_callers(
    review: Review, names: list[AddedPublicName], nodes: ChangeNodes, *, cross_file_only: bool
) -> str:
    """定義と変更ファイルの呼ぶ側を残し、変更外の同名照合は代表と件数で表す。"""
    paths = set(changed_files.file_changes(review))
    lines = [
        "### 追加した公開 API と呼ぶ側",
        (
            "変更後の同名照合（型解決なし）。未検出は未使用の根拠にしない。"
            "変更ファイルを優先した代表 1 件と全照合数を掲示する。"
            "照合先の呼び出し行は未収録。"
        ),
    ]
    adapters = registry.adapters()
    omitted_test = 0
    omitted_local = 0
    rows_by_path: dict[str, list[str]] = defaultdict(list)
    boundary_counts: dict[tuple[str, str], int] = defaultdict(int)
    for name in names:
        if registry.is_test_path(name.path, adapters):
            omitted_test += 1
            continue
        relevant = (
            [
                site
                for site in name.callers
                if (caller_path := site.rpartition(" の ")[0]) in paths
                and caller_path != name.path
            ]
            if cross_file_only
            else name.callers
        )
        if cross_file_only and not relevant:
            omitted_local += 1
            continue
        representative = min(
            relevant,
            key=lambda site: (site.rpartition(" の ")[0] not in paths, site),
            default=NO_CALLER,
        )
        caller_path, sep, caller_name = representative.rpartition(" の ")
        if cross_file_only:
            boundary = name.path, caller_path
            boundary_counts[boundary] += 1
            if boundary_counts[boundary] > 1:
                continue
        shown = f"{nodes.path(caller_path)}{sep}{caller_name}" if sep else representative
        rows_by_path[name.path].append(f"{name.line} {name.name} → {shown} ({len(name.callers)})")
    for path, rows in sorted(rows_by_path.items()):
        lines.extend((nodes.path(path), *rows))
    if cross_file_only:
        lines.append(
            "定義ファイル・代表の照合先ファイルの組ごとに定義を 1 件掲載。省略した定義数:"
        )
        lines.extend(
            f"{nodes.path(path)} → {nodes.path(caller)}: {count - 1}"
            for (path, caller), count in sorted(boundary_counts.items())
            if count > 1
        )
        lines.append(
            f"別の変更ファイルに照合先がない定義 {omitted_local} 件を省略。"
            "各定義の照合は原則 P2 に掲載。"
        )
    if omitted_test:
        lines.append(f"テストの定義 {omitted_test} 件は省略。")
    return "\n".join(lines)
