import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.reviews.catalog import load_review
from jigane.reviews.lifecycle import ReviewError, create_range_review
from jigane.storage import locking
from jigane.storage import review_creation as creation_module
from jigane.storage import store as store_module
from jigane.storage.review_error import ReviewExistsError
from jigane.storage.store import (
    STAGING_PREFIX,
    CheckedReviewId,
    ReviewId,
    ReviewKind,
    check_new_review_id,
    check_review_id,
    open_review_directory,
)


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    return root


def _repository_directory(tmp_path: Path) -> Path:
    return tmp_path / "home" / "octo" / "hello"


def test_create_with_case_folded_equal_name_is_rejected_with_reason(
    repo: Path, tmp_path: Path
) -> None:
    create_range_review(repo, "main..feature", "mine")
    with pytest.raises(ReviewError, match="大文字と小文字だけが違う"):
        create_range_review(repo, "main..feature", "Mine")
    assert [path.name for path in _repository_directory(tmp_path).iterdir()] == ["@mine"]


def test_open_with_different_spelling_is_refused_with_reason(repo: Path) -> None:
    create_range_review(repo, "main..feature", "mine")
    other_spelling = ReviewId("octo", "hello", "Mine", ReviewKind.RANGE)
    with pytest.raises(ReviewError, match="開きません"):
        open_review_directory(other_spelling)
    with pytest.raises(ReviewError, match="開きません"):
        load_review(check_review_id(other_spelling))
    exact = ReviewId("octo", "hello", "mine", ReviewKind.RANGE)
    assert load_review(check_review_id(exact)).id == exact


def test_open_with_different_owner_spelling_is_refused(repo: Path) -> None:
    create_range_review(repo, "main..feature", "mine")
    with pytest.raises(ReviewError, match="開きません"):
        open_review_directory(ReviewId("Octo", "hello", "mine", ReviewKind.RANGE))


_CLAIM_HOLDER = (
    "import os, sys, time\n"
    "from jigane.storage import locking\n"
    "descriptor = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR)\n"
    "assert locking.try_lock(descriptor)\n"
    "print('locked', flush=True)\n"
    "time.sleep(60)\n"
)


def _claim_lock_path() -> Path:
    """review_creation の受け付けの錠のファイル。親のディレクトリも作っておく。"""
    path = creation_module._claim_lock_path(ReviewId("octo", "hello", "mine", ReviewKind.RANGE))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _start_claim_holder(lock_path: Path) -> subprocess.Popen[str]:
    """別のプロセスに、請求のロックを握らせる。握ったことを確かめてから返す。"""
    holder = subprocess.Popen(
        [sys.executable, "-c", _CLAIM_HOLDER, str(lock_path)],
        # 子が import する jigane を、この試験を動かしている親と同じ木に固定する
        env={**os.environ, "PYTHONPATH": str(Path(store_module.__file__).resolve().parents[2])},
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "locked"
    return holder


def _stop(holder: subprocess.Popen[str]) -> None:
    """finally を走らせずに落とす。OS が持ち主の終了で錠を外す。"""
    holder.kill()
    holder.wait()
    assert holder.stdout is not None
    holder.stdout.close()


def test_create_is_refused_with_reason_while_another_process_holds_the_claim(repo: Path) -> None:
    holder = _start_claim_holder(_claim_lock_path())
    try:
        # 綴り違いの名前も、畳んだ名前の同じ錠に当たる
        for name in ("mine", "Mine"):
            with pytest.raises(ReviewExistsError, match="進行中です"):
                create_range_review(repo, "main..feature", name)
    finally:
        _stop(holder)


def test_create_succeeds_after_the_claim_holder_is_killed(repo: Path) -> None:
    holder = _start_claim_holder(_claim_lock_path())
    _stop(holder)
    review = create_range_review(repo, "main..feature", "mine")
    assert (review.id.directory() / "review.json").is_file()


def test_create_is_refused_with_reason_when_the_claim_lock_cannot_be_taken(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(locking, "try_lock", lambda _descriptor: False)
    with pytest.raises(ReviewExistsError, match="進行中です"):
        create_range_review(repo, "main..feature", "mine")


def test_concurrent_creates_of_the_same_name_succeed_once(repo: Path, tmp_path: Path) -> None:
    outcomes: list[str] = []
    failures: list[ReviewError] = []
    barrier = threading.Barrier(2)

    def create(name: str) -> None:
        barrier.wait()
        try:
            create_range_review(repo, "main..feature", name)
        except ReviewError as error:
            failures.append(error)
            outcomes.append("rejected")
        else:
            outcomes.append("created")

    threads = [threading.Thread(target=create, args=(name,)) for name in ("mine", "Mine")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["created", "rejected"]
    (failure,) = failures
    # 負けた側は、請求の衝突（既存）か綴りの衝突（作れない）のどちらかの作成向けの理由で失敗する
    assert isinstance(failure, ReviewExistsError) or "大文字と小文字だけが違う" in str(failure)
    assert "開きません" not in str(failure)
    assert len(list(_repository_directory(tmp_path).glob("@*"))) == 1


def test_competing_create_during_install_fails_on_the_claim(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.rename
    raised: list[ReviewExistsError] = []

    def rename_with_competitor(self: Path, target: Path) -> Path:
        if self.name.startswith(STAGING_PREFIX) and not raised:
            with pytest.raises(ReviewExistsError) as caught:
                create_range_review(repo, "main..feature", "Mine")
            raised.append(caught.value)
        return original(self, target)

    monkeypatch.setattr(Path, "rename", rename_with_competitor)
    create_range_review(repo, "main..feature", "mine")
    (failure,) = raised
    assert "進行中です" in str(failure)
    assert [path.name for path in _repository_directory(tmp_path).iterdir()] == ["@mine"]


def test_twin_appearing_during_create_fails_for_creation_and_leaves_no_staging(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = creation_module._install_staging

    def install_after_twin(staging: Path, final: Path, review_id: CheckedReviewId) -> None:
        (final.parent / "@MINE").mkdir()
        original(staging, final, review_id)

    monkeypatch.setattr(creation_module, "_install_staging", install_after_twin)
    with pytest.raises(ReviewError, match="作れません") as caught:
        create_range_review(repo, "main..feature", "mine")
    assert "開きません" not in str(caught.value)
    assert [path.name for path in _repository_directory(tmp_path).iterdir()] == ["@MINE"]


def _claim_lock_is_free(lock_path: Path) -> bool:
    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        if not locking.try_lock(descriptor):
            return False
        locking.unlock(descriptor)
        return True
    finally:
        os.close(descriptor)


def test_claim_is_released_after_a_successful_create(repo: Path) -> None:
    create_range_review(repo, "main..feature", "mine")
    assert _claim_lock_is_free(_claim_lock_path())


def test_claim_is_released_and_the_original_error_stays_when_the_rename_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.rename

    def failing_rename(self: Path, target: Path) -> Path:
        if self.name.startswith(STAGING_PREFIX):
            raise RuntimeError("改名の失敗")
        return original(self, target)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "rename", failing_rename)
        with pytest.raises(RuntimeError, match="改名の失敗"):
            create_range_review(repo, "main..feature", "mine")
    assert _claim_lock_is_free(_claim_lock_path())
    assert create_range_review(repo, "main..feature", "mine").id.key == "mine"


_READING_SUBCOMMANDS = [
    ["show"],
    ["archive"],
    ["unarchive"],
    ["delete"],
    ["refresh"],
    ["pick", "{id}", "1"],
    ["unpick", "{id}", "1"],
    ["hold", "{id}", "1"],
    ["unhold", "{id}", "1"],
    ["object", "{id}", "1", "--reason", "x"],
    ["unobject", "{id}", "1"],
    ["usage"],
    ["export-reply", "--template", "x"],
    ["export-report", "--template", "x"],
    ["diagrams"],
    ["estimate", "macro"],
    ["sequence"],
    ["run", "macro"],
]


@pytest.mark.parametrize("arguments", _READING_SUBCOMMANDS, ids=lambda a: a[0])
def test_every_reading_subcommand_refuses_a_different_spelling(
    repo: Path, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    create_range_review(repo, "main..feature", "mine")
    identifier = "octo/hello@Mine"
    if "{id}" in arguments:
        argv = [identifier if part == "{id}" else part for part in arguments]
    else:
        argv = [arguments[0], identifier, *arguments[1:]]
    assert main(argv) != 0
    captured = capsys.readouterr()
    assert "開きません" in captured.out + captured.err


def test_remove_claim_lock_keeps_the_file_while_a_creation_holds_it() -> None:
    review_id = check_new_review_id(ReviewId("octo", "hello", "mine", ReviewKind.RANGE))
    lock_path = _claim_lock_path()
    holder = _start_claim_holder(lock_path)
    try:
        assert creation_module.remove_claim_lock(review_id) is False
        assert lock_path.is_file()
    finally:
        _stop(holder)
    assert creation_module.remove_claim_lock(review_id) is True
    assert not lock_path.exists()


def test_claim_is_retaken_when_the_lock_file_was_replaced_after_locking(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = _claim_lock_path()
    original = locking._same_file
    calls: list[bool] = []

    def replace_then_check(descriptor: int, path: Path) -> bool:
        if not calls:
            if sys.platform == "win32":
                # Windows は開いているファイルを消せず、差し替わりは起きない。
                # 取り直しの分岐だけを、差し替わったという判定を一度返して確かめる
                calls.append(False)
                return False
            # 錠を握った直後に、削除側がファイルを消して作り直した形にする
            path.unlink()
            path.write_text("", encoding="utf-8")
        result = original(descriptor, path)
        calls.append(result)
        return result

    monkeypatch.setattr(locking, "_same_file", replace_then_check)
    review = create_range_review(repo, "main..feature", "mine")
    assert calls == [False, True]
    assert (review.id.directory() / "review.json").is_file()
    assert lock_path.is_file()


def test_remove_claim_lock_closes_before_unlinking_where_open_files_cannot_be_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_id = check_new_review_id(ReviewId("octo", "hello", "mine", ReviewKind.RANGE))
    lock_path = _claim_lock_path()
    lock_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(locking, "CAN_UNLINK_OPEN_FILE", False)
    open_descriptors: list[int] = []
    original_open = os.open
    original_unlink = Path.unlink

    def tracking_open(path: object, *args: int, **kwargs: int) -> int:
        descriptor = original_open(path, *args, **kwargs)  # type: ignore[arg-type]
        open_descriptors.append(descriptor)
        return descriptor

    def unlink_refusing_open_files(self: Path, *args: bool, **kwargs: bool) -> None:
        for descriptor in open_descriptors:
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            raise PermissionError("開いたままです")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(Path, "unlink", unlink_refusing_open_files)
    assert creation_module.remove_claim_lock(review_id) is True
    assert not lock_path.exists()
