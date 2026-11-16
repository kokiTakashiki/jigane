"""置き場の検査が、途中の部分を並行して作られている最中でも外と誤らないかを、Windows で確かめる。

ntpath.realpath は、まだ無い部分の解決に失敗すると長いパスの前置きの付いた形を作り、
二度目の失敗の理由が最初と同じときだけ前置きを外す。別のスレッドが owner と repo の
ディレクトリを作る途中だと、最初は「ディレクトリが無い」、二度目は「ファイルが無い」になる。
"""

import ntpath
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from jigane.storage.store import ReviewId, ReviewKind, check_new_review_id

if TYPE_CHECKING:
    from collections.abc import Callable

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3


def test_new_review_id_is_inside_home_while_its_parents_are_being_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("JIGANE_HOME", str(home))
    real = cast("Callable[[str], str]", vars(ntpath)["_getfinalpathname"])
    failures: list[int] = []

    def racing(path: str) -> str:
        if Path(path).exists():
            return real(path)
        # 最初の失敗だけ「ディレクトリが無い」、以後は並行の作成が進んだ後の「ファイルが無い」
        winerror = ERROR_FILE_NOT_FOUND if failures else ERROR_PATH_NOT_FOUND
        failures.append(winerror)
        raise OSError(None, "missing", path, winerror)

    monkeypatch.setattr(ntpath, "_getfinalpathname", racing)

    checked = check_new_review_id(ReviewId("octo", "hello", "mine", ReviewKind.RANGE))

    assert failures
    assert checked.directory() == home / "octo" / "hello" / "@mine"
