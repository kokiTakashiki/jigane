"""保存済みのレビューの読み出しと一覧を持つ。名前からの解決は resolution が持つ。"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from jigane.flaky.gates import load_gates
from jigane.judgment_map.declaration import Declaration
from jigane.judgment_map.map_kind import MAP_FILE, load_map, read_line_totals
from jigane.pull_request_context.background import load_background
from jigane.reviews.kind_fields import kind_fields_of
from jigane.reviews.lifecycle import (
    Review,
    ReviewKindMismatchError,
    ReviewOrigin,
    now,
    origin_from_stored,
    origin_kind,
    read_stored_local_range,
    read_stored_sources,
    write_review_file,
)
from jigane.reviews.review_formats import load_review_meta
from jigane.reviews.stored_pairs import PairsNotSavedError, load_pairs
from jigane.storage.atomic_write import write_text_atomic
from jigane.storage.review_access import locked_review, read_stored_file, stored_file_exists
from jigane.storage.review_error import ReviewError
from jigane.storage.store import (
    REVIEW_FILE,
    STAGING_PREFIX,
    CheckedReviewId,
    ReviewId,
    ReviewKind,
    check_review_id,
    data_home,
    review_name_of_directory,
)
from jigane.storage.symlink_guard import (
    StoredFileNotRegularError,
    StoredFileSymlinkError,
    is_link_like,
)

LOGGER = logging.getLogger(__name__)

# 削除が中身を退ける置き場の名前。データの置き場の直下に作る。一覧には出さない
TRASH_DIRECTORY = ".trash"
# アーカイブの印。レビューの中身とは分けるので、refresh は触れない。中身は印を付けた時刻
ARCHIVE_MARKER_FILE = "archived"
ARCHIVED_EXCLUDE = "exclude"
ARCHIVED_ONLY = "only"
ARCHIVED_INCLUDE = "include"


@dataclass(frozen=True)
class LocatedReview:
    """置き場から読んだレビューと、読んだ置き場の位置。

    写した置き場では、位置は識別子が指す場所（review.id.directory()）と違う。
    写しの置き場を扱う関数はこの値を受け、識別子から置き場を導く関数には渡さない。
    """

    review: Review
    directory: Path


class _StoredHead(NamedTuple):
    meta: dict[str, Any]
    review_id: ReviewId
    origin: ReviewOrigin


def _read_meta_and_origin(directory: Path) -> _StoredHead:
    """review.json を読み、識別子と出所にする。read_review_at と一覧の軽い読みが共通に通る。

    ValueError / KeyError / TypeError と ReviewKindMismatchError は呼ぶ側が受ける。
    """
    meta = load_review_meta(json.loads(read_stored_file(directory, REVIEW_FILE)))
    review_id = parse_stored_id(meta)
    diff_source, pull_request = read_stored_sources(meta["pull_request"], meta["diff_source"])
    try:
        origin = origin_from_stored(
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
    return _StoredHead(meta, review_id, origin)


def read_review_at(directory: Path) -> LocatedReview:
    """保存先のディレクトリからレビューを読む。保存先と識別子の一致は求めない。

    置き場を別の場所へ写した複製（保存先の名前が識別子の指す場所と違う）を読むための口で、
    読むだけの検査（JSON と必須項目、識別子の形）を行う。失敗は ReviewError だけで返す。
    本物の置き場の検査は load_review_at が、この上に重ねる。
    """
    try:
        meta, review_id, origin = _read_meta_and_origin(directory)
        map_kind = load_map(json.loads(read_stored_file(directory, MAP_FILE)))
        review = Review(
            check_review_id(review_id),
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
    except StoredFileSymlinkError as error:
        raise ReviewError(f"保存したレビューのファイルが symlink です: {error}") from error
    except StoredFileNotRegularError as error:
        raise ReviewError(f"保存したレビューのファイルが通常でない: {error}") from error
    except (ValueError, KeyError, TypeError) as error:
        # JSON の構文、識別子の形、必須項目の欠け、項目の型。いずれも保存した記録が壊れている
        raise ReviewError(f"保存したレビューを読めません: {directory}: {error!r}") from error


def set_repository_root(located: LocatedReview, root: str) -> None:
    """置き場の review.json の repository_root だけを書き換える。

    写した置き場の木の付け替えに使う。書き換えには置き場を読める必要があるので、
    保存先の一致は求めない read_review_at から読んだ値を受ける。
    """
    located.review.repository_root = root
    write_review_file(located.directory, located.review)


def _check_stored_place(
    review_id: CheckedReviewId, stored_kind: ReviewKind, directory: Path
) -> None:
    """保存先と識別子、識別子の種類と保存した出所の種類が合うことを確かめる。"""
    if review_id.directory() != directory:
        # 種類は保存した記録の id が決め、保存先の名前だけでは決めない
        # （数字だけのファイルのレビューの名前は PR の番号と同じ名前になる）。
        # id が指す場所と保存先が違う記録は、どちらが正しいか決められない
        raise ReviewError(f"保存先が識別子（{review_id}）の指す場所と違います: {directory}")
    if stored_kind != review_id.kind:
        raise ReviewKindMismatchError(
            f"識別子の種類（{review_id.kind}）と保存した出所（{stored_kind}）が"
            f"食い違います: {review_id}"
        )


def load_review_at(directory: Path) -> Review:
    """保存先のディレクトリからレビューを読む。記録を Review にするまでの検査はここに集める。

    read_review_at の検査に、保存先との一致と出所の種類の一致を足す。
    失敗は ReviewError（とその派生）だけで返す。呼ぶ側は ReviewError を捕まえれば足りる。
    """
    review = read_review_at(directory).review
    _check_stored_place(review.id, review.kind, directory)
    return review


def parse_stored_id(meta: dict[str, Any]) -> ReviewId:
    """review.json の id を識別子にする。読めなければ ValueError / KeyError / TypeError。"""
    return ReviewId.parse(meta["id"])


def check_stored_id(review_id: ReviewId) -> CheckedReviewId:
    """識別子が指す保存先の記録の id が、その識別子と食い違わないことを確かめる。

    識別子から記録へ進む入口（load_review と resolve_review）が必ず通る。
    同じ保存先を別の種類の識別子で指している場合
    （数字だけのファイルのレビューと PR の番号）を、ここで止める。
    """
    checked = check_review_id(review_id)
    directory = checked.directory()
    try:
        meta = load_review_meta(json.loads(read_stored_file(directory, REVIEW_FILE)))
        stored = parse_stored_id(meta)
    except FileNotFoundError as error:
        raise ReviewError(f"レビューがありません: {review_id}") from error
    except (
        ValueError,
        KeyError,
        TypeError,
        ReviewError,
        StoredFileSymlinkError,
        StoredFileNotRegularError,
    ):
        # 壊れた記録（JSON、版、id の欠けや形の誤り、symlink や FIFO の review.json）は
        # 食い違いを判定できないので通す。
        # ここで止めると、壊れた記録を完全な識別子で delete する復旧の道が塞がる。
        # 読む側の load_review_at が別に止める
        return checked
    if stored != review_id:
        raise ReviewKindMismatchError(
            f"識別子の種類（{review_id.kind}）と保存した記録の識別子（{stored}）が"
            f"食い違います: {review_id}"
        )
    return checked


def load_review(review_id: CheckedReviewId) -> Review:
    check_stored_id(review_id)
    return load_review_at(review_id.directory())


def scope_files(review: Review) -> set[str]:
    """レビューの範囲のファイル。差分のレビューは変更したファイル（削除も含む）、ファイルのレビューは渡したファイル群。"""
    if not review.is_diff:
        return set(review.paths)
    try:
        pairs = load_pairs(review.id)
    except PairsNotSavedError:
        # pairs.json が無いときだけ paths に切り替える（ファイルの対を保存する前の古いレビュー。
        # paths が変更したファイルの新しい側を持つ）。古い版、新しすぎる版、壊れた版は
        # 読めない保存物なので例外のまま伝える。以前はどれも paths に落としていた
        return set(review.paths)
    return {path for pair in pairs for path in (pair.old_path, pair.new_path) if path is not None}


def is_archived(review_id: CheckedReviewId) -> bool:
    return stored_file_exists(review_id, ARCHIVE_MARKER_FILE)


def set_archived(review_id: CheckedReviewId, *, archived: bool) -> bool:
    """アーカイブの印を付ける、または外す。すでにその状態なら何もしない。変えたかを返す。"""
    with locked_review(review_id) as directory:
        if is_archived(review_id) == archived:
            return False
        if archived:
            write_text_atomic(directory / ARCHIVE_MARKER_FILE, now() + "\n")
        else:
            (directory / ARCHIVE_MARKER_FILE).unlink()
        return True


@dataclass(frozen=True)
class ReviewSummary:
    """一覧が使う項目だけを持つレビューの要約。区間と箇所の地図、背景、ゲートは持たない。"""

    id: CheckedReviewId
    created_at: str
    repository_root: str
    file_count: int
    origin: ReviewOrigin
    # 判断が要る行と全体の行。map.json の集計から読む
    required_lines: int
    total_lines: int

    @property
    def kind_fields(self) -> dict[str, Any]:
        return kind_fields_of(origin_kind(self.origin))


def _read_review_summary(directory: Path) -> ReviewSummary:
    """一覧のために、レビューの要約の行だけを軽く読む。検査は load_review_at と同じ入口で行う。

    map.json は集計の項目だけを取り、background.json と gates.json は開かない。
    失敗は ReviewError だけで返す。

    検査は load_review_at より緩い。map.json の本体（区間と箇所）、background.json、gates.json の
    形は検査しない。この差は意図で、一覧が引く項目だけを検査して読みを軽くするためにある。
    """
    try:
        meta, review_id, origin = _read_meta_and_origin(directory)
        checked = check_review_id(review_id)
        _check_stored_place(checked, origin_kind(origin), directory)
        required, total = read_line_totals(json.loads(read_stored_file(directory, MAP_FILE)))
        return ReviewSummary(
            checked,
            meta["created_at"],
            meta["repository_root"],
            len(meta["paths"]),
            origin,
            required,
            total,
        )
    except FileNotFoundError as error:
        raise ReviewError(f"レビューがありません: {directory}") from error
    except StoredFileSymlinkError as error:
        raise ReviewError(f"保存したレビューのファイルが symlink です: {error}") from error
    except StoredFileNotRegularError as error:
        raise ReviewError(f"保存したレビューのファイルが通常でない: {error}") from error
    except (ValueError, KeyError, TypeError) as error:
        raise ReviewError(f"保存したレビューを読めません: {directory}: {error!r}") from error


def _listed_directories(archived: str) -> list[Path]:
    """一覧に出すレビューの保存先。ごみ箱と作成途中は除き、アーカイブの絞り込みを掛ける。"""
    directories: list[Path] = []
    home = data_home()
    for review_file in sorted(home.glob(f"*/*/*/{REVIEW_FILE}")):
        directory = review_file.parent
        relative = review_file.relative_to(home)
        if relative.parts[0] == TRASH_DIRECTORY or relative.parts[2].startswith(STAGING_PREFIX):
            continue
        marker = directory / ARCHIVE_MARKER_FILE
        marked = marker.is_file() and not is_link_like(marker)
        if archived != ARCHIVED_INCLUDE and marked != (archived == ARCHIVED_ONLY):
            continue
        directories.append(directory)
    return directories


# 警告をまとめる間だけ、除いた記録の名前を溜める入れ物。None のときは 1 件ずつ警告する
_SKIPPED_COLLECTOR: ContextVar[set[str] | None] = ContextVar(
    "skipped_review_collector", default=None
)


def _warn_skipped(directory: Path, error: ReviewError) -> None:
    name = review_name_of_directory(directory)
    collector = _SKIPPED_COLLECTOR.get()
    if collector is None:
        LOGGER.warning("一覧から除きました: %s: %s", name, error)
        return
    # 一回の実行で置き場を何度引いても、同じ記録は一度だけ数える
    collector.add(name)


@contextmanager
def collapse_skipped_warnings() -> Iterator[None]:
    """この間に一覧から除いた記録の件数を、抜けるときに 1 行の警告で出す。

    見積もりのように置き場の全件を引く入口で、古い版の記録 1 件ごとに同じ警告が並ぶのを避ける。
    """
    collected: set[str] = set()
    token = _SKIPPED_COLLECTOR.set(collected)
    try:
        yield
    finally:
        _SKIPPED_COLLECTOR.reset(token)
        if collected:
            LOGGER.warning(
                "読めない古い記録が %d 件あります。理由は `jigane list` で 1 件ずつ見られます。",
                len(collected),
            )


@dataclass(frozen=True)
class SummariesWithSkipped:
    """読めた要約（新しい順）と、読めずに除いた記録の数。"""

    summaries: tuple[ReviewSummary, ...]
    skipped: int


def list_review_summaries_counting_skipped(
    archived: str = ARCHIVED_EXCLUDE,
) -> SummariesWithSkipped:
    """list_review_summaries と同じ要約に、読めずに除いた記録の数を添えて返す。"""
    summaries: list[ReviewSummary] = []
    skipped = 0
    for directory in _listed_directories(archived):
        try:
            summaries.append(_read_review_summary(directory))
        except ReviewError as error:
            # 壊れた記録 1 件で一覧の全体が出なくなるのを避ける。隠したままにはせず警告する
            _warn_skipped(directory, error)
            skipped += 1
    ordered = sorted(summaries, key=lambda summary: summary.created_at, reverse=True)
    return SummariesWithSkipped(tuple(ordered), skipped)


def list_review_summaries(archived: str = ARCHIVED_EXCLUDE) -> list[ReviewSummary]:
    """list_reviews と同じ並びと除外で、要約だけを返す。archived の意味も同じ。"""
    return list(list_review_summaries_counting_skipped(archived).summaries)


def list_reviews(archived: str = ARCHIVED_EXCLUDE) -> list[Review]:
    """archived は EXCLUDE（隠す）、ONLY（アーカイブ済みだけ）、INCLUDE（全部）。"""
    reviews: list[Review] = []
    for directory in _listed_directories(archived):
        try:
            reviews.append(load_review_at(directory))
        except ReviewError as error:
            # 壊れた記録 1 件で一覧の全体が出なくなるのを避ける。隠したままにはせず警告する
            _warn_skipped(directory, error)
    return sorted(reviews, key=lambda review: review.created_at, reverse=True)
