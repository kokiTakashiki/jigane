import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from jigane.storage.locking import hold_replaceable_file_lock, remove_file_if_unlocked
from jigane.storage.store import (
    CLAIMS_DIRECTORY,
    REVIEW_FILE,
    STAGING_PREFIX,
    CheckedReviewId,
    ReviewError,
    ReviewExistsError,
    ReviewId,
    check_new_review_id,
    data_home,
    ensure_data_home,
    fold_name,
)


def _claim_lock_path(review_id: ReviewId) -> Path:
    """名前の作成の請求のロックのファイル。置き場の根の下の .claims に、畳んだ名前で置く。

    owner、repo、名前のどれも畳むので、綴り違いの作成も同じ錠に当たる。レビューのディレクトリの
    外に置くのは、レビューの一覧や保存先の中身にロックのファイルを混ぜないため。
    """
    name = f"{review_id.kind.directory_prefix}{review_id.key}"
    return (
        data_home()
        / CLAIMS_DIRECTORY
        / fold_name(review_id.owner)
        / fold_name(review_id.repo)
        / f"claim-{fold_name(name)}"
    )


def _removable_path(path: Path, review_id: CheckedReviewId) -> Path:
    """消してよい場所かを、名前に依らない共通の条件で確かめて、解決した経路を返す。"""
    home = data_home().resolve()
    expected_parent = review_id.directory().parent.resolve()
    if path.is_symlink():
        raise ReviewError(f"シンボリックリンクは消しません: {path}")
    resolved = path.resolve()
    if resolved == home or not resolved.is_relative_to(home):
        raise ReviewError(f"データの置き場の外か置き場そのものは消しません: {path}")
    if resolved.parent != expected_parent:
        raise ReviewError(f"このレビューの置き場の直下ではないので消しません: {path}")
    return resolved


def _remove_staging_directory(path: Path, review_id: CheckedReviewId) -> None:
    """この識別子の保存先の親の下にある、作成途中の置き場だけを消す。"""
    resolved = _removable_path(path, review_id)
    if not resolved.name.startswith(STAGING_PREFIX):
        raise ReviewError(f"消す対象の名前が想定と違います: {path}")
    shutil.rmtree(resolved, ignore_errors=True)


def _remove_leftover_directory(path: Path, review_id: CheckedReviewId) -> None:
    """この識別子の保存先そのものだけを消す。以前の失敗が残した半端なものを置き換える前に使う。"""
    resolved = _removable_path(path, review_id)
    if resolved.name.startswith(STAGING_PREFIX):
        raise ReviewError(f"消す対象の名前が想定と違います: {path}")
    if resolved.name != review_id.directory().name:
        raise ReviewError(f"このレビューの保存先ではないので消しません: {path}")
    shutil.rmtree(resolved, ignore_errors=True)


@contextmanager
def _creation_claim(review_id: ReviewId) -> Iterator[None]:
    """名前の作成の請求を、OS のファイルロックで取る。取れなければ進行中の作成として拒む。

    ロックのファイルは畳んだ名前から作り、綴り違いの作成も同じ錠に当たる。持ち主のプロセスが
    強制終了されれば OS が錠を外すので、残った請求を手で消す必要はない。
    ロックのファイルは解放しても消さない。消すのは remove_claim_lock だけで、
    錠を握ってから消す。
    """
    lock_path = _claim_lock_path(review_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with hold_replaceable_file_lock(lock_path) as held:
        if not held:
            raise ReviewExistsError(
                review_id,
                detail=(
                    "（同じ名前の作成が進行中です。直前にレビューを消した直後の"
                    "一時的な競合のこともあるので、少し待ってからもう一度実行してください）"
                ),
            )
        yield


def remove_claim_lock(review_id: CheckedReviewId) -> bool:
    """レビューを消した後、対応する請求のロックのファイルを消す。消せたら True。

    作成が錠を握っていれば消さず False を返す。残っても作成の動作には影響しない
    （次の作成が同じ錠を使う）。
    """
    return remove_file_if_unlocked(_claim_lock_path(review_id))


def _install_staging(staging: Path, final: Path, review_id: CheckedReviewId) -> None:
    """仮置きを保存先へ置く。請求を握った間に、綴りと既存を確かめて改名する一つの操作。

    請求は OS のファイルロックで、検査と改名は請求の中で行う。
    """
    with _creation_claim(review_id):
        check_new_review_id(review_id)
        if final.exists():
            if (final / REVIEW_FILE).is_file():
                raise ReviewExistsError(review_id)
            # review.json の無い保存先は、以前の失敗が残した半端なもの。混ぜずに消してから置く
            _remove_leftover_directory(final, review_id)
        staging.rename(final)


def _make_staging(final: Path) -> Path:
    ensure_data_home()
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = final.parent / f"{STAGING_PREFIX}{uuid.uuid4().hex}"
    staging.mkdir()
    return staging


@contextmanager
def staging_directory(review_id: CheckedReviewId) -> Iterator[Path]:
    """保存先と同じ親の下の隠しの置き場で組み立て、抜けるときに保存先へ置く。

    失敗したら置き場を消して例外をそのまま上げる。改名が原子的なので、保存先に半端な
    ディレクトリが現れることはない。綴りの検査は入口の new_review_id と、
    請求の中の check_new_review_id で行う。
    """
    final = review_id.directory()
    staging = _make_staging(final)
    try:
        yield staging
        _install_staging(staging, final, review_id)
    except BaseException:
        if staging.exists():
            _remove_staging_directory(staging, review_id)
        raise


class SaveTarget(NamedTuple):
    """保存するときの保存先と、実際に書く先。仮置きを使わなければ同じ場所。"""

    final: Path
    writing: Path


def direct_save_target(review_id: CheckedReviewId) -> SaveTarget:
    """保存先へ直接書く対象。ディレクトリは作らない（prepare_save_target が作る）。"""
    final = review_id.directory()
    return SaveTarget(final, final)


def staged_save_target(review_id: CheckedReviewId, staging: Path) -> SaveTarget:
    """仮置きへ書く対象。仮置きは staging_directory が作っている。"""
    return SaveTarget(review_id.directory(), staging)


def prepare_save_target(target: SaveTarget) -> None:
    """書く前に、置き場の根と書く先のディレクトリが無ければ作る。"""
    ensure_data_home()
    target.writing.mkdir(parents=True, exist_ok=True)
