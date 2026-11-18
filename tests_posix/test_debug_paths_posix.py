"""`jigane debug paths` の根の検査の結果の試験。mode と所有者は POSIX だけで成り立つ。"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from jigane import commands


def check_for(
    monkeypatch: pytest.MonkeyPatch, home: Path, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    monkeypatch.setenv("JIGANE_HOME", str(home))
    assert commands.main(["debug", "paths", "--json"]) == 0
    check: dict[str, Any] = json.loads(capsys.readouterr().out)["data_home_check"]
    return check


def test_private_root_is_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "jigane"
    home.mkdir()
    home.chmod(0o700)
    assert check_for(monkeypatch, home, capsys) == {"exists": True, "stops": False, "kind": "ok"}


def test_root_writable_by_others_stops_but_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "jigane"
    home.mkdir()
    home.chmod(0o777)
    assert check_for(monkeypatch, home, capsys) == {
        "exists": True,
        "stops": True,
        "kind": "writable_by_others",
    }


def test_root_owned_by_another_user_stops_but_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "jigane"
    home.mkdir()
    other_uid = home.stat().st_uid + 1
    monkeypatch.setattr(os, "getuid", lambda: other_uid, raising=False)
    assert check_for(monkeypatch, home, capsys) == {
        "exists": True,
        "stops": True,
        "kind": "owner_mismatch",
    }


def test_owner_mismatch_wins_over_writable_by_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "jigane"
    home.mkdir()
    home.chmod(0o770)
    other_uid = home.stat().st_uid + 1
    monkeypatch.setattr(os, "getuid", lambda: other_uid, raising=False)
    assert check_for(monkeypatch, home, capsys)["kind"] == "owner_mismatch"
