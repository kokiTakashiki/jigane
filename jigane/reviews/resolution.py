"""レビューの識別子の入力（完全な形、短い形、名前だけ）を、保存済みのレビューの一つに決める。"""

import glob
import json
from collections.abc import Sequence
from pathlib import Path

from jigane.git.repository import Repository, identify
from jigane.reviews.catalog import (
    ARCHIVED_ONLY,
    TRASH_DIRECTORY,
    check_stored_id,
    list_reviews,
    parse_stored_id,
)
from jigane.reviews.review_formats import load_review_meta
from jigane.storage.review_error import ReviewError
from jigane.storage.store import (
    REVIEW_FILE,
    CheckedReviewId,
    ReviewId,
    ReviewKind,
    check_review_id,
    data_home,
)


def _short_matches(
    known: Sequence[ReviewId], repo: str, kind: ReviewKind, key: str
) -> list[ReviewId]:
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
                    parse_stored_id(
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


def _resolve_name_only(text: str, working_directory: Path) -> CheckedReviewId:
    """名前だけの入力を、呼び出した場所のリポジトリの中で一つに決める。決まらなければ拒む。"""
    repository = identify(working_directory)
    here = _stored_ids_named(text, repository)
    if len(here) == 1:
        return check_review_id(here[0])
    if len(here) > 1:
        listed = ", ".join(str(candidate) for candidate in here)
        raise ReviewError(f"名前が曖昧です: {text}（候補: {listed}）")
    # 呼び出した場所のリポジトリに無いときは、全リポジトリの候補を示して完全な形へ案内する
    raise ReviewError(_name_only_message(text, repository))


def resolve_review(text: str, working_directory: Path | None = None) -> CheckedReviewId:
    """識別子を決める。名前だけの入力は working_directory（省くと現在地）のリポジトリで探す。"""
    try:
        full = ReviewId.parse(text)
    except ValueError as error:
        if "/" in text:
            # owner を含む形は完全な識別子として書かれている。短い識別子の誤りに読み替えない
            raise ReviewError(str(error)) from error
        full = None
    if full is not None:
        return check_stored_id(full)
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
    return check_review_id(matches[0])
