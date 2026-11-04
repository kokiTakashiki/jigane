"""方針のレビューに渡す、共変更の表。

変更したファイルを起点に、git の履歴で一緒に変わったファイルを数え（`policy_materials.cochange`）、
二つのファイルの間に import の依存があるかを言語アダプタの索引で判定して並べる。
強く共変更するのに構造の依存が無い対を先に並べる。隠れた依存の手がかりになるため。
"""

from enum import StrEnum
from pathlib import Path
from typing import assert_never

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.git.process import CommandError
from jigane.languages import registry
from jigane.languages.base import SequenceAdapter
from jigane.policy_materials import cochange
from jigane.policy_materials.cochange import CoChange
from jigane.pull_request_context.fetching import repository_cache_path
from jigane.reviews.lifecycle import (
    DesignDocOrigin,
    FeatureOrigin,
    PullRequestOrigin,
    RangeOrigin,
    Review,
    scope_files,
)
from jigane.sequence.indexes import Indexes

SECTION_COCHANGE = "共変更の表"
INTRO = (
    "変更したファイルと、git の履歴で一緒に変わったファイルの対を並べた。"
    "強さは、一緒に変わった回数を、二つのファイルの変更回数の平均で割った値。"
    "マージのコミットと、多くのファイルを変えたコミットは数えていない。"
    "構造の依存は、どちらかのファイルがもう一方を import するか。"
    "言語の解析を持たないファイル（文書や設定）との対は「コード以外」とした。"
    "構造の依存が「なし」の対を先に並べた。"
    "同じパッケージ内の暗黙の参照など、import に現れない依存は見ていない。"
)
HEADER = ("変更したファイル", "相手", "一緒に変わった回数", "強さ", "構造の依存")
STRENGTH_DIGITS = 2


class StructuralDependency(StrEnum):
    """二つのファイルの間の構造の依存。値は表に出す語。"""

    PRESENT = "あり"
    ABSENT = "なし"
    NOT_CODE = "コード以外"


def _has_adapter(path: str) -> bool:
    return isinstance(registry.adapter_for(path), SequenceAdapter)


def structural_dependencies(
    root: Path, pairs: list[tuple[str, str]]
) -> dict[tuple[str, str], StructuralDependency]:
    """対ごとの構造の依存。どちらかが解析を持たない言語なら「コード以外」。"""
    code_paths = {path for pair in pairs for path in pair if _has_adapter(path)}
    indexes = Indexes(root, code_paths)
    result: dict[tuple[str, str], StructuralDependency] = {}
    for left, right in pairs:
        if left not in code_paths or right not in code_paths:
            result[(left, right)] = StructuralDependency.NOT_CODE
        elif _imports(indexes, left, right) or _imports(indexes, right, left):
            result[(left, right)] = StructuralDependency.PRESENT
        else:
            result[(left, right)] = StructuralDependency.ABSENT
    return result


def _imports(indexes: Indexes, importer: str, imported: str) -> bool:
    """importer の import が、imported のモジュールか、その中の名前を取り込むか。"""
    for index in indexes.indexes:
        module = index.modules.get(imported)
        sources = index.imported.get(importer)
        if module is None or sources is None:
            continue
        if any(name == module or name.startswith(module + ".") for name in sources.values()):
            return True
    return False


def _history_source(review: Review) -> tuple[Path, str] | None:
    """履歴を読む git の場所と、読む先のコミット。履歴を持たない出所は None。

    repository_root は保存した変更後の木で .git を持たないので、履歴の場所には使わない。
    """
    match review.origin:
        case RangeOrigin(range=local_range, diff=diff):
            return Path(local_range.working_directory), diff.head_sha
        case PullRequestOrigin(diff=diff):
            cache = repository_cache_path(review.id.owner, review.id.repo)
            return (cache, diff.head_sha) if cache.is_dir() else None
        case FeatureOrigin() | DesignDocOrigin():
            # 差分の先端のコミットが無く、読む先が定まらない
            return None
        case _:
            assert_never(review.origin)


def _co_changes(review: Review) -> list[CoChange] | None:
    """変更したファイルの共変更。履歴を読めない（git でない、コミットが無い）ときは None。"""
    source = _history_source(review)
    if source is None:
        return None
    git_dir, revision = source
    try:
        rows = cochange.count_co_changes(
            git_dir, frozenset(scope_files(review)), revision=revision
        )
    except (CommandError, OSError):
        return None
    return cochange.select_strong(rows, max_rows=len(rows))


def cochange_table(review: Review, max_rows: int = cochange.MAX_ROWS) -> TableFigure | None:
    """共変更の表。履歴が無い、または下限を満たす対が無ければ None。"""
    rows = _co_changes(review)
    if not rows:
        return None
    dependencies = structural_dependencies(
        Path(review.repository_root), [(row.origin, row.partner) for row in rows]
    )
    ordered = sorted(
        rows,
        key=lambda row: dependencies[(row.origin, row.partner)] != StructuralDependency.ABSENT,
    )
    shown = ordered[:max_rows]
    table_rows = tuple(
        TableRow(
            (
                row.origin,
                row.partner,
                str(row.shared_commits),
                f"{row.strength:.{STRENGTH_DIGITS}f}",
                dependencies[(row.origin, row.partner)].value,
            ),
            RowChange.UNCHANGED,
        )
        for row in shown
    )
    omitted = len(ordered) - len(shown)
    note = f"上限を超えた {omitted} 件は載せていない。" if omitted else None
    return TableFigure(INTRO, HEADER, table_rows, note)
