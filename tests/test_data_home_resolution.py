"""データの置き場の根の決め方が docs/commands.md の節と一致することを確かめる。"""

import sys
from pathlib import Path

import pytest

from jigane.storage import store


def test_jigane_home_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "override"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert store.data_home() == tmp_path / "override"


def test_xdg_data_home_when_jigane_home_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("JIGANE_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", "linux")
    assert store.data_home() == tmp_path / "xdg" / "jigane"


@pytest.mark.parametrize("xdg", [None, ""])
def test_default_under_home_local_share(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, xdg: str | None
) -> None:
    monkeypatch.delenv("JIGANE_HOME", raising=False)
    if xdg is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", xdg)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert store.data_home() == tmp_path / ".local" / "share" / "jigane"


def test_windows_uses_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("JIGANE_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "platform", "win32")
    assert store.data_home() == tmp_path / "local" / "jigane"


def test_empty_jigane_home_falls_through_to_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", "")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", "linux")
    assert store.data_home() == tmp_path / "xdg" / "jigane"


def test_empty_jigane_home_falls_through_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", "")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert store.data_home() == tmp_path / ".local" / "share" / "jigane"


def test_windows_jigane_home_wins_over_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "override"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "platform", "win32")
    assert store.data_home() == tmp_path / "override"


def test_windows_without_localappdata_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIGANE_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(KeyError):
        store.data_home()
