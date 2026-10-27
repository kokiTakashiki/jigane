"""実装のスキルのシャード。単位（差分またはファイル）を、大きさが揃うよう分ける。

シャードは計画ごとの型の和にする。計画の種類は ShardPlan だけが持つ。
"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from jigane.design.review_documents import read_attachments, read_document, version_drifts
from jigane.git.tree_files import MAX_REVIEW_FILE_BYTES, TreeFileTooLargeError, read_tree_bytes
from jigane.groups.definitions import GroupId
from jigane.judgment_map.changes import (
    OMITTED_BINARY,
    OMITTED_GENERATED,
    OMITTED_TOO_LARGE,
    file_change,
)
from jigane.judgment_map.classification import is_whole_deletion, matches_any
from jigane.judgment_map.configuration import load_config
from jigane.llm_runs import claude
from jigane.policy_materials.design_doc import shard_materials
from jigane.preconditions.required import required
from jigane.reviews.attachments import AttachmentContent, VersionDrift
from jigane.reviews.lifecycle import (
    DesignDocOrigin,
    FeatureOrigin,
    PullRequestOrigin,
    RangeOrigin,
    Review,
    ReviewError,
    ReviewOrigin,
    load_pairs,
)

SHARD_BYTES = 30_000
MAX_SHARDS = 4
LOGGER = logging.getLogger(__name__)
_OMITTED_MARKERS = (OMITTED_GENERATED, OMITTED_BINARY, OMITTED_TOO_LARGE)


class ShardPlanKind(StrEnum):
    """シャードの計画の種類。差分の周り、選んだファイルの全文、設計書とファイル。"""

    DIFF = "diff"
    FILES = "files"
    DESIGN = "design"


def plan_kind_of(origin: ReviewOrigin) -> ShardPlanKind:
    """レビューの出所から計画の種類を決める、唯一の置き場。"""
    match origin:
        case PullRequestOrigin() | RangeOrigin():
            return ShardPlanKind.DIFF
        case FeatureOrigin():
            return ShardPlanKind.FILES
        case DesignDocOrigin():
            return ShardPlanKind.DESIGN
        case _:
            assert_never(origin)


def plan_kind_for(group: GroupId, review: Review) -> ShardPlanKind:
    """群が使う計画の種類。実装とセキュリティは、出所の種類をそのまま使う。"""
    kind = plan_kind_of(review.origin)
    match group:
        case GroupId.MICRO | GroupId.SECURITY:
            return kind
        case GroupId.MACRO:
            raise ValueError("方針の群はシャードに分けません")
        case _:
            assert_never(group)


@dataclass(frozen=True)
class Unit:
    path: str
    text: str
    size: int


@dataclass(frozen=True)
class _UnitShard:
    """単位（差分またはファイルの本文）の束を受け持つシャードの共通部。"""

    units: tuple[Unit, ...]

    @property
    def paths(self) -> list[str]:
        return sorted(unit.path for unit in self.units)

    @property
    def size(self) -> int:
        return sum(unit.size for unit in self.units)


@dataclass(frozen=True)
class DiffShard(_UnitShard):
    """差分の周りを受け持つシャード。units は変更したファイルごとの差分。"""


@dataclass(frozen=True)
class FullTextShard(_UnitShard):
    """選んだファイルの全文を受け持つシャード。units はファイルごとの本文。"""


@dataclass(frozen=True)
class DesignFileShard(_UnitShard):
    """設計書の計画で、選んだコードのファイルを受け持つシャード。"""

    document_path: str
    # 設計書の全文（行番号付き）
    document: str
    # 受け持つファイルごとの、設計書が名指した節と推しの理由
    selection: str
    # 付属の文書。パスは指摘の根拠に認める位置になる
    attachments: tuple[AttachmentContent, ...] = ()

    @property
    def size(self) -> int:
        texts = (self.document, self.selection, *(item.text for item in self.attachments))
        return super().size + sum(len(text.encode("utf-8")) for text in texts)


@dataclass(frozen=True)
class DesignDocumentShard:
    """設計書の計画で、設計書そのものを受け持つシャード。単位は持たない。"""

    document_path: str
    # 設計書の全文（行番号付き）と見出しの木
    document: str
    structure: str
    # ファイルのシャードのどれかが受け持つファイル。受け渡しの懸念の振り分けに使う
    covered: frozenset[str]
    # 付属の文書。パスは指摘の根拠に認める位置になる
    attachments: tuple[AttachmentContent, ...] = ()

    @property
    def units(self) -> tuple[Unit, ...]:
        return ()

    @property
    def paths(self) -> list[str]:
        return [self.document_path]

    @property
    def size(self) -> int:
        texts = (self.document, self.structure, *(item.text for item in self.attachments))
        return sum(len(text.encode("utf-8")) for text in texts)


Shard = DiffShard | FullTextShard | DesignFileShard | DesignDocumentShard


def scope_paths(shard: Shard) -> set[str]:
    """指摘の根拠に認める位置のパス。設計書の計画のファイルのシャードは設計書も認める。"""
    match shard:
        case DesignFileShard():
            return {*shard.paths, shard.document_path, *(item.path for item in shard.attachments)}
        case DesignDocumentShard():
            return {*shard.paths, *(item.path for item in shard.attachments)}
        case DiffShard() | FullTextShard():
            return set(shard.paths)
        case _:
            assert_never(shard)


@dataclass(frozen=True)
class ShardPlan:
    kind: ShardPlanKind
    # 差分の単位、またはファイルの単位。設計書は含まない
    units: tuple[Unit, ...]
    shards: tuple[Shard, ...]
    # 実行を止めないが利用者へ知らせる、保存した版との食い違い。文は drift_notice が組む
    drifts: tuple[VersionDrift, ...] = ()
    # 秘密のファイル名に当たるため、単位に入れず送らなかったパス。利用者へ知らせる
    withheld: tuple[str, ...] = ()


def _unit(path: str, text: str) -> Unit:
    return Unit(path, text, len(text.encode("utf-8")))


def build_units(review: Review) -> list[Unit]:
    """レビューの対象から単位を作る。丸ごと削除したファイルと生成物は入れない。"""
    return build_units_and_withheld(review)[0]


def build_units_and_withheld(review: Review) -> tuple[list[Unit], tuple[str, ...]]:
    """単位と、秘密のファイル名に当たるため単位に入れなかったパス。本文は読まない。"""
    withheld: list[str] = []
    root = Path(review.repository_root)
    base_tree = review.base_tree_path
    if plan_kind_of(review.origin) is ShardPlanKind.DIFF and base_tree is not None:
        return _diff_units(review, root, base_tree, withheld), tuple(withheld)
    return _file_units(review, root, withheld), tuple(withheld)


def _diff_units(review: Review, root: Path, base_tree: Path, withheld: list[str]) -> list[Unit]:
    # PR の作者が設定を緩められないよう、除外は変更前のツリーから読む
    config = load_config(base_tree)
    units: list[Unit] = []
    for pair in load_pairs(review.id):
        # new_path が None は丸ごとの削除。実装は変更後のコードを読むため入れない。
        if is_whole_deletion(pair):
            continue
        new_path = required(pair.new_path)
        if matches_any(new_path, config.exclude):
            continue
        # file_change は old_path の本文も base_tree から読むため、改名や複写の旧名も見る
        secret = next(
            (p for p in (pair.old_path, new_path) if p is not None and claude.is_secret_file(p)),
            None,
        )
        if secret is not None:
            withheld.append(secret)
            continue
        lines = file_change(base_tree, root, pair, config)
        if lines[-1] in _OMITTED_MARKERS:
            continue
        units.append(_unit(new_path, "\n".join(lines) + "\n"))
    return units


def _file_units(review: Review, root: Path, withheld: list[str]) -> list[Unit]:
    exclude = load_config(root).exclude
    units: list[Unit] = []
    for path in review.paths:
        if matches_any(path, exclude):
            continue
        if claude.is_secret_file(path):
            withheld.append(path)
            continue
        try:
            data = read_tree_bytes(root, path, max_bytes=MAX_REVIEW_FILE_BYTES)
        except TreeFileTooLargeError:
            LOGGER.warning(
                "%s は %d バイトを超えるため、レビューの対象から除きました",
                path,
                MAX_REVIEW_FILE_BYTES,
            )
            continue
        if data is None:
            continue
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError:
            # バイナリは行を読めないため、実装の対象にしない。
            continue
        units.append(_unit(path, f"=== {path} ===\n{body}\n"))
    return units


def shard_count(total_bytes: int, unit_count: int) -> int:
    if unit_count == 0:
        return 0
    wanted = math.ceil(total_bytes / SHARD_BYTES)
    # 単位より多く分けても空のシャードが増えるだけのため、単位の数も上限にする。
    return max(1, min(wanted, MAX_SHARDS, unit_count))


def assign_shards[S](units: list[Unit], make: Callable[[tuple[Unit, ...]], S]) -> list[S]:
    """最長処理時間優先。大きい順（同じ大きさはパス順）に、最も軽いシャードへ入れる。"""
    count = shard_count(sum(unit.size for unit in units), len(units))
    buckets: list[list[Unit]] = [[] for _ in range(count)]
    loads = [0] * count
    for unit in sorted(units, key=lambda item: (-item.size, item.path)):
        lightest = loads.index(min(loads))
        buckets[lightest].append(unit)
        loads[lightest] += unit.size
    return [make(tuple(bucket)) for bucket in buckets]


def _design_shards(
    review: Review, origin: DesignDocOrigin, units: list[Unit]
) -> tuple[list[Shard], tuple[VersionDrift, ...]]:
    root = Path(review.repository_root)
    document = root / origin.document_path
    if not document.is_file():
        raise ReviewError(f"設計書がありません: {origin.document_path}")
    read = read_document(document)
    materials = shard_materials(
        root,
        origin.document_path,
        read.text,
        origin.selection,
    )
    attached = read_attachments(review)
    drifts = version_drifts(review, read, attached)

    def file_shard(bucket: tuple[Unit, ...]) -> DesignFileShard:
        paths = sorted(unit.path for unit in bucket)
        return DesignFileShard(
            bucket,
            origin.document_path,
            materials.document,
            "\n".join(materials.reasons.get(path, f"- {path}") for path in paths),
            attached,
        )

    file_shards = assign_shards(units, file_shard)
    document_shard = DesignDocumentShard(
        origin.document_path,
        materials.document,
        materials.structure,
        frozenset(path for shard in file_shards for path in shard.paths),
        attached,
    )
    return [document_shard, *file_shards], drifts


def build_plan(review: Review, group: GroupId) -> ShardPlan:
    """群と出所から、計画の種類、単位、シャードを組む。種類ごとの分岐はここだけ。"""
    kind = plan_kind_for(group, review)
    units, withheld = build_units_and_withheld(review)
    match kind:
        case ShardPlanKind.DIFF:
            return ShardPlan(
                kind, tuple(units), tuple(assign_shards(units, DiffShard)), withheld=withheld
            )
        case ShardPlanKind.FILES:
            return ShardPlan(
                kind, tuple(units), tuple(assign_shards(units, FullTextShard)), withheld=withheld
            )
        case ShardPlanKind.DESIGN:
            origin = review.origin
            if not isinstance(origin, DesignDocOrigin):
                # 設計書の計画を決めるのは出所だけ（plan_kind_of）。型を絞るための分岐
                raise TypeError("設計書の計画は設計書の出所にだけ組めます")
            # 選んだファイルが 0 件ならファイルのシャードは無く、設計書のシャードだけが残る。
            # 設計書の全文はシャードの上限で切らない。上限は assign_shards の単位にだけ掛かる
            design_shards, drifts = _design_shards(review, origin, units)
            return ShardPlan(kind, tuple(units), tuple(design_shards), drifts, withheld)
        case _:
            assert_never(kind)
