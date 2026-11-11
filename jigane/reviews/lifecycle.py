import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, assert_never

from jigane.flaky.catalog import load_catalog
from jigane.flaky.gates import update_gates
from jigane.gates.entry import Gates
from jigane.git import process
from jigane.git.repository import Repository, identify, identify_git
from jigane.git.revision import CommitSha, parse_commit_sha
from jigane.git.tree_files import walk_tree_files
from jigane.judgment_map.changes import CHANGES_CHAR_LIMIT, CHANGES_FILE, render_changes
from jigane.judgment_map.classification import (
    BotTouch,
    FilePair,
    FindingTouch,
    JudgmentMap,
    Segment,
    build_judgment_map,
    file_line_counts,
)
from jigane.judgment_map.configuration import Config, load_config
from jigane.judgment_map.declaration import (
    Declaration,
    commits_declaration,
    extract_declaration,
    intent_declaration,
    pull_request_declaration,
)
from jigane.judgment_map.map_kind import (
    MAP_FILE,
    CodeMap,
    DesignDocMap,
    JudgmentMapKind,
    dump_map,
)
from jigane.judgment_map.region_table import CodeRegion, Region, RegionTable
from jigane.pull_request_context import bots
from jigane.pull_request_context.background import (
    Background,
    collect_background,
    collect_declaration_background,
    collect_range_background,
    save_background,
)
from jigane.pull_request_context.fetching import (
    PullRequestMetadata,
    changed_file_pairs,
    ensure_repository_cache,
    extract_tree,
    fetch_metadata,
    fetch_pull_request,
    parse_pull_request_url,
)
from jigane.reviews.range_head import (
    UNTRACKED,
    BaseLost,
    GitPlace,
    GitPlaceUnusable,
    GitProcessQueries,
    HeadTracking,
    RefreshHead,
    ResolvedRange,
    TrackedBranch,
    new_range_place,
    plan_refresh_range,
    resolve_range,
    run_git,
    select_git_place,
)
from jigane.reviews.review_formats import (
    PAIRS_FILE,
    PULL_REQUEST_FIELDS,
    REGIONS_FILE,
    dump_pairs,
    dump_regions,
    dump_review,
    load_regions,
)
from jigane.reviews.stored_pairs import load_pairs
from jigane.static_analysis.intake import (
    NO_STATIC,
    STATIC_FINDINGS_FILE,
    SarifRequest,
    StaticFindingsStore,
    StaticSelection,
    import_sarif,
    select_for_refresh,
    static_to_save,
    static_touches,
    static_warnings,
)
from jigane.storage.atomic_write import write_text_atomic
from jigane.storage.review_creation import (
    SaveTarget,
    direct_save_target,
    prepare_save_target,
    staged_save_target,
    staging_directory,
)
from jigane.storage.review_error import (
    ReviewError as ReviewError,  # noqa: PLC0414 他のモジュールが jigane.reviews.lifecycle から再エクスポートとして取り込む
)
from jigane.storage.store import (
    REVIEW_FILE as REVIEW_FILE,  # noqa: PLC0414 開発用リポジトリが jigane.reviews.lifecycle から取り込む継ぎ目の名前
)
from jigane.storage.store import (
    STAGING_PREFIX as STAGING_PREFIX,  # noqa: PLC0414 開発用リポジトリが jigane.reviews.lifecycle から取り込む継ぎ目の名前
)
from jigane.storage.store import (
    CheckedReviewId,
    ReviewKind,
    new_review_id,
)
from jigane.text.document_files import DOCUMENT_ONLY_NOTICE, all_documents

BASE_TREE = "trees/base"
HEAD_TREE = "trees/head"
# 変更の文の上限の警告。宣言の警告と区別して引き継ぐための先頭の文言
LIMIT_WARNING_PREFIX = "変更の文が上限"


class ReviewKindMismatchError(ReviewError):
    """識別子の種類と、保存した出所が導く種類が食い違う記録。"""


@dataclass
class DiffSource:
    """差分のレビューの、変更前の木と二つのコミット。出所が PR でも、他のものでも持つ。"""

    base_sha: str
    head_sha: str
    merge_base: str
    # 変更前のツリー。変更後のツリーは Review.repository_root
    base_tree: str

    @staticmethod
    def path_of(base_tree: str) -> Path:
        """保存した変更前の木の値から Path を作る。Path を作るのはここだけ。"""
        return Path(base_tree)

    @property
    def base_tree_path(self) -> Path:
        return self.path_of(self.base_tree)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalRange:
    """出所が手元の範囲のときだけ持つ値。PR の PullRequestInfo に当たる。"""

    # 作ったときの範囲の文字列（例 main..feature）。DiffSource の base_sha と head_sha は、
    # この範囲を解いたコミット
    range_text: str
    # 作ったときの作業ディレクトリの git の根。refresh が範囲を解き直す場所。
    # repository_root は変更後の木を指すので代わりにならない
    working_directory: str
    # 共通の git ディレクトリの絶対パス。作業ディレクトリが消えた後の、git を走らせる代わりの場所。
    # 空は代わりの場所を持たないこと
    git_directory: str = ""
    head_tracking: HeadTracking = UNTRACKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "range_text": self.range_text,
            "working_directory": self.working_directory,
            "git_directory": self.git_directory,
            "head_tracking": None if self.head_tracking is None else self.head_tracking.to_dict(),
        }


def read_stored_local_range(stored: dict[str, Any] | None) -> LocalRange | None:
    """保存した review.json の local_range を読む。null なら範囲ではない。"""
    if stored is None:
        return None
    try:
        raw_tracking = stored["head_tracking"]
        tracking = UNTRACKED if raw_tracking is None else TrackedBranch.from_dict(raw_tracking)
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewError(
            f"review.json の local_range の head_tracking が不正です: {error!r}"
        ) from error
    try:
        return LocalRange(**{**stored, "head_tracking": tracking})
    except TypeError as error:
        raise ReviewError(f"review.json の local_range が不正です: {error!r}") from error


@dataclass
class PullRequestInfo:
    """出所が PR のときだけ持つ値。"""

    url: str
    number: int
    title: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_stored_sources(
    stored: dict[str, Any] | None, source: dict[str, Any] | None
) -> tuple[DiffSource | None, PullRequestInfo | None]:
    """今の版の review.json の値を読む。diff_source と PR の情報は独立に読む。"""
    pull_request = (
        PullRequestInfo(**{name: stored[name] for name in PULL_REQUEST_FIELDS})
        if stored is not None
        else None
    )
    return (DiffSource(**source) if source is not None else None), pull_request


@dataclass(frozen=True)
class PullRequestOrigin:
    info: PullRequestInfo
    diff: DiffSource


@dataclass(frozen=True)
class RangeOrigin:
    range: LocalRange
    diff: DiffSource


@dataclass(frozen=True)
class FeatureOrigin:
    # 作ったときに渡された対象。リポジトリのルートからの posix の相対パス。
    # 保存していなかった古いファイルのレビューは None
    targets: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AttachedDocument:
    """設計書に付ける付属の文書（検証メモなど）。"""

    # リポジトリのルートからの posix の相対パス
    path: str
    # 内容の SHA-256（16 進）
    version: str


@dataclass(frozen=True)
class DesignDocOrigin:
    """設計書のレビューの出所。保存するのは設計書の版、コードの版、利用者の選択、付属の文書で、構造、名指し、候補、いまの形の図は二つの版から導く。"""

    # 設計書のパス。リポジトリのルートからの posix の相対パス
    document_path: str
    # 設計書の内容の SHA-256（16 進）
    document_version: str
    # 作ったとき、または refresh したときの作業ディレクトリの HEAD のコミット
    code_version: str
    # 利用者が候補から選んだファイル。リポジトリのルートからの posix の相対パス
    selection: tuple[str, ...]
    # 利用者が選んだ付属の文書。付属を持たない記録（古い記録を含む）は空
    attachments: tuple[AttachedDocument, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        stored: dict[str, Any] = {
            "document_path": self.document_path,
            "document_version": self.document_version,
            "code_version": self.code_version,
            "selection": list(self.selection),
        }
        if self.attachments:
            # 付属の無い記録は、付属を持たなかった頃と同じ形で保存する
            stored["attachments"] = [
                {"path": item.path, "version": item.version} for item in self.attachments
            ]
        return stored

    @classmethod
    def from_dict(cls, stored: dict[str, Any]) -> "DesignDocOrigin":
        selection = stored["selection"]
        if not isinstance(selection, list) or not all(isinstance(item, str) for item in selection):
            raise TypeError(f"設計書の選択が文字列の列ではありません: {selection!r}")
        # 付属を持たない頃の記録には attachments の項目が無い
        attachments = stored.get("attachments", [])
        if not isinstance(attachments, list) or not all(
            isinstance(item, dict) and "path" in item and "version" in item for item in attachments
        ):
            raise TypeError(f"設計書の付属の文書が不正です: {attachments!r}")
        return cls(
            str(stored["document_path"]),
            str(stored["document_version"]),
            str(stored["code_version"]),
            tuple(selection),
            tuple(
                AttachedDocument(str(item["path"]), str(item["version"])) for item in attachments
            ),
        )


ReviewOrigin = PullRequestOrigin | RangeOrigin | FeatureOrigin | DesignDocOrigin


def _diff_of(origin: ReviewOrigin) -> DiffSource | None:
    match origin:
        case PullRequestOrigin(diff=diff) | RangeOrigin(diff=diff):
            return diff
        case FeatureOrigin() | DesignDocOrigin():
            return None
        case _:
            assert_never(origin)


def origin_kind(origin: ReviewOrigin) -> ReviewKind:
    match origin:
        case PullRequestOrigin():
            return ReviewKind.PULL_REQUEST
        case RangeOrigin():
            return ReviewKind.RANGE
        case FeatureOrigin():
            return ReviewKind.FEATURE
        case DesignDocOrigin():
            return ReviewKind.DESIGN_DOC
        case _:
            assert_never(origin)


@dataclass
class Review:
    id: CheckedReviewId
    created_at: str
    repository_root: str
    paths: list[str]
    declaration: Declaration
    # 宣言の警告を含まない、内容から作った警告。review.json の正本はこちら
    content_warnings: list[str]
    # 読み込みと保存の入口で一度だけ復号した値。dict の形（map.json）は保存の入出力の中に閉じる
    map_kind: JudgmentMapKind
    origin: ReviewOrigin
    # 作ったときは None。refresh が更新するたびに書き換える
    refreshed_at: str | None = None
    # 差分のレビューでのみ値を持つ。background.json に保存する
    background: Background | None = None
    # gates.json の内容。flaky のゲートを入れる前に作ったレビューは None
    gates: Gates | None = None

    @property
    def warnings(self) -> list[str]:
        """利用者に見せる警告の全体。宣言の警告と内容の警告から導き、保存の正本にしない。"""
        return compose_warnings(self.declaration, self.content_warnings)

    @property
    def mode(self) -> str:
        """review.json の mode は読まず、書き出しにだけ使う。kind から引く。"""
        return self.kind.mode

    @property
    def is_pull_request(self) -> bool:
        """出所が PR か。ボットの材料など PR 固有の動作は、ここから引く。

        refresh と reply は差分のレビューで共通なので、ここでは分けない。
        """
        return self.kind.is_pull_request

    @property
    def is_diff(self) -> bool:
        """差分のレビューか。mode の比較や diff_source の None 判定でなく、ここから引く。"""
        return self.kind.is_diff

    @property
    def kind(self) -> ReviewKind:
        """出所の種類。PR の情報と差分の出所の有無から導く。

        識別子の種類とは読むとき（load_review_at）と書くとき（save_review）に突き合わせる。
        """
        return origin_kind(self.origin)

    @property
    def diff_origin(self) -> DiffSource | None:
        """差分の出所。差分のレビューでなければ None。変更前の木の引き元。"""
        return _diff_of(self.origin)

    @property
    def base_tree_path(self) -> Path | None:
        """変更前の木の場所。差分のレビューでなければ None。"""
        diff = self.diff_origin
        return diff.base_tree_path if diff is not None else None

    def stored_fields(self) -> dict[str, Any]:
        """出所から導く保存の項目。形は出所の導入前と同じ。"""
        match self.origin:
            case PullRequestOrigin(info=info, diff=diff):
                return {
                    "targets": None,
                    "diff_source": diff.to_dict(),
                    "pull_request": info.to_dict(),
                    "local_range": None,
                    "design_doc": None,
                }
            case RangeOrigin(range=local_range, diff=diff):
                return {
                    "targets": None,
                    "diff_source": diff.to_dict(),
                    "pull_request": None,
                    "local_range": local_range.to_dict(),
                    "design_doc": None,
                }
            case FeatureOrigin(targets=targets):
                return {
                    "targets": list(targets) if targets is not None else None,
                    "diff_source": None,
                    "pull_request": None,
                    "local_range": None,
                    "design_doc": None,
                }
            case DesignDocOrigin() as design_doc:
                return {
                    "targets": None,
                    "diff_source": None,
                    "pull_request": None,
                    "local_range": None,
                    "design_doc": design_doc.to_dict(),
                }
            case _:
                assert_never(self.origin)

    def kind_fields(self) -> dict[str, Any]:
        """種類から導く JSON の項目。to_dict と list の行はここから組み立てる。"""
        return _kind_fields(self.kind)

    def to_stored_dict(self) -> dict[str, Any]:
        """review.json へ書く形。id から導ける種類の項目、warnings、宣言の表示名は保存しない。"""
        stored = self.stored_fields()
        return {
            "id": str(self.id),
            "created_at": self.created_at,
            "refreshed_at": self.refreshed_at,
            "repository_root": self.repository_root,
            "paths": self.paths,
            "targets": stored["targets"],
            "declaration": self.declaration.to_stored_dict(),
            "content_warnings": self.content_warnings,
            "diff_source": stored["diff_source"],
            "pull_request": stored["pull_request"],
            "local_range": stored["local_range"],
            "design_doc": stored["design_doc"],
            "background": self.background.to_dict() if self.background else None,
        }

    def to_dict(self) -> dict[str, Any]:
        """出力（--json）の形。保存の形に、導ける項目と判断の地図と関門を足す。"""
        map_dict = self.map_kind.to_dict()
        printed: dict[str, Any] = {}
        for key, value in self.to_stored_dict().items():
            printed[key] = self.declaration.to_dict() if key == "declaration" else value
            if key == "id":
                printed.update(self.kind_fields())
            elif key == "declaration":
                printed["warnings"] = self.warnings
        printed["gates"] = self.gates.to_json() if self.gates is not None else None
        printed["judgment_map"] = {
            **map_dict,
            "line_counts": {
                **self.map_kind.table.line_counts.to_dict(),
                "by_file": file_line_counts(self.map_kind.segments),
            },
        }
        return printed


def _kind_fields(kind: ReviewKind) -> dict[str, Any]:
    return {
        "kind": str(kind),
        "mode": kind.mode,
        "is_diff": kind.is_diff,
        "is_pull_request": kind.is_pull_request,
    }


def _enumerate_files(root: Path, targets: list[Path]) -> list[str]:
    found: dict[str, None] = {}
    for target in targets:
        if target.is_file():
            found[target.relative_to(root).as_posix()] = None
            continue
        if not target.is_dir():
            raise ReviewError(f"対象のパスが存在しません: {target}")
        for path in walk_tree_files(root, target):
            found[path] = None
    return list(found)


@dataclass
class RegionChanges:
    """判断の箇所の番号の割り当てで起きた変化。J の番号の一覧。"""

    added: list[str]
    changed: list[str]
    removed: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number_order(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda text: int(text[1:]))


def _assign_numbers(
    regions: Sequence[Region], directory: Path
) -> tuple[list[Region], RegionChanges]:
    """内部の識別（key）に表示の番号を割り当てる。番号は再利用しない。"""
    path = directory / REGIONS_FILE
    state: dict[str, Any] = {"next_number": 1, "regions": {}}
    if path.is_file():
        state = load_regions(json.loads(path.read_text(encoding="utf-8")))
    added: list[str] = []
    changed: list[str] = []
    numbered: list[Region] = []
    for region in regions:
        entry = state["regions"].get(region.key)
        if entry is None:
            entry = {
                "id": f"J{state['next_number']}",
                "content_hash": region.content_hash,
            }
            state["next_number"] += 1
            state["regions"][region.key] = entry
            added.append(entry["id"])
        else:
            if entry.get("gone"):
                # 一度消えた key が戻った。番号は前のまま使う
                added.append(entry["id"])
                del entry["gone"]
            if entry["content_hash"] != region.content_hash:
                changed.append(entry["id"])
                entry["content_hash"] = region.content_hash
        numbered.append(replace(region, id=entry["id"]))
    current = {region.key for region in regions}
    removed: list[str] = []
    for key, entry in state["regions"].items():
        # gone の印は、前の回に報告済みで番号を取っておくためだけに残る entry を指す
        if key not in current and not entry.get("gone"):
            entry["gone"] = True
            removed.append(entry["id"])
    write_text_atomic(path, json.dumps(dump_regions(state), ensure_ascii=False, indent=2))
    return numbered, RegionChanges(
        _number_order(added),
        _number_order(changed),
        _number_order(removed),
    )


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_review_file(directory: Path, review: Review) -> None:
    write_text_atomic(
        directory / REVIEW_FILE,
        json.dumps(dump_review(review.to_stored_dict()), ensure_ascii=False, indent=2),
    )


# 入力から作り直す地図には無く、_assign_numbers が保存済みの状態から書き戻す領域の項目
_REVIEWER_OWNED = ("id",)


def _comparable_map(map_kind: JudgmentMapKind) -> dict[str, Any]:
    """保存のたびに変わりうる J の番号を除いた判断の地図。比べるだけで、読み手には渡さない。"""
    map_dict = map_kind.to_dict()
    regions = [
        {k: v for k, v in region.items() if k not in _REVIEWER_OWNED}
        for region in map_dict["regions"]
    ]
    return {**map_dict, "regions": regions}


def _same_content(
    review: Review,
    paths: Sequence[str],
    declaration: Declaration,
    content_warnings: Sequence[str],
    map_kind: JudgmentMapKind,
    *,
    gates: Gates,
) -> bool:
    return (
        review.gates == gates
        and review.paths == list(paths)
        and review.declaration == declaration
        and review.content_warnings == list(content_warnings)
        and _comparable_map(review.map_kind) == _comparable_map(map_kind)
    )


def _reject_foreign_items(kind: ReviewKind, foreign: Mapping[str, object], *own: object) -> None:
    """foreign のうち own に挙げた項目以外が入っていれば ReviewError にする。"""
    for label, value in foreign.items():
        if value is not None and not any(value is item for item in own):
            raise ReviewError(f"{kind} の記録に、別の種類の項目（{label}）が入っています")


def _pull_request_origin(
    pull_request: PullRequestInfo | None, diff_source: DiffSource | None
) -> PullRequestOrigin:
    if pull_request is None or diff_source is None:
        raise ReviewError("PR の記録に、PR の情報か差分の出所がありません")
    return PullRequestOrigin(pull_request, diff_source)


def _range_origin(local_range: LocalRange | None, diff_source: DiffSource | None) -> RangeOrigin:
    if local_range is None or diff_source is None:
        raise ReviewError("範囲の記録に、範囲の情報か差分の出所がありません")
    return RangeOrigin(local_range, diff_source)


def _design_doc_origin(
    targets: list[str] | None, design_doc: dict[str, Any] | None
) -> DesignDocOrigin:
    if targets is not None:
        raise ReviewError("設計書の記録に、ファイルの対象が入っています")
    if design_doc is None:
        raise ReviewError("設計書の記録に、設計書の情報がありません")
    return DesignDocOrigin.from_dict(design_doc)


def origin_from_stored(
    kind: ReviewKind,
    pull_request: PullRequestInfo | None,
    local_range: LocalRange | None,
    diff_source: DiffSource | None,
    targets: list[str] | None,
    *,
    design_doc: dict[str, Any] | None,
) -> ReviewOrigin:
    """保存した識別子の種類から、その種類の出所の型を組み立てる。

    stored_fields と対で、読み込みだけが使う。

    種類は識別子が決める。種類に属さない項目が入っている、属する項目が欠けている記録は、
    出所の種類が決まらないので ReviewError にする。
    """
    foreign = {
        "PR の情報": pull_request,
        "範囲の情報": local_range,
        "差分の出所": diff_source,
        "設計書の情報": design_doc,
    }
    match kind:
        case ReviewKind.PULL_REQUEST:
            _reject_foreign_items(kind, foreign, pull_request, diff_source)
            return _pull_request_origin(pull_request, diff_source)
        case ReviewKind.RANGE:
            _reject_foreign_items(kind, foreign, local_range, diff_source)
            return _range_origin(local_range, diff_source)
        case ReviewKind.FEATURE:
            _reject_foreign_items(kind, foreign)
            return FeatureOrigin(tuple(targets) if targets is not None else None)
        case ReviewKind.DESIGN_DOC:
            _reject_foreign_items(kind, foreign, design_doc)
            return _design_doc_origin(targets, design_doc)
        case _:
            assert_never(kind)


def compose_warnings(declaration: Declaration, content_warnings: Sequence[str]) -> list[str]:
    return [*declaration.warnings, *content_warnings]


@dataclass(frozen=True)
class ContentBase[OriginT: ReviewOrigin]:
    """保存する内容のうち、出所の種類によらず共通の項目。

    出所の型 OriginT は、ReviewContent が PR 以外の出所、PullRequestContent が
    PR の出所と絞る。出所の型は重ならず、両方を受ける側は和の別名 Content で受ける。
    """

    paths: tuple[str, ...]
    declaration: Declaration
    # 宣言の警告を含まない、内容から作った警告
    content_warnings: tuple[str, ...]
    judgment_map: JudgmentMap
    # 差分のレビューの背景。出所が PR でも範囲でも、ここに置く
    background: Background | None = None
    # 作る側が種類ごとの出所の型で直接渡す
    origin: OriginT = field(kw_only=True)
    # 一回の組み立てで使った指摘。書くかどうかは種類で決まる（static_to_save）
    static: StaticSelection = field(default=NO_STATIC, kw_only=True)

    @property
    def warnings(self) -> list[str]:
        """Review.warnings になる全体。宣言の警告と内容の警告の組み立てはここだけ。"""
        return compose_warnings(self.declaration, self.content_warnings)


@dataclass(frozen=True)
class ReviewContent(ContentBase[RangeOrigin | FeatureOrigin]):
    """PR 以外の出所（範囲、ファイル）の保存する内容。

    PR の出所の内容は PullRequestContent でしか作れない。
    """


@dataclass(frozen=True)
class PullRequestContent(ContentBase[PullRequestOrigin]):
    """PR を作る、または作り直すときだけ渡る内容。

    bot 解析は bot_comments.json に別ファイルで保存し、Review には載せない。
    Review の側へ置くと、読むたびに別ファイルの読み込みが要る。
    """

    bot_analysis: bots.BotAnalysis = field(default_factory=bots.BotAnalysis)


@dataclass(frozen=True)
class DesignDocContent(ContentBase[DesignDocOrigin]):
    """設計書のレビューの保存する内容。

    section_map は設計書の節から組んだ判断の地図の部品（build_section_map の値）。
    judgment_map は候補のコードの地図で、ゲートの入力であり、地図には参考として載る。

    paths は利用者の選択のうち作業ディレクトリに在るファイルで、空でもよい。
    選択した後に消えたファイルは、警告を付けて paths から外す。
    """

    section_map: RegionTable = field(kw_only=True)


Content = ReviewContent | PullRequestContent | DesignDocContent


def unreadable_segments_of(map_kind: JudgmentMapKind) -> Sequence[Segment]:
    """読めない記述を検出する区間。設計書のレビューは宣言を読む対象ではないので空。"""
    return map_kind.segments if isinstance(map_kind, CodeMap) else ()


def map_kind_of(content: Content) -> JudgmentMapKind:
    """保存する判断の地図の種類。地図の組み立ての出所ごとの分岐は、ここだけに置く。"""
    origin = content.origin
    match origin:
        case PullRequestOrigin() | RangeOrigin() | FeatureOrigin():
            return CodeMap.of(content.judgment_map)
        case DesignDocOrigin():
            if not isinstance(content, DesignDocContent):
                raise ReviewError("設計書の出所の内容が設計書の内容ではありません")
            return DesignDocMap.of(content.section_map, content.judgment_map)
        case _:
            assert_never(origin)


def _write_static_findings(directory: Path, store: StaticFindingsStore | None) -> None:
    if store is None:
        # 保存済みの指摘を重ねただけ、重ねなかった、または SARIF を渡していないので、書き換えない
        return
    write_text_atomic(
        directory / STATIC_FINDINGS_FILE,
        json.dumps(store.to_dict(), ensure_ascii=False, indent=2),
    )


def save_review(
    review_id: CheckedReviewId,
    content: Content,
    created_at: str,
    refreshed_at: str | None,
    repository_root: str,
) -> tuple[Review, RegionChanges]:
    """既存の保存先へ、番号の割り当てと保存をする。更新はここを通る。"""
    return _save_review(
        review_id,
        content,
        created_at,
        refreshed_at,
        repository_root,
        direct_save_target(review_id),
    )


def save_staged_review(
    review_id: CheckedReviewId,
    content: Content,
    created_at: str,
    refreshed_at: str | None,
    repository_root: str,
    *,
    staging: Path,
) -> tuple[Review, RegionChanges]:
    """作成の仮置きへ書く。木のパスなど記録に入る値は保存先のものを使い、木を読む場面だけ仮置きの実体へ読み替える。"""
    return _save_review(
        review_id,
        content,
        created_at,
        refreshed_at,
        repository_root,
        staged_save_target(review_id, staging),
    )


def _save_review(
    review_id: CheckedReviewId,
    content: Content,
    created_at: str,
    refreshed_at: str | None,
    repository_root: str,
    target: SaveTarget,
) -> tuple[Review, RegionChanges]:
    """番号の割り当てと保存の本体。作るときも更新するときもここを通る。"""
    judgment_map = content.judgment_map
    paths = content.paths
    origin = content.origin
    diff_source = _diff_of(origin)
    background = content.background
    content_kind = origin_kind(origin)
    if content_kind != review_id.kind:
        # 何も書かないうちに止める。食い違う記録を残すと、読むときに弾かれて一覧から消える
        raise ReviewKindMismatchError(
            f"識別子の種類（{review_id.kind}）と保存する内容の出所（{content_kind}）が"
            f"食い違います: {review_id}"
        )
    final_directory, directory = target

    def physical(path: Path) -> Path:
        if directory != final_directory and path.is_relative_to(final_directory):
            return directory / path.relative_to(final_directory)
        return path

    prepare_save_target(target)
    unnumbered = map_kind_of(content)
    numbered, region_changes = _assign_numbers(unnumbered.table.regions, directory)
    map_kind = unnumbered.with_regions(numbered)
    gates = update_gates(
        directory,
        physical(Path(repository_root)),
        paths,
        judgment_map.segments,
        load_catalog(),
        base_root=physical(diff_source.base_tree_path) if diff_source else None,
        renames=judgment_map.renames,
        unreadable_segments=unreadable_segments_of(map_kind),
    )
    review = Review(
        review_id,
        created_at,
        repository_root,
        list(paths),
        content.declaration,
        list(content.content_warnings),
        map_kind,
        origin,
        refreshed_at,
        background,
        gates,
    )
    write_review_file(directory, review)
    if background is not None:
        save_background(directory, background)
    write_text_atomic(
        directory / MAP_FILE, json.dumps(dump_map(map_kind), ensure_ascii=False, indent=2)
    )
    _write_static_findings(directory, static_to_save(content.static))
    if isinstance(content, PullRequestContent):
        bot_analysis = content.bot_analysis
        region_keys: dict[int, list[str]] = {}
        for region in numbered:
            # 節の行には bot のコメントが付かない（保存では空の列）
            if isinstance(region, CodeRegion):
                for comment_id in region.bot_comment_ids:
                    region_keys.setdefault(comment_id, []).append(region.key)
        write_text_atomic(
            directory / bots.BOT_COMMENTS_FILE,
            json.dumps(
                bots.dump_bot_comments(bot_analysis, region_keys), ensure_ascii=False, indent=2
            ),
        )
    return review, region_changes


def judge_files(root: Path, files: list[str], touches: Sequence[FindingTouch] = ()) -> JudgmentMap:
    """ファイル全体を判断の地図にする。ファイルと設計書のレビューが共有する。"""
    pairs = [FilePair(None, file) for file in files]
    return build_judgment_map(None, root, pairs, load_config(root), finding_touches=touches)


def _head_commit(root: Path) -> str:
    """ファイルのレビューには変更後のコミットが無いので、HEAD を解析したコミットとみなす。"""
    try:
        return run_git(root, "rev-parse", "HEAD").strip()
    except process.CommandError as error:
        raise ReviewError(f"HEAD を解けません: {root}") from error


def _build_feature_content(
    root: Path,
    targets: list[Path],
    intent: Declaration | None,
    static: StaticSelection = NO_STATIC,
) -> ReviewContent:
    files = _enumerate_files(root, targets)
    if not files:
        raise ReviewError("対象のファイルがありません")
    declaration = intent if intent is not None else extract_declaration(root, files)
    judgment_map = judge_files(root, files, static_touches(static))
    relative = tuple(target.relative_to(root).as_posix() for target in targets)
    origin = FeatureOrigin(relative)
    return ReviewContent(
        tuple(files),
        declaration,
        static_warnings(static),
        judgment_map,
        origin=origin,
        static=static,
    )


def read_intent(
    working_directory: Path, intent: str | None, intent_file: Path | None
) -> Declaration | None:
    """--intent と --intent-file の排他を確かめ、与えられた意図を宣言にする。"""
    if intent is not None and intent_file is not None:
        raise ReviewError("--intent と --intent-file は同時に指定できません")
    if intent_file is not None:
        return intent_declaration((working_directory / intent_file).read_text(encoding="utf-8"))
    if intent is not None:
        return intent_declaration(intent)
    return None


def _repository_of_targets(working_directory: Path, targets: list[Path]) -> Repository:
    """対象のパスが属するリポジトリを返す。対象がすべて git の外なら作業ディレクトリのもの。"""
    inside: dict[Path, Repository] = {}
    outside: list[Path] = []
    for target in targets:
        # 存在しないパスは、存在する最も近い祖先で所属を決める
        directory = target
        while not directory.is_dir():
            directory = directory.parent
        repository = identify_git(directory)
        if repository is None:
            outside.append(target)
        else:
            inside.setdefault(repository.root, repository)
    if not inside:
        # git の外だけを渡す使い方（git の無い環境を含む）を支えるため、
        # 作業ディレクトリを根にする。
        # 根の外の対象は、呼び出し側が「リポジトリの外」として落とす。
        return identify(working_directory)
    if outside:
        raise ReviewError(f"git のリポジトリの中と外の対象が混ざっています: {outside[0]}")
    if len(inside) > 1:
        roots = ", ".join(str(root) for root in inside)
        raise ReviewError(f"対象が複数のリポジトリにまたがっています: {roots}")
    return next(iter(inside.values()))


@dataclass(frozen=True)
class ReviewInputs:
    """レビューを作る入口と作り直す入口が、本体の引数のほかに受ける追加の入力。

    入力を足すときはここへ欄を足す。PR の入口は意図を受けないので、この型だけを受ける。
    """

    sarif: SarifRequest | None = None


@dataclass(frozen=True)
class DeclaredInputs(ReviewInputs):
    """意図の宣言を利用者の文で渡せる入口（範囲、ファイル、refresh）の入力。"""

    intent: str | None = None
    intent_file: Path | None = None


@dataclass(frozen=True)
class RefreshInputs(ReviewInputs):
    """意図を解き終えた後の refresh の入力。declaration_override は利用者が差し替えた宣言。"""

    declaration_override: Declaration | None = None


NO_REVIEW_INPUTS = ReviewInputs()
NO_DECLARED_INPUTS = DeclaredInputs()
NO_REFRESH_INPUTS = RefreshInputs()


def create_feature_review(
    working_directory: Path,
    paths: list[str],
    name: str,
    inputs: DeclaredInputs = NO_DECLARED_INPUTS,
) -> Review:
    sarif = inputs.sarif
    declared = read_intent(working_directory, inputs.intent, inputs.intent_file)
    targets = [(working_directory / path).resolve() for path in paths]
    repository = _repository_of_targets(working_directory, targets)
    review_id = new_review_id(repository.owner, repository.repo, name, ReviewKind.FEATURE)
    root = repository.root
    for target in targets:
        if not target.is_relative_to(root):
            raise ReviewError(f"対象がリポジトリの外にあります: {target}")
    static = import_sarif(sarif, _head_commit(root)) if sarif is not None else NO_STATIC
    content = _build_feature_content(root, targets, declared, static)
    with staging_directory(review_id) as staging:
        review, _ = save_staged_review(
            review_id,
            content,
            now(),
            None,
            str(root),
            staging=staging,
        )
    return review


def _write_diff_materials(
    directory: Path,
    base_tree: Path,
    head_tree: Path,
    pairs: list[FilePair],
    config: Config,
    *,
    touches: Sequence[BotTouch],
    finding_touches: Sequence[FindingTouch] = (),
) -> tuple[JudgmentMap, list[str]]:
    """判断の地図を作り、changes.diff と pairs.json を書く。戻りの文字列は本文の上限の警告。"""
    judgment_map = build_judgment_map(
        base_tree, head_tree, pairs, config, touches, finding_touches
    )
    changes, omitted_by_limit = render_changes(base_tree, head_tree, pairs, config)
    warnings: list[str] = []
    if omitted_by_limit:
        warnings.append(
            f"{LIMIT_WARNING_PREFIX}（{CHANGES_CHAR_LIMIT} 文字）を超え、"
            f"{omitted_by_limit} 件のファイルの本文を省きました"
        )
    (directory / CHANGES_FILE).write_text(changes, encoding="utf-8")
    write_text_atomic(directory / PAIRS_FILE, json.dumps(dump_pairs(pairs), ensure_ascii=False))
    return judgment_map, warnings


def _build_diff_tree(
    git_dir: Path,
    directory: Path,
    base_sha: str,
    head_sha: CommitSha,
    merge_base: CommitSha,
    *,
    stored_directory: Path,
) -> tuple[DiffSource, list[FilePair], Config]:
    """変更前と変更後の木を directory に展開し、ファイルの対と設定と DiffSource を返す。

    DiffSource に入れる木のパスは stored_directory のもの。directory は途中の置き場でありうる。
    """
    base_tree = directory / BASE_TREE
    head_tree = directory / HEAD_TREE
    extract_tree(git_dir, merge_base, base_tree)
    extract_tree(git_dir, head_sha, head_tree)
    pairs = changed_file_pairs(git_dir, merge_base, head_sha)
    # 変更を出す側が設定を緩められないよう、設定は変更前の木から読む
    config = load_config(base_tree)
    stored_base_tree = str(stored_directory / BASE_TREE)
    source = DiffSource(base_sha, str(head_sha), str(merge_base), stored_base_tree)
    return source, pairs, config


def _build_pull_request_content(
    directory: Path,
    git_dir: Path,
    metadata: PullRequestMetadata,
    merge_base: CommitSha,
    stored_directory: Path,
    static: StaticSelection = NO_STATIC,
) -> PullRequestContent:
    """二つのツリーと changes.diff を書き出し、保存する内容を組み立てる。"""
    base_tree = directory / BASE_TREE
    head_tree = directory / HEAD_TREE
    source, pairs, config = _build_diff_tree(
        git_dir,
        directory,
        str(metadata.base_sha),
        metadata.head_sha,
        merge_base,
        stored_directory=stored_directory,
    )
    bot_analysis = bots.collect(
        metadata.owner, metadata.repo, metadata.number, git_dir, head_tree, bots=config.bots
    )
    declaration = pull_request_declaration(metadata.title, metadata.body)
    warnings = bot_analysis.warnings()
    judgment_map, limit_warnings = _write_diff_materials(
        directory,
        base_tree,
        head_tree,
        pairs,
        config,
        touches=bot_analysis.touches(),
        finding_touches=static_touches(static),
    )
    warnings += limit_warnings
    warnings += static_warnings(static)
    info = PullRequestInfo(
        url=metadata.url,
        number=metadata.number,
        title=metadata.title,
    )
    background = collect_background(
        metadata, head_tree, base_tree, git_dir, str(merge_base), pairs=pairs
    )
    return PullRequestContent(
        tuple(pair.new_path for pair in pairs if pair.new_path is not None),
        declaration,
        tuple(warnings),
        judgment_map,
        background,
        bot_analysis,
        origin=PullRequestOrigin(info, source),
        static=static,
    )


def create_pull_request_review(url: str, inputs: ReviewInputs = NO_REVIEW_INPUTS) -> Review:
    sarif = inputs.sarif
    owner, repo, number = parse_pull_request_url(url)
    metadata = fetch_metadata(owner, repo, number)
    review_id = new_review_id(owner, repo, str(number), ReviewKind.PULL_REQUEST)
    directory = review_id.directory()
    git_dir = ensure_repository_cache(owner, repo)
    merge_base = fetch_pull_request(git_dir, metadata)
    static = import_sarif(sarif, str(metadata.head_sha)) if sarif is not None else NO_STATIC
    with staging_directory(review_id) as staging:
        content = _build_pull_request_content(
            staging, git_dir, metadata, merge_base, directory, static
        )
        review, _ = save_staged_review(
            review_id, content, now(), None, str(directory / HEAD_TREE), staging=staging
        )
    return review


_COMMIT_START = "\x01"
_COMMIT_FIELD = "\x02"
_RANGE_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def default_range_name(head: str, head_sha: str) -> str:
    """名前を省いたときの既定。head の refs 名から作り、HEAD や作れない名前はコミットの短縮形。"""
    candidate = _RANGE_NAME_UNSAFE.sub("-", head).strip("-._")
    if head == "HEAD" or not candidate:
        return head_sha[:8]
    return candidate


def _commits_text(root: Path, merge_base: str, head_sha: str) -> tuple[str, bool]:
    """(コミットの題と本文を並べた文, 本文を持つコミットがあるか)。"""
    output = run_git(
        root,
        "log",
        "--reverse",
        f"--format={_COMMIT_START}%s{_COMMIT_FIELD}%b",
        "--end-of-options",
        f"{merge_base}..{head_sha}",
    )
    entries: list[str] = []
    has_body = False
    for record in output.split(_COMMIT_START)[1:]:
        subject, _, body = record.partition(_COMMIT_FIELD)
        has_body = has_body or bool(body.strip())
        entries.append(f"{subject}\n\n{body.strip()}" if body.strip() else subject)
    return "\n\n".join(entries), has_body


def _range_declaration(
    root: Path, resolved: ResolvedRange, kept: Declaration | None
) -> Declaration:
    """範囲のレビューの宣言を決める一か所。保つ宣言があればそれ、無ければコミットから作る。"""
    if kept is not None:
        return kept
    commits_text, has_body = _commits_text(root, resolved.merge_base, resolved.head_sha)
    return commits_declaration(commits_text, has_body=has_body)


def _build_range_content(
    root: Path,
    directory: Path,
    local_range: LocalRange,
    resolved: ResolvedRange,
    intent: Declaration | None,
    *,
    stored_directory: Path,
    static: StaticSelection = NO_STATIC,
) -> ReviewContent:
    base_tree = directory / BASE_TREE
    head_tree = directory / HEAD_TREE
    merge_base = resolved.merge_base
    head_sha = resolved.head_sha
    source, pairs, config = _build_diff_tree(
        root,
        directory,
        resolved.base_sha,
        parse_commit_sha("範囲の先端", head_sha),
        parse_commit_sha("範囲の基点", merge_base),
        stored_directory=stored_directory,
    )
    declaration = _range_declaration(root, resolved, intent)
    judgment_map, diff_warnings = _write_diff_materials(
        directory,
        base_tree,
        head_tree,
        pairs,
        config,
        touches=(),
        finding_touches=static_touches(static),
    )
    paths = tuple(pair.new_path for pair in pairs if pair.new_path is not None)
    warnings = [*diff_warnings, *static_warnings(static)]
    # 削除を数えない。run と show の判定が持つ review.paths と同じ集合で、三か所の判定を揃えるため
    if all_documents(paths):
        warnings.append(DOCUMENT_ONLY_NOTICE)
    background = collect_range_background(root, base_tree, merge_base, pairs)
    return ReviewContent(
        paths,
        declaration,
        tuple(warnings),
        judgment_map,
        background=background,
        origin=RangeOrigin(local_range, source),
        static=static,
    )


def _new_local_range(root: Path, range_text: str, resolved: ResolvedRange) -> LocalRange:
    place = new_range_place(root, range_text, resolved.head_sha)
    return LocalRange(range_text, str(root), place.git_directory, place.head_tracking)


def create_range_review(
    working_directory: Path,
    range_text: str,
    name: str | None = None,
    inputs: DeclaredInputs = NO_DECLARED_INPUTS,
) -> Review:
    sarif = inputs.sarif
    declared = read_intent(working_directory, inputs.intent, inputs.intent_file)
    repository = identify(working_directory)
    root = repository.root
    resolved = resolve_range(root, range_text)
    static = import_sarif(sarif, resolved.head_sha) if sarif is not None else NO_STATIC
    resolved_name = (
        name if name is not None else default_range_name(resolved.head_name, resolved.head_sha)
    )
    review_id = new_review_id(repository.owner, repository.repo, resolved_name, ReviewKind.RANGE)
    directory = review_id.directory()
    with staging_directory(review_id) as staging:
        content = _build_range_content(
            root,
            staging,
            _new_local_range(root, range_text, resolved),
            resolved,
            declared,
            stored_directory=directory,
            static=static,
        )
        review, _ = save_staged_review(
            review_id, content, now(), None, str(directory / HEAD_TREE), staging=staging
        )
    return review


@dataclass
class RefreshResult:
    review: Review
    updated: bool
    region_changes: RegionChanges
    # 範囲のレビューだけが持つ。範囲でないレビューの出力には出さない
    head: RefreshHead | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "updated": self.updated,
            "regions": self.region_changes.to_dict(),
            "review": self.review.to_dict(),
        }
        if self.head is not None:
            result["head"] = self.head.to_dict()
        return result


def _refresh_diff_review(
    review: Review,
    source: DiffSource,
    head_sha: str,
    merge_base: str,
    declare: Callable[[], RefreshResult],
    *,
    build_content: Callable[[], Content],
    force_rebuild: bool = False,
) -> RefreshResult:
    """差分レビューの refresh の骨格。出所ごとの差は declare と build_content が持つ。

    declare は、ツリーが同じときに宣言などの変更を保存して返す。build_content は、
    ツリーが変わったときに全体を作り直す内容を組み立てる。
    """
    if not force_rebuild and head_sha == source.head_sha and merge_base == source.merge_base:
        return _rebuild_stored_map(declare(), source)
    updated, region_changes = save_review(
        review.id, build_content(), review.created_at, now(), review.repository_root
    )
    return RefreshResult(updated, updated=True, region_changes=region_changes)


def refresh_pull_request(review: Review, inputs: ReviewInputs = NO_REVIEW_INPUTS) -> RefreshResult:
    sarif = inputs.sarif
    origin = review.origin
    if not isinstance(origin, PullRequestOrigin):
        raise ReviewError(f"PR の出所でない記録を PR として refresh できません: {review.id}")
    source = origin.diff
    owner, repo = review.id.owner, review.id.repo
    metadata = fetch_metadata(owner, repo, int(review.id.key))
    git_dir = ensure_repository_cache(owner, repo)
    merge_base = fetch_pull_request(git_dir, metadata)

    def declare() -> RefreshResult:
        return _refresh_declaration(review, origin, metadata, RegionChanges([], [], []))

    return _refresh_diff_review(
        review,
        source,
        str(metadata.head_sha),
        str(merge_base),
        declare,
        build_content=lambda: _build_pull_request_content(
            review.id.directory(),
            git_dir,
            metadata,
            merge_base,
            review.id.directory(),
            _refresh_static(review, str(metadata.head_sha), sarif),
        ),
        force_rebuild=sarif is not None,
    )


def _refresh_static(review: Review, commit: str, sarif: SarifRequest | None) -> StaticSelection:
    """作り直しで使う指摘。--sarif があれば置き換え、無ければ select_for_refresh に任せる。"""
    replacement = import_sarif(sarif, commit) if sarif is not None else None
    return select_for_refresh(review.id.directory(), commit, replacement)


def refresh_range(
    review: Review,
    inputs: RefreshInputs = NO_REFRESH_INPUTS,
) -> RefreshResult:
    declaration_override = inputs.declaration_override
    sarif = inputs.sarif
    origin = review.origin
    if not isinstance(origin, RangeOrigin):
        raise ReviewError(f"範囲の出所でない記録を範囲として refresh できません: {review.id}")
    local_range = origin.range
    source = origin.diff
    rebuild_advice = (
        f"作り直すには delete してから new range してください: {review.id}"
        f"（保存した作業ディレクトリ: {local_range.working_directory!r}）"
    )
    place = select_git_place(local_range.working_directory, local_range.git_directory)
    match place:
        case GitPlaceUnusable.NO_WORKING_DIRECTORY:
            raise ReviewError(f"範囲を解き直す作業ディレクトリがありません。{rebuild_advice}")
        case GitPlaceUnusable.NOT_A_REPOSITORY:
            raise ReviewError(
                f"作業ディレクトリが git のリポジトリではありません。{rebuild_advice}"
            )
        case GitPlace():
            root = place.path
    planned = plan_refresh_range(
        GitProcessQueries(place.path),
        place,
        local_range.range_text,
        local_range.head_tracking,
        source.head_sha,
        source.base_sha,
        source.merge_base,
    )
    if isinstance(planned, BaseLost):
        raise ReviewError(
            f"記録した範囲の基点 {source.merge_base} を、先端 {planned.head_sha}"
            f"（記録した枝: {planned.branch}）が含まなくなりました。基点を失ったので、"
            f"新しい範囲で作り直してください。{rebuild_advice}"
        )
    resolved = planned.resolved
    head = planned.head
    kept = _kept_declaration(review, declaration_override)

    def declare() -> RefreshResult:
        declaration = _range_declaration(root, resolved, kept)
        if declaration == review.declaration:
            return RefreshResult(review, updated=False, region_changes=RegionChanges([], [], []))
        updated = replace(review, declaration=declaration, refreshed_at=now())
        write_review_file(review.id.directory(), updated)
        return RefreshResult(updated, updated=True, region_changes=RegionChanges([], [], []))

    result = _refresh_diff_review(
        review,
        source,
        resolved.head_sha,
        resolved.merge_base,
        declare,
        build_content=lambda: _build_range_content(
            root,
            review.id.directory(),
            local_range,
            resolved,
            kept,
            stored_directory=review.id.directory(),
            static=_refresh_static(review, resolved.head_sha, sarif),
        ),
        force_rebuild=sarif is not None,
    )
    return replace(result, head=head)


def _stored_bot_analysis(directory: Path) -> bots.BotAnalysis:
    """保存した bot_comments.json から、判定済みのボットのコメントを戻す。"""
    stored = bots.read_bot_comments(directory)
    if stored is None:
        # ボットのコメントを保存する前に作ったレビューは、触れた行を持たない
        return bots.BotAnalysis()
    return stored.analysis()


def _rebuild_stored_map(refreshed: RefreshResult, source: DiffSource) -> RefreshResult:
    """コミットが同じ差分レビューの判断の地図とゲートを、保存したツリーから作り直す。

    jigane の分類の規則が変わると、入力が同じでも結果が変わる。
    """
    review = refreshed.review
    directory = review.id.directory()
    base_tree = source.base_tree_path
    head_tree = directory / HEAD_TREE
    config = load_config(base_tree)
    bot_analysis = (
        _stored_bot_analysis(directory) if review.is_pull_request else bots.BotAnalysis()
    )
    # 保存した指摘は、解析したコミットが変更後のコミットと同じときだけ重なる
    static = select_for_refresh(directory, source.head_sha, None)
    judgment_map = build_judgment_map(
        base_tree,
        head_tree,
        load_pairs(review.id),
        config,
        bot_analysis.touches(),
        static_touches(static),
    )
    gates = update_gates(
        directory,
        head_tree,
        review.paths,
        judgment_map.segments,
        load_catalog(),
        base_root=base_tree,
        renames=judgment_map.renames,
        unreadable_segments=judgment_map.segments,
    )
    if (
        _comparable_map(review.map_kind) == _comparable_map(CodeMap.of(judgment_map))
        and review.gates == gates
    ):
        return refreshed
    # 木が同じなので、内容の警告は作り直さず引き継ぐ
    content: Content
    if isinstance(review.origin, PullRequestOrigin):
        content = PullRequestContent(
            tuple(review.paths),
            review.declaration,
            tuple(review.content_warnings),
            judgment_map,
            review.background,
            bot_analysis,
            origin=review.origin,
        )
    elif isinstance(review.origin, RangeOrigin | FeatureOrigin):
        content = ReviewContent(
            tuple(review.paths),
            review.declaration,
            tuple(review.content_warnings),
            judgment_map,
            review.background,
            origin=review.origin,
        )
    else:
        # 保存した木を持つのは差分の出所だけで、設計書のレビューはここへ来る道を持たない
        raise ReviewError(f"差分の出所でない記録の木は作り直せません: {review.id}")
    updated, region_changes = save_review(
        review.id, content, review.created_at, now(), review.repository_root
    )
    return RefreshResult(updated, updated=True, region_changes=region_changes)


def _kept_declaration(
    review: Review, declaration_override: Declaration | None = None
) -> Declaration | None:
    """refresh で宣言を保つか作り直すかを決める一か所。

    保つなら宣言、作り直すなら None。利用者が差し替えた宣言があれば、保存した宣言より優先する。
    """
    if declaration_override is not None:
        return declaration_override
    return review.declaration if review.declaration.source.kept_on_refresh else None


def _refresh_declaration(
    review: Review,
    origin: PullRequestOrigin,
    metadata: PullRequestMetadata,
    unchanged: RegionChanges,
) -> RefreshResult:
    """ツリーが同じ PR の、タイトルと本文の変更だけを取り込む。"""
    info = origin.info
    declaration = _kept_declaration(review) or pull_request_declaration(
        metadata.title, metadata.body
    )
    # 本文が変わると参照する文書も変わりうるため、タイトルと本文だけの更新でも集め直す
    previous = review.background
    background = Background(
        **collect_declaration_background(metadata, Path(review.repository_root)),
        # 過去の PR と所有者はツリーだけから決まるので、ツリーが同じなら引き継ぐ
        past_changes=previous.past_changes if previous else (),
        owners=previous.owners if previous else (),
    )
    if (
        declaration == review.declaration
        and metadata.title == info.title
        and background == review.background
    ):
        return RefreshResult(review, updated=False, region_changes=unchanged)
    # 内容の警告（変更の文の上限、ボットのコメント）は木から決まるので、本文だけの更新では
    # review.content_warnings を変えずに引き継ぐ。変わるのは宣言の警告だけ
    updated = replace(
        review,
        declaration=declaration,
        origin=replace(
            origin,
            info=replace(
                info,
                title=metadata.title,
            ),
        ),
        refreshed_at=now(),
        background=background,
    )
    write_review_file(review.id.directory(), updated)
    save_background(review.id.directory(), background)
    return RefreshResult(updated, updated=True, region_changes=unchanged)


def refresh_from_content(
    review: Review,
    build_content: Callable[[], ReviewContent | DesignDocContent],
    *,
    force_save: bool = False,
) -> RefreshResult:
    """内容を組み立て直し、変わっていれば保存する更新の骨格。種類によらず一つだけ持つ。

    種類ごとの差は build_content が持つ。build_content は新しい内容だけを返し、保存と
    変更なしの判定は骨格が行う。
    """
    content = build_content()
    gates = update_gates(
        review.id.directory(),
        Path(review.repository_root),
        content.paths,
        content.judgment_map.segments,
        load_catalog(),
        renames=content.judgment_map.renames,
        unreadable_segments=unreadable_segments_of(map_kind_of(content)),
    )
    if (
        not force_save
        and review.origin == content.origin
        and _same_content(
            review,
            content.paths,
            content.declaration,
            content.content_warnings,
            map_kind_of(content),
            gates=gates,
        )
    ):
        return RefreshResult(review, updated=False, region_changes=RegionChanges([], [], []))
    updated, region_changes = save_review(
        review.id, content, review.created_at, now(), review.repository_root
    )
    return RefreshResult(updated, updated=True, region_changes=region_changes)


def refresh_feature(
    review: Review,
    inputs: RefreshInputs = NO_REFRESH_INPUTS,
) -> RefreshResult:
    declaration_override = inputs.declaration_override
    sarif = inputs.sarif
    origin = review.origin
    if not isinstance(origin, FeatureOrigin):
        raise ReviewError(
            "ファイルのレビューの出所でない記録をファイルのレビューとして "
            f"refresh できません: {review.id}"
        )
    targets = origin.targets
    if targets is None:
        raise ReviewError(
            "作ったときの対象を保存していない古いレビューは、refresh できません。"
            f"作り直してください: {review.id}"
        )
    root = Path(review.repository_root)
    return refresh_from_content(
        review,
        lambda: _build_feature_content(
            root,
            [root / target for target in targets],
            _kept_declaration(review, declaration_override),
            _refresh_static(
                review,
                # 保存した指摘も SARIF も無いファイルのレビューは、git の外でも作り直せる
                _head_commit(root)
                if sarif is not None or (review.id.directory() / STATIC_FINDINGS_FILE).is_file()
                else "",
                sarif,
            ),
        ),
        force_save=sarif is not None,
    )
