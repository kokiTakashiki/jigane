"""write_text_atomic が、mkstemp の既定に依らず許可 0600 のファイルを作ることを確かめる。"""

import os
import stat
import tempfile
from pathlib import Path

import pytest

from jigane.storage.atomic_write import write_text_atomic


@pytest.fixture
def permissive_mkstemp(monkeypatch: pytest.MonkeyPatch) -> None:
    """mkstemp が 0644 で作る環境を再現し、明示の chmod が無ければ 0600 にならないようにする。"""
    original = tempfile.mkstemp

    def mkstemp_with_0644(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, name = original(*args, **kwargs)  # type: ignore[call-overload]
        Path(name).chmod(0o644)
        return descriptor, name

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp_with_0644)


@pytest.mark.usefixtures("permissive_mkstemp")
def test_created_file_is_mode_0600_even_under_a_permissive_umask(tmp_path: Path) -> None:
    path = tmp_path / "findings.json"
    previous = os.umask(0)
    try:
        write_text_atomic(path, "new")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.usefixtures("permissive_mkstemp")
def test_replacing_a_group_readable_file_yields_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "findings.json"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o644)
    write_text_atomic(path, "new")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
