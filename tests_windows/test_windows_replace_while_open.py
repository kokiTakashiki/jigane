"""読んでいる最中の保存物を置き換えられるかを、Windows で確かめる。

Windows の os.replace は、置き換え先を別の手が開いている間は WinError 5 で落ちうる。
show やエディタが保存物を開いている最中に run が書き終える場面を再現する。
"""

import threading
from pathlib import Path

import pytest

from jigane.storage import store
from jigane.storage.store import StoredFileInUseError, write_text_atomic


def test_write_succeeds_while_another_handle_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "findings.json"
    path.write_text("old", encoding="utf-8")
    with path.open(encoding="utf-8") as reader:
        # 試し直しの上限（2 秒）より十分短い 0.3 秒後に、別のスレッドが読みの手を閉じる
        closer = threading.Timer(0.3, reader.close)
        closer.start()
        try:
            content_read = reader.read()
            write_text_atomic(path, "new")
        finally:
            closer.join()
    assert content_read == "old"
    assert path.read_text(encoding="utf-8") == "new"


def test_write_names_the_file_when_the_reader_never_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "REPLACE_RETRY_COUNT", 3)
    monkeypatch.setattr(store, "REPLACE_RETRY_SECONDS", 0.01)
    path = tmp_path / "findings.json"
    path.write_text("old", encoding="utf-8")
    with path.open(encoding="utf-8") as reader:
        with pytest.raises(StoredFileInUseError) as raised:
            write_text_atomic(path, "new")
        assert reader.read() == "old"
    assert str(path) in str(raised.value)
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["findings.json"]
    assert path.read_text(encoding="utf-8") == "old"
