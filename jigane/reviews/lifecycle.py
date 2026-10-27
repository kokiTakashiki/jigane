import copy
import glob
import json
import re
import shutil
import uuid
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, assert_never

from jigane.flaky.catalog import load_catalog
from jigane.flaky.gates import load_gates, update_gates
from jigane.gates.entry import Gates
from jigane.git import process
from jigane.git.repository import Repository, identify, identify_git
from jigane.git.tree_files import walk_tree_files
from jigane.judgment_map.changes import CHANGES_CHAR_LIMIT, CHANGES_FILE, render_changes
from jigane.judgment_map.classification import (
    BotTouch,
    FilePair,
    JudgmentMap,
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
    load_map,
)
from jigane.judgment_map.region_table import CodeRegion, Region, RegionTable
from jigane.pull_request_context import bots
from jigane.pull_request_context.background import (
    Background,
    collect_background,
    collect_declaration_background,
    collect_range_background,
    load_background,
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
from jigane.storage.review_error import ReviewError as ReviewError  # noqa: PLC0414 他のモジュールが jigane.reviews.lifecycle から再エクスポートとして取り込む
from jigane.storage.review_error import ReviewExistsError
from jigane.storage.store import (
    ReviewId,
    ReviewKind,
    check_new_key,
    data_home,
    review_lock,
    write_text_atomic,
)
from jigane.storage.store_format import Upgrade, upgrade_mapping, upgrade_to_current

REVIEW_FILE = "review.json"
# review.json の形の版。保存物の形を変えたら一つ上げ、_REVIEW_UPGRADES に前の版からの移しを足す
REVIEW_FORMAT = 4
# regions.json の形の版。上げ方は REVIEW_FORMAT と同じ
REGIONS_FORMAT = 2
# pairs.json の形の版。上げ方は REVIEW_FORMAT と同じ
PAIRS_FORMAT = 2
# 削除が中身を退ける置き場の名前。データの置き場の直下に作る。一覧には出さない
TRASH_DIRECTORY = ".trash"
# 作成の途中の置き場の名前の頭。本物のレビューの名前は先頭が英数字か _ なので重ならない
STAGING_PREFIX = ".creating-"
# アーカイブの印。レビューの中身とは分けるので、refresh は触れない。中身は印を付けた時刻
ARCHIVE_MARKER_FILE = "archived"
ARCHIVED_EXCLUDE = "exclude"
ARCHIVED_ONLY = "only"
ARCHIVED_INCLUDE = "include"
PAIRS_FILE = "pairs.json"
REGIONS_FILE = "regions.json"
BASE_TREE = "trees/base"
HEAD_TREE = "trees/head"
# 変更の文の上限の警告。宣言の警告と区別して引き継ぐための先頭の文言
LIMIT_WARNING_PREFIX = "変更の文が上限"


class ReviewKindMismatchError(ReviewError):
    """識別子の種類と、保存した出所が導く種類が食い違う記録。"""


@dataclass
class DiffSource:
    """差分レビュー式の、変更前の木と二つのコミット。出所が PR でも、他のものでも持つ。"""

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PULL_REQUEST_FIELDS = ("url", "number", "title")
# 版 2 までの review.json の pull_request が持つ鍵。版 1 の移しが diff_source と分けるのに使う
_PULL_REQUEST_FIELDS_UNTIL_V2 = (*_PULL_REQUEST_FIELDS, "linked_issues")


def read_stored_local_range(stored: dict[str, Any] | None) -> LocalRange | None:
    """保存した review.json の local_range を読む。null なら範囲ではない。"""
    return LocalRange(**stored) if stored is not None else None


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
        PullRequestInfo(**{name: stored[name] for name in _PULL_REQUEST_FIELDS})
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
    # 保存していなかった古い機能レビュー式は None
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
    """設計書のレビューの出所。保存するのは設計書の版、コードの版、利用者の選択、付属の文書で、構造、名指し、推し、いまの形の図は二つの版から導く。"""

    # 設計書のパス。リポジトリのルートからの posix の相対パス
    document_path: str
    # 設計書の内容の SHA-256（16 進）
    document_version: str
    # 作ったとき、または refresh したときの作業ディレクトリの HEAD のコミット
    code_version: str
    # 利用者が推しから選んだファイル。リポジトリのルートからの posix の相対パス
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
    id: ReviewId
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
    # 差分レビュー式でのみ値を持つ。background.json に保存する
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

        refresh と reply は差分レビュー式で共通なので、ここでは分けない。
        """
        return self.kind.is_pull_request

    @property
    def is_diff(self) -> bool:
        """差分レビュー式か。mode の比較や diff_source の None 判定でなく、ここから引く。"""
        return self.kind.is_diff

    @property
    def kind(self) -> ReviewKind:
        """出所の種類。PR の情報と差分の出所の有無から導く。

        識別子の種類とは読むとき（load_review_at）と書くとき（save_review）に突き合わせる。
        """
        return origin_kind(self.origin)

    @property
    def diff_origin(self) -> DiffSource | None:
        """差分の出所。差分レビュー式でなければ None。変更前の木の引き元。"""
        return _diff_of(self.origin)

    @property
    def base_tree_path(self) -> Path | None:
        """変更前の木の場所。差分レビュー式でなければ None。"""
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


def _upgrade_regions_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の regions.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    for entry in upgraded["regions"].values():
        # judged は読み口の無いまま残った鍵で、今の版は書かない
        entry.pop("judged", None)
    return upgraded


# 保存された版から次の版への移し。load_regions が REGIONS_FORMAT まで順に回す
_REGIONS_UPGRADES: dict[int, Upgrade] = {1: _upgrade_regions_v1}


def load_regions(raw: object) -> dict[str, Any]:
    """regions.json を読む境の一か所。保存された版から今の版まで移した辞書を返す。"""
    if not isinstance(raw, dict):
        raise ReviewError("regions.json の最上位が辞書ではありません")
    return upgrade_mapping(REGIONS_FILE, raw, current=REGIONS_FORMAT, upgrades=_REGIONS_UPGRADES)


def dump_regions(state: Mapping[str, Any]) -> dict[str, Any]:
    """regions.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {"format": REGIONS_FORMAT, **{k: v for k, v in state.items() if k != "format"}}


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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _upgrade_review_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の review.json を次の版へ移す。鍵の欠けは、移す前の読み口が使っていた値で埋める。"""
    upgraded = copy.deepcopy(dict(raw))
    # 宣言の警告を保存する前の記録は、警告が無いものとして読む
    declaration = upgraded["declaration"]
    declaration.setdefault("warnings", [])
    # content_warnings を保存する前の記録は、宣言に警告が無かったので、warnings の全体が内容の警告
    upgraded.setdefault("content_warnings", list(upgraded["warnings"]))
    # 古い PR の記録は、diff_source を分けず PR の値に変更前の木の値を同居させていた
    stored_pull_request = upgraded.get("pull_request")
    if upgraded.get("diff_source") is None and stored_pull_request is not None:
        upgraded["diff_source"] = {
            key: value
            for key, value in stored_pull_request.items()
            if key not in _PULL_REQUEST_FIELDS_UNTIL_V2
        }
        upgraded["pull_request"] = {
            key: stored_pull_request[key] for key in _PULL_REQUEST_FIELDS_UNTIL_V2
        }
    # 出所の欄は、種類に属さないものを null で書く。古い記録は書いていない
    for name in (
        "refreshed_at",
        "targets",
        "pull_request",
        "diff_source",
        "local_range",
        "design_doc",
        "background",
    ):
        upgraded.setdefault(name, None)
    return upgraded


def _upgrade_review_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 2 の review.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    # PR の情報から issue を外した。版 2 までの保存物が持つ linked_issues は読み口が無いので落とす
    if upgraded.get("pull_request") is not None:
        upgraded["pull_request"].pop("linked_issues", None)
    return upgraded


def _upgrade_review_v3(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 3 の review.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    # 版 3 までの保存物が持つ次の鍵は、読み口が無いか id から導けるので落とす。
    # warnings は content_warnings の移しの材料だけだった（版 1 の移しが先に読む）
    for name in ("kind", "mode", "is_diff", "is_pull_request", "warnings"):
        upgraded.pop(name, None)
    upgraded["declaration"].pop("source_label", None)
    return upgraded


# 保存された版から次の版への移し。load_review_meta が REVIEW_FORMAT まで順に回す
_REVIEW_UPGRADES: dict[int, Upgrade] = {
    1: _upgrade_review_v1,
    2: _upgrade_review_v2,
    3: _upgrade_review_v3,
}


def load_review_meta(raw: object) -> dict[str, Any]:
    """review.json を読む境の一か所。保存された版から今の版まで移した辞書を返す。"""
    if not isinstance(raw, dict):
        raise ReviewError("review.json の最上位が辞書ではありません")
    return upgrade_mapping(REVIEW_FILE, raw, current=REVIEW_FORMAT, upgrades=_REVIEW_UPGRADES)


def dump_review(meta: Mapping[str, Any]) -> dict[str, Any]:
    """review.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {"format": REVIEW_FORMAT, **meta}


def _write_review_file(directory: Path, review: Review) -> None:
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
        raise ReviewError("設計書の記録に、機能の対象が入っています")
    if design_doc is None:
        raise ReviewError("設計書の記録に、設計書の情報がありません")
    return DesignDocOrigin.from_dict(design_doc)


def _origin_from_stored(
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
    # 差分レビュー式の背景。出所が PR でも範囲でも、ここに置く
    background: Background | None = None
    # 作る側が種類ごとの出所の型で直接渡す
    origin: OriginT = field(kw_only=True)

    @property
    def warnings(self) -> list[str]:
        """Review.warnings になる全体。宣言の警告と内容の警告の組み立てはここだけ。"""
        return compose_warnings(self.declaration, self.content_warnings)


@dataclass(frozen=True)
class ReviewContent(ContentBase[RangeOrigin | FeatureOrigin]):
    """PR 以外の出所（範囲、機能）の保存する内容。

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
    judgment_map は推しのコードの地図で、ゲートの入力であり、地図には参考として載る。

    paths は利用者の選択のうち作業ディレクトリに在るファイルで、空でもよい。
    選択した後に消えたファイルは、警告を付けて paths から外す。
    """

    section_map: RegionTable = field(kw_only=True)


Content = ReviewContent | PullRequestContent | DesignDocContent


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


def save_review(
    review_id: ReviewId,
    content: Content,
    created_at: str,
    refreshed_at: str | None,
    repository_root: str,
    *,
    staging: Path | None = None,
) -> tuple[Review, RegionChanges]:
    """作るときも更新するときも、番号の割り当てと保存はここを通る。

    staging を渡すと、保存先の代わりにそのディレクトリへ書く。木のパスなど記録に入る値は
    保存先のものを使い、木を読む場面だけ staging の中の実体へ読み替える。
    """
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
    final_directory = review_id.directory()
    directory = final_directory if staging is None else staging

    def physical(path: Path) -> Path:
        if staging is not None and path.is_relative_to(final_directory):
            return staging / path.relative_to(final_directory)
        return path

    directory.mkdir(parents=True, exist_ok=True)
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
    _write_review_file(directory, review)
    if background is not None:
        save_background(directory, background)
    write_text_atomic(
        directory / MAP_FILE, json.dumps(dump_map(map_kind), ensure_ascii=False, indent=2)
    )
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


def judge_files(root: Path, files: list[str]) -> JudgmentMap:
    """ファイル全体を判断の地図にする。機能と設計書のレビューが共有する。"""
    pairs = [FilePair(None, file) for file in files]
    return build_judgment_map(None, root, pairs, load_config(root))


def _build_feature_content(
    root: Path,
    targets: list[Path],
    intent: Declaration | None,
) -> ReviewContent:
    files = _enumerate_files(root, targets)
    if not files:
        raise ReviewError("対象のファイルがありません")
    declaration = intent if intent is not None else extract_declaration(root, files)
    judgment_map = judge_files(root, files)
    relative = tuple(target.relative_to(root).as_posix() for target in targets)
    origin = FeatureOrigin(relative)
    return ReviewContent(tuple(files), declaration, (), judgment_map, origin=origin)


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


def _existing_review_message(review_id: ReviewId) -> str:
    """識別子が既にあるときの案内。"""
    match review_id.kind:
        case ReviewKind.PULL_REQUEST:
            return (
                f"レビューが既にあります: {review_id}。作者の修正は refresh で取り込んでください"
            )
        case ReviewKind.RANGE:
            return (
                f"レビューが既にあります: {review_id}。作り直すには refresh してください。"
                "別のレビューを残すなら --name で別の名前を付けてください"
            )
        case ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
            return f"レビューが既にあります: {review_id}"
        case _:
            assert_never(review_id.kind)


def _existing_review_error(review_id: ReviewId) -> ReviewExistsError:
    return ReviewExistsError(review_id, _existing_review_message(review_id))


def _new_review_id(owner: str, repo: str, name: str, kind: ReviewKind) -> ReviewId:
    """新しい識別子を、名前の検査と既存の確かめを通して作る。"""
    try:
        check_new_key(kind, name)
        review_id = ReviewId(owner, repo, name, kind)
        ReviewId.parse(str(review_id))
    except ValueError as error:
        raise ReviewError(str(error)) from error
    if (review_id.directory() / REVIEW_FILE).is_file():
        raise _existing_review_error(review_id)
    return review_id


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


def _remove_directory_in_home(path: Path, review_id: ReviewId, *, staging: bool) -> None:
    """データの置き場の中の、この識別子の作成が作った（または残した）ディレクトリだけを消す。

    消してよいのは、識別子の保存先そのもの（staging=False）か、その親の下の作成途中の置き場
    （staging=True）に限る。置き場の外、置き場そのもの、シンボリックリンクは消さない。
    """
    home = data_home().resolve()
    final = review_id.directory()
    expected_parent = final.parent.resolve()
    if path.is_symlink():
        raise ReviewError(f"シンボリックリンクは消しません: {path}")
    resolved = path.resolve()
    if resolved == home or not resolved.is_relative_to(home):
        raise ReviewError(f"データの置き場の外か置き場そのものは消しません: {path}")
    if resolved.parent != expected_parent:
        raise ReviewError(f"このレビューの置き場の直下ではないので消しません: {path}")
    if staging != resolved.name.startswith(STAGING_PREFIX):
        raise ReviewError(f"消す対象の名前が想定と違います: {path}")
    if not staging and resolved.name != final.name:
        raise ReviewError(f"このレビューの保存先ではないので消しません: {path}")
    shutil.rmtree(resolved, ignore_errors=True)


@contextmanager
def _staging_directory(review_id: ReviewId) -> Iterator[Path]:
    """保存先と同じ親の下の隠しの置き場で組み立て、抜けるときに保存先へ改名する。

    失敗したら置き場を消して例外をそのまま上げる。改名が原子的なので、保存先に半端な
    ディレクトリが現れることはない。
    """
    final = review_id.directory()
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = final.parent / f"{STAGING_PREFIX}{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        yield staging
        _install_staging(staging, final, review_id)
    except BaseException:
        if staging.exists():
            _remove_directory_in_home(staging, review_id, staging=True)
        raise


def _install_staging(staging: Path, final: Path, review_id: ReviewId) -> None:
    if final.exists():
        if (final / REVIEW_FILE).is_file():
            raise _existing_review_error(review_id)
        # review.json の無い保存先は、以前の失敗が残した半端なもの。混ぜずに消してから置く
        _remove_directory_in_home(final, review_id, staging=False)
    try:
        staging.rename(final)
    except OSError as error:
        if (final / REVIEW_FILE).is_file():
            # 同じ名前の作成が先に改名を済ませた
            raise _existing_review_error(review_id) from error
        raise


def create_feature_review(
    working_directory: Path,
    paths: list[str],
    name: str,
    intent: str | None = None,
    intent_file: Path | None = None,
) -> Review:
    declared = read_intent(working_directory, intent, intent_file)
    targets = [(working_directory / path).resolve() for path in paths]
    repository = _repository_of_targets(working_directory, targets)
    review_id = _new_review_id(repository.owner, repository.repo, name, ReviewKind.FEATURE)
    root = repository.root
    for target in targets:
        if not target.is_relative_to(root):
            raise ReviewError(f"対象がリポジトリの外にあります: {target}")
    content = _build_feature_content(root, targets, declared)
    with _staging_directory(review_id) as staging:
        review, _ = save_review(
            review_id,
            content,
            _now(),
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
) -> tuple[JudgmentMap, list[str]]:
    """判断の地図を作り、changes.diff と pairs.json を書く。戻りの文字列は本文の上限の警告。"""
    judgment_map = build_judgment_map(base_tree, head_tree, pairs, config, touches)
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
    head_sha: str,
    merge_base: str,
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
    return DiffSource(base_sha, head_sha, merge_base, stored_base_tree), pairs, config


def _build_pull_request_content(
    directory: Path,
    git_dir: Path,
    metadata: PullRequestMetadata,
    merge_base: str,
    stored_directory: Path,
) -> PullRequestContent:
    """二つのツリーと changes.diff を書き出し、保存する内容を組み立てる。"""
    base_tree = directory / BASE_TREE
    head_tree = directory / HEAD_TREE
    source, pairs, config = _build_diff_tree(
        git_dir,
        directory,
        metadata.base_sha,
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
        directory, base_tree, head_tree, pairs, config, touches=bot_analysis.touches()
    )
    warnings += limit_warnings
    info = PullRequestInfo(
        url=metadata.url,
        number=metadata.number,
        title=metadata.title,
    )
    background = collect_background(
        metadata, head_tree, base_tree, git_dir, merge_base, pairs=pairs
    )
    return PullRequestContent(
        tuple(pair.new_path for pair in pairs if pair.new_path is not None),
        declaration,
        tuple(warnings),
        judgment_map,
        background,
        bot_analysis,
        origin=PullRequestOrigin(info, source),
    )


def create_pull_request_review(url: str) -> Review:
    owner, repo, number = parse_pull_request_url(url)
    metadata = fetch_metadata(owner, repo, number)
    review_id = _new_review_id(owner, repo, str(number), ReviewKind.PULL_REQUEST)
    directory = review_id.directory()
    git_dir = ensure_repository_cache(owner, repo)
    merge_base = fetch_pull_request(git_dir, metadata)
    with _staging_directory(review_id) as staging:
        content = _build_pull_request_content(staging, git_dir, metadata, merge_base, directory)
        review, _ = save_review(
            review_id, content, _now(), None, str(directory / HEAD_TREE), staging=staging
        )
    return review


_COMMIT_START = "\x01"
_COMMIT_FIELD = "\x02"
_RANGE_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _git(root: Path, *arguments: str) -> str:
    return process.run_command(["git", *arguments], cwd=root)


def _resolve_commit(root: Path, revision: str) -> str:
    try:
        return _git(
            root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"
        ).strip()
    except process.CommandError as error:
        if error.missing_executable:
            # git が無いことは refs の誤りではない。「必要なコマンドが見つからない」として伝える
            raise
        raise ReviewError(f"refs を解けません: {revision}") from error


@dataclass(frozen=True)
class RangeRefs:
    """範囲の文字列を左右の refs 名に割った値。右側を省いた範囲の head は HEAD。"""

    base: str
    head: str


def split_range(range_text: str) -> RangeRefs:
    base, separator, head = range_text.partition("..")
    if not separator or not base or head.startswith("."):
        raise ReviewError(f"範囲は <base>..<head> の形で指定します: {range_text!r}")
    return RangeRefs(base, head or "HEAD")


def default_range_name(head: str, head_sha: str) -> str:
    """名前を省いたときの既定。head の refs 名から作り、HEAD や作れない名前はコミットの短縮形。"""
    candidate = _RANGE_NAME_UNSAFE.sub("-", head).strip("-._")
    if head == "HEAD" or not candidate:
        return head_sha[:8]
    return candidate


def _commits_text(root: Path, merge_base: str, head_sha: str) -> tuple[str, bool]:
    """(コミットの題と本文を並べた文, 本文を持つコミットがあるか)。"""
    output = _git(
        root,
        "log",
        "--reverse",
        f"--format={_COMMIT_START}%s{_COMMIT_FIELD}%b",
        f"{merge_base}..{head_sha}",
    )
    entries: list[str] = []
    has_body = False
    for record in output.split(_COMMIT_START)[1:]:
        subject, _, body = record.partition(_COMMIT_FIELD)
        has_body = has_body or bool(body.strip())
        entries.append(f"{subject}\n\n{body.strip()}" if body.strip() else subject)
    return "\n\n".join(entries), has_body


@dataclass(frozen=True)
class ResolvedRange:
    """範囲の文字列を解いた値。作成と refresh が同じ値を受け渡す。"""

    base_sha: str
    head_sha: str
    merge_base: str
    # 範囲の head の refs 名。既定の名前に使う
    head_name: str


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
    range_text: str,
    resolved: ResolvedRange,
    intent: Declaration | None,
    *,
    stored_directory: Path,
) -> ReviewContent:
    base_tree = directory / BASE_TREE
    head_tree = directory / HEAD_TREE
    merge_base = resolved.merge_base
    head_sha = resolved.head_sha
    source, pairs, config = _build_diff_tree(
        root,
        directory,
        resolved.base_sha,
        head_sha,
        merge_base,
        stored_directory=stored_directory,
    )
    declaration = _range_declaration(root, resolved, intent)
    judgment_map, diff_warnings = _write_diff_materials(
        directory, base_tree, head_tree, pairs, config, touches=()
    )
    warnings = diff_warnings
    background = collect_range_background(root, base_tree, merge_base, pairs)
    return ReviewContent(
        tuple(pair.new_path for pair in pairs if pair.new_path is not None),
        declaration,
        tuple(warnings),
        judgment_map,
        background=background,
        origin=RangeOrigin(LocalRange(range_text, str(root)), source),
    )


def resolve_range(root: Path, range_text: str) -> ResolvedRange:
    """範囲の文字列を解く。作るときも解き直すときも使う。

    refs が解けない、共通の祖先が無い、範囲に変更が無いときは ReviewError。
    """
    refs = split_range(range_text)
    base_sha = _resolve_commit(root, refs.base)
    head_sha = _resolve_commit(root, refs.head)
    try:
        merge_base = _git(root, "merge-base", base_sha, head_sha).strip()
    except process.CommandError as error:
        raise ReviewError(f"共通の祖先がありません: {range_text}") from error
    if not _git(root, "diff", "--name-only", merge_base, head_sha).strip():
        raise ReviewError(f"範囲に変更がありません: {range_text}")
    return ResolvedRange(base_sha, head_sha, merge_base, refs.head)


def create_range_review(
    working_directory: Path,
    range_text: str,
    name: str | None = None,
    intent: str | None = None,
    intent_file: Path | None = None,
) -> Review:
    declared = read_intent(working_directory, intent, intent_file)
    repository = identify(working_directory)
    root = repository.root
    resolved = resolve_range(root, range_text)
    resolved_name = (
        name if name is not None else default_range_name(resolved.head_name, resolved.head_sha)
    )
    review_id = _new_review_id(repository.owner, repository.repo, resolved_name, ReviewKind.RANGE)
    directory = review_id.directory()
    with _staging_directory(review_id) as staging:
        content = _build_range_content(
            root, staging, range_text, resolved, declared, stored_directory=directory
        )
        review, _ = save_review(
            review_id, content, _now(), None, str(directory / HEAD_TREE), staging=staging
        )
    return review


@dataclass
class RefreshResult:
    review: Review
    updated: bool
    region_changes: RegionChanges

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated": self.updated,
            "regions": self.region_changes.to_dict(),
            "review": self.review.to_dict(),
        }


def _refresh_diff_review(
    review: Review,
    source: DiffSource,
    head_sha: str,
    merge_base: str,
    declare: Callable[[], RefreshResult],
    *,
    build_content: Callable[[], Content],
) -> RefreshResult:
    """差分レビューの refresh の骨格。出所ごとの差は declare と build_content が持つ。

    declare は、ツリーが同じときに宣言などの変更を保存して返す。build_content は、
    ツリーが変わったときに全体を作り直す内容を組み立てる。
    """
    if head_sha == source.head_sha and merge_base == source.merge_base:
        return _rebuild_stored_map(declare(), source)
    updated, region_changes = save_review(
        review.id, build_content(), review.created_at, _now(), review.repository_root
    )
    return RefreshResult(updated, updated=True, region_changes=region_changes)


def refresh_pull_request(review: Review) -> RefreshResult:
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
        metadata.head_sha,
        merge_base,
        declare,
        build_content=lambda: _build_pull_request_content(
            review.id.directory(), git_dir, metadata, merge_base, review.id.directory()
        ),
    )


def refresh_range(
    review: Review, declaration_override: Declaration | None = None
) -> RefreshResult:
    origin = review.origin
    if not isinstance(origin, RangeOrigin):
        raise ReviewError(f"範囲の出所でない記録を範囲として refresh できません: {review.id}")
    local_range = origin.range
    source = origin.diff
    root = Path(local_range.working_directory)
    rebuild_advice = (
        f"作り直すには delete してから new range してください: {review.id}"
        f"（保存した作業ディレクトリ: {local_range.working_directory!r}）"
    )
    if not local_range.working_directory or not root.is_dir():
        raise ReviewError(f"範囲を解き直す作業ディレクトリがありません。{rebuild_advice}")
    try:
        _git(root, "rev-parse", "--git-dir")
    except process.CommandError as error:
        raise ReviewError(
            f"作業ディレクトリが git のリポジトリではありません。{rebuild_advice}"
        ) from error
    resolved = resolve_range(root, local_range.range_text)
    kept = _kept_declaration(review, declaration_override)

    def declare() -> RefreshResult:
        declaration = _range_declaration(root, resolved, kept)
        if declaration == review.declaration:
            return RefreshResult(review, updated=False, region_changes=RegionChanges([], [], []))
        updated = replace(review, declaration=declaration, refreshed_at=_now())
        _write_review_file(review.id.directory(), updated)
        return RefreshResult(updated, updated=True, region_changes=RegionChanges([], [], []))

    return _refresh_diff_review(
        review,
        source,
        resolved.head_sha,
        resolved.merge_base,
        declare,
        build_content=lambda: _build_range_content(
            root,
            review.id.directory(),
            local_range.range_text,
            resolved,
            kept,
            stored_directory=review.id.directory(),
        ),
    )


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
    judgment_map = build_judgment_map(
        base_tree, head_tree, load_pairs(review.id), config, bot_analysis.touches()
    )
    gates = update_gates(
        directory,
        head_tree,
        review.paths,
        judgment_map.segments,
        load_catalog(),
        base_root=base_tree,
        renames=judgment_map.renames,
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
        review.id, content, review.created_at, _now(), review.repository_root
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
        refreshed_at=_now(),
        background=background,
    )
    _write_review_file(review.id.directory(), updated)
    save_background(review.id.directory(), background)
    return RefreshResult(updated, updated=True, region_changes=unchanged)


def refresh_from_content(
    review: Review, build_content: Callable[[], ReviewContent | DesignDocContent]
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
    )
    if review.origin == content.origin and _same_content(
        review,
        content.paths,
        content.declaration,
        content.content_warnings,
        map_kind_of(content),
        gates=gates,
    ):
        return RefreshResult(review, updated=False, region_changes=RegionChanges([], [], []))
    updated, region_changes = save_review(
        review.id, content, review.created_at, _now(), review.repository_root
    )
    return RefreshResult(updated, updated=True, region_changes=region_changes)


def refresh_feature(
    review: Review, declaration_override: Declaration | None = None
) -> RefreshResult:
    origin = review.origin
    if not isinstance(origin, FeatureOrigin):
        raise ReviewError(f"機能の出所でない記録を機能として refresh できません: {review.id}")
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
        ),
    )


@dataclass(frozen=True)
class LocatedReview:
    """置き場から読んだレビューと、読んだ置き場の位置。

    写した置き場では、位置は識別子が指す場所（review.id.directory()）と違う。
    写しの置き場を扱う関数はこの値を受け、識別子から置き場を導く関数には渡さない。
    """

    review: Review
    directory: Path


def read_review_at(directory: Path) -> LocatedReview:
    """保存先のディレクトリからレビューを読む。保存先と識別子の一致は求めない。

    置き場を別の場所へ写した複製（保存先の名前が識別子の指す場所と違う）を読むための口で、
    読むだけの検査（JSON と必須項目、識別子の形）を行う。失敗は ReviewError だけで返す。
    本物の置き場の検査は load_review_at が、この上に重ねる。
    """
    try:
        meta = load_review_meta(json.loads((directory / REVIEW_FILE).read_text(encoding="utf-8")))
        review_id = _parse_stored_id(meta)
        map_kind = load_map(json.loads((directory / MAP_FILE).read_text(encoding="utf-8")))
        diff_source, pull_request = read_stored_sources(meta["pull_request"], meta["diff_source"])
        try:
            origin = _origin_from_stored(
                review_id.kind,
                pull_request,
                read_stored_local_range(meta["local_range"]),
                diff_source,
                meta["targets"],
                design_doc=meta["design_doc"],
            )
        except ReviewError as error:
            # 固有のデータと diff_source の組が合わず、保存した出所の種類が決まらない
            raise ReviewKindMismatchError(
                f"保存した出所が食い違います: {review_id}: {error}"
            ) from error
        review = Review(
            review_id,
            meta["created_at"],
            meta["repository_root"],
            meta["paths"],
            Declaration.from_dict(meta["declaration"]),
            meta["content_warnings"],
            map_kind,
            origin,
            meta["refreshed_at"],
            load_background(directory),
            load_gates(directory),
        )
        return LocatedReview(review, directory)
    except FileNotFoundError as error:
        raise ReviewError(f"レビューがありません: {directory}") from error
    except (ValueError, KeyError, TypeError) as error:
        # JSON の構文、識別子の形、必須項目の欠け、項目の型。いずれも保存した記録が壊れている
        raise ReviewError(f"保存したレビューを読めません: {directory}: {error!r}") from error


def set_repository_root(located: LocatedReview, root: str) -> None:
    """置き場の review.json の repository_root だけを書き換える。

    写した置き場の木の付け替えに使う。書き換えには置き場を読める必要があるので、
    保存先の一致は求めない read_review_at から読んだ値を受ける。
    """
    located.review.repository_root = root
    _write_review_file(located.directory, located.review)


def load_review_at(directory: Path) -> Review:
    """保存先のディレクトリからレビューを読む。記録を Review にするまでの検査はここに集める。

    read_review_at の検査に、保存先との一致と出所の種類の一致を足す。
    失敗は ReviewError（とその派生）だけで返す。呼ぶ側は ReviewError を捕まえれば足りる。
    """
    review = read_review_at(directory).review
    review_id = review.id
    if review_id.directory() != directory:
        # 種類は保存した記録の id が決め、保存先の名前だけでは決めない
        # （数字だけの機能の名前は PR の番号と同じ名前になる）。
        # id が指す場所と保存先が違う記録は、どちらが正しいか決められない
        raise ReviewError(f"保存先が識別子（{review_id}）の指す場所と違います: {directory}")
    stored_kind = review.kind
    if stored_kind != review_id.kind:
        raise ReviewKindMismatchError(
            f"識別子の種類（{review_id.kind}）と保存した出所（{stored_kind}）が"
            f"食い違います: {review_id}"
        )
    return review


def _parse_stored_id(meta: dict[str, Any]) -> ReviewId:
    """review.json の id を識別子にする。読めなければ ValueError / KeyError / TypeError。"""
    return ReviewId.parse(meta["id"])


def check_stored_id(review_id: ReviewId) -> None:
    """識別子が指す保存先の記録の id が、その識別子と食い違わないことを確かめる。

    識別子から記録へ進む入口（load_review と resolve_review）が必ず通る。
    同じ保存先を別の種類の識別子で指している場合（数字だけの機能と PR の番号）を、ここで止める。
    """
    try:
        meta = load_review_meta(
            json.loads((review_id.directory() / REVIEW_FILE).read_text(encoding="utf-8"))
        )
        stored = _parse_stored_id(meta)
    except FileNotFoundError as error:
        raise ReviewError(f"レビューがありません: {review_id}") from error
    except (ValueError, KeyError, TypeError, ReviewError):
        # 壊れた記録（JSON、版、id の欠けや形の誤り）は食い違いを判定できないので通す。
        # ここで止めると、壊れた記録を完全な識別子で delete する復旧の道が塞がる。
        # 読む側の load_review_at が別に止める
        return
    if stored != review_id:
        raise ReviewKindMismatchError(
            f"識別子の種類（{review_id.kind}）と保存した記録の識別子（{stored}）が"
            f"食い違います: {review_id}"
        )


def load_review(review_id: ReviewId) -> Review:
    check_stored_id(review_id)
    return load_review_at(review_id.directory())


def _upgrade_pairs_v1(raw: list[Any]) -> dict[str, Any]:
    """版 1 の pairs.json（対の配列そのもの）を次の版へ移す。"""
    return {"pairs": copy.deepcopy(raw)}


# 保存された版から次の版への移し。read_pairs が PAIRS_FORMAT まで順に回す
_PAIRS_UPGRADES: dict[int, Upgrade] = {1: _upgrade_pairs_v1}


def read_pairs(raw: object) -> list[FilePair]:
    """pairs.json を読む境の一か所。版 1 は最上位が配列で、版 2 からは format を持つ辞書。"""
    if not isinstance(raw, dict | list):
        raise ReviewError("pairs.json の最上位が配列でも辞書でもありません")
    if isinstance(raw, dict):
        stored = upgrade_mapping(PAIRS_FILE, raw, current=PAIRS_FORMAT, upgrades=_PAIRS_UPGRADES)
    else:
        # 配列は format を持てないので版 1。upgrade_mapping は辞書しか受けない
        stored = upgrade_to_current(PAIRS_FILE, 1, raw, PAIRS_FORMAT, _PAIRS_UPGRADES)
    try:
        return [FilePair(old, new) for old, new in stored["pairs"]]
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewError(
            f"pairs.json の対が [変更前, 変更後] の組ではありません: {error!r}"
        ) from error


def dump_pairs(pairs: Sequence[FilePair]) -> dict[str, Any]:
    """pairs.json へ書く境の一か所。配列は format を持てないので、辞書に包んで今の版を刻む。"""
    return {"format": PAIRS_FORMAT, "pairs": [[pair.old_path, pair.new_path] for pair in pairs]}


def load_pairs(review_id: ReviewId) -> list[FilePair]:
    """差分レビュー式の、変更したファイルの対。"""
    path = review_id.directory() / PAIRS_FILE
    if not path.is_file():
        raise ReviewError(
            "ファイルの対が保存されていない古いレビューです。"
            f"refresh で作り直してください: {review_id}"
        )
    return read_pairs(json.loads(path.read_text(encoding="utf-8")))


def scope_files(review: Review) -> set[str]:
    """レビューの範囲のファイル。差分レビュー式は変更したファイル（削除も含む）、機能レビュー式は渡したファイル群。"""
    if not review.is_diff:
        return set(review.paths)
    try:
        pairs = load_pairs(review.id)
    except ReviewError:
        # ファイルの対を保存する前の古いレビュー。paths が変更したファイルの新しい側を持つ
        return set(review.paths)
    return {path for pair in pairs for path in (pair.old_path, pair.new_path) if path is not None}


def is_archived(review_id: ReviewId) -> bool:
    return (review_id.directory() / ARCHIVE_MARKER_FILE).is_file()


def set_archived(review_id: ReviewId, *, archived: bool) -> bool:
    """アーカイブの印を付ける、または外す。すでにその状態なら何もしない。変えたかを返す。"""
    directory = review_id.directory()
    with review_lock(directory):
        if is_archived(review_id) == archived:
            return False
        if archived:
            write_text_atomic(directory / ARCHIVE_MARKER_FILE, _now() + "\n")
        else:
            (directory / ARCHIVE_MARKER_FILE).unlink()
        return True


def list_reviews(archived: str = ARCHIVED_EXCLUDE) -> list[Review]:
    """archived は EXCLUDE（隠す）、ONLY（アーカイブ済みだけ）、INCLUDE（全部）。"""
    reviews: list[Review] = []
    home = data_home()
    for review_file in sorted(home.glob(f"*/*/*/{REVIEW_FILE}")):
        directory = review_file.parent
        relative = review_file.relative_to(home)
        if relative.parts[0] == TRASH_DIRECTORY or relative.parts[2].startswith(STAGING_PREFIX):
            continue
        marked = (directory / ARCHIVE_MARKER_FILE).is_file()
        if archived != ARCHIVED_INCLUDE and marked != (archived == ARCHIVED_ONLY):
            continue
        try:
            reviews.append(load_review_at(directory))
        except ReviewError as error:
            # 壊れた記録 1 件で一覧の全体が出なくなるのを避ける。隠したままにはせず警告する
            warnings.warn(f"一覧から除きました: {error}", stacklevel=2)
    return sorted(reviews, key=lambda review: review.created_at, reverse=True)


def _short_matches(known: list[ReviewId], repo: str, kind: ReviewKind, key: str) -> list[ReviewId]:
    return [
        candidate
        for candidate in known
        if candidate.repo == repo and candidate.key == key and candidate.kind == kind
    ]


def _stored_ids_named(name: str, repository: Repository | None = None) -> list[ReviewId]:
    """置き場のディレクトリ名が name に当たるレビューの識別子を、アーカイブ済みも含めて集める。

    repository を渡すと、その owner/repo の下だけを探す。渡さなければ全リポジトリを探す。

    置き場の全件は読まない。名前の合うディレクトリの review.json から id だけを読む。
    読めない記録は候補にしない（壊れた記録の扱いは list_reviews と delete が持つ）。
    """
    home = data_home()
    found: list[ReviewId] = []
    scope = "*/*" if repository is None else glob.escape(f"{repository.owner}/{repository.repo}")
    for prefix in sorted({kind.directory_prefix for kind in ReviewKind}):
        pattern = f"{scope}/{glob.escape(prefix + name)}/{REVIEW_FILE}"
        for review_file in sorted(home.glob(pattern)):
            if review_file.relative_to(home).parts[0] == TRASH_DIRECTORY:
                continue
            try:
                found.append(
                    _parse_stored_id(
                        load_review_meta(json.loads(review_file.read_text(encoding="utf-8")))
                    )
                )
            except (OSError, ValueError, KeyError, TypeError, ReviewError):
                # 読めない記録は id を取れない。候補を欠いても誤りの文は出せる
                continue
    return [review_id for review_id in found if review_id.key == name]


def _name_only_message(text: str, repository: Repository) -> str:
    """名前だけが渡され、呼び出した場所のリポジトリに無いときの誤りの文。候補は示すだけで、選びはしない。"""
    candidates = _stored_ids_named(text)
    where = f"{repository.owner}/{repository.repo}"
    head = f"このリポジトリ（{where}）に名前 {text!r} のレビューはありません。"
    if not candidates:
        # 区切りはレビューの種類ごとに違うので、例を一つに決めず一覧の形へ案内する
        return f"{head}jigane list に出る完全な識別子か短い識別子で指してください"
    listed = "、".join(
        f"{candidate}（短い形: {candidate.repo}{candidate.kind.separator}{candidate.key}）"
        for candidate in candidates
    )
    return f"{head}候補: {listed}。jigane list で一覧を確かめられます"


def _resolve_name_only(text: str, working_directory: Path) -> ReviewId:
    """名前だけの入力を、呼び出した場所のリポジトリの中で一つに決める。決まらなければ拒む。"""
    repository = identify(working_directory)
    here = _stored_ids_named(text, repository)
    if len(here) == 1:
        return here[0]
    if len(here) > 1:
        listed = ", ".join(str(candidate) for candidate in here)
        raise ReviewError(f"名前が曖昧です: {text}（候補: {listed}）")
    # 呼び出した場所のリポジトリに無いときは、全リポジトリの候補を示して完全な形へ案内する
    raise ReviewError(_name_only_message(text, repository))


def resolve_review(text: str, working_directory: Path | None = None) -> ReviewId:
    """識別子を決める。名前だけの入力は working_directory（省くと現在地）のリポジトリで探す。"""
    try:
        full = ReviewId.parse(text)
    except ValueError as error:
        if "/" in text:
            # owner を含む形は完全な識別子として書かれている。短い識別子の誤りに読み替えない
            raise ReviewError(str(error)) from error
        full = None
    if full is not None:
        check_stored_id(full)
        return full
    try:
        repo, kind, key = ReviewId.parse_short(text)
    except ValueError as error:
        # どれかの種類の名前の規則に合う文字列だけを、`jigane new` で付けた名前と見なす
        if any(named.key_pattern.fullmatch(text) for named in ReviewKind):
            return _resolve_name_only(text, working_directory or Path.cwd())
        raise ReviewError(str(error)) from error
    # 短い識別子は、アーカイブしていないレビューを先に探す。無いときだけアーカイブ済みへ広げる。
    # 置き場の全件を読むのは、短い形と分かってからにする
    known = [review.id for review in list_reviews()]
    matches = _short_matches(known, repo, kind, key)
    if not matches:
        matches = _short_matches(
            [review.id for review in list_reviews(ARCHIVED_ONLY)], repo, kind, key
        )
    if not matches:
        raise ReviewError(f"レビューがありません: {text}")
    if len(matches) > 1:
        listed = ", ".join(str(match) for match in matches)
        raise ReviewError(f"短い識別子が曖昧です: {text}（候補: {listed}）")
    return matches[0]
