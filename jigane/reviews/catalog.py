"""保存済みのレビューの読み出しと一覧を持つ。名前からの解決は resolution が持つ。"""

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jigane.flaky.gates import load_gates
from jigane.judgment_map.declaration import Declaration
from jigane.judgment_map.map_kind import MAP_FILE, load_map
from jigane.pull_request_context.background import load_background
from jigane.reviews.lifecycle import (
    Review,
    ReviewKindMismatchError,
    now,
    origin_from_stored,
    read_stored_local_range,
    read_stored_sources,
    write_review_file,
)
from jigane.reviews.review_formats import load_review_meta
from jigane.reviews.stored_pairs import load_pairs
from jigane.storage.atomic_write import write_text_atomic
from jigane.storage.review_access import locked_review, stored_file_exists
from jigane.storage.review_error import ReviewError
from jigane.storage.store import (
    REVIEW_FILE,
    STAGING_PREFIX,
    CheckedReviewId,
    ReviewId,
    check_review_id,
    data_home,
)

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


def read_review_at(directory: Path) -> LocatedReview:
    """保存先のディレクトリからレビューを読む。保存先と識別子の一致は求めない。

    置き場を別の場所へ写した複製（保存先の名前が識別子の指す場所と違う）を読むための口で、
    読むだけの検査（JSON と必須項目、識別子の形）を行う。失敗は ReviewError だけで返す。
    本物の置き場の検査は load_review_at が、この上に重ねる。
    """
    try:
        meta = load_review_meta(json.loads((directory / REVIEW_FILE).read_text(encoding="utf-8")))
        review_id = parse_stored_id(meta)
        map_kind = load_map(json.loads((directory / MAP_FILE).read_text(encoding="utf-8")))
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


def load_review_at(directory: Path) -> Review:
    """保存先のディレクトリからレビューを読む。記録を Review にするまでの検査はここに集める。

    read_review_at の検査に、保存先との一致と出所の種類の一致を足す。
    失敗は ReviewError（とその派生）だけで返す。呼ぶ側は ReviewError を捕まえれば足りる。
    """
    review = read_review_at(directory).review
    review_id = review.id
    if review_id.directory() != directory:
        # 種類は保存した記録の id が決め、保存先の名前だけでは決めない
        # （数字だけのファイルのレビューの名前は PR の番号と同じ名前になる）。
        # id が指す場所と保存先が違う記録は、どちらが正しいか決められない
        raise ReviewError(f"保存先が識別子（{review_id}）の指す場所と違います: {directory}")
    stored_kind = review.kind
    if stored_kind != review_id.kind:
        raise ReviewKindMismatchError(
            f"識別子の種類（{review_id.kind}）と保存した出所（{stored_kind}）が"
            f"食い違います: {review_id}"
        )
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
        meta = load_review_meta(json.loads((directory / REVIEW_FILE).read_text(encoding="utf-8")))
        stored = parse_stored_id(meta)
    except FileNotFoundError as error:
        raise ReviewError(f"レビューがありません: {review_id}") from error
    except (ValueError, KeyError, TypeError, ReviewError):
        # 壊れた記録（JSON、版、id の欠けや形の誤り）は食い違いを判定できないので通す。
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
    except ReviewError:
        # ファイルの対を保存する前の古いレビュー。paths が変更したファイルの新しい側を持つ
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
