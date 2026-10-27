"""置き換えの誤りを、WinError 5 でない限り試し直さないことを確かめる。"""

from pathlib import Path

import pytest

from jigane.storage.store import write_text_atomic


def test_permission_error_without_winerror_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def deny(self: Path, _target: Path) -> Path:
        calls.append(self)
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "replace", deny)
    path = tmp_path / "findings.json"
    with pytest.raises(PermissionError) as raised:
        write_text_atomic(path, "new")
    assert type(raised.value) is PermissionError
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []
