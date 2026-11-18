import json
from pathlib import Path
from typing import Any

import pytest

from jigane import commands
from jigane.repository_docs.settings import SETTINGS_FILE


def run_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert commands.main(["debug", "paths", "--json"]) == 0
    body: dict[str, Any] = json.loads(capsys.readouterr().out)
    return body


def test_json_has_fixed_keys_and_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("JIGANE_HOME", str(home))
    body = run_json(capsys)
    assert list(body) == ["data_home", "data_home_check", "repository_docs_settings"]
    assert list(body["data_home_check"]) == ["exists", "stops", "kind"]
    assert body["data_home"] == str(home)
    assert body["repository_docs_settings"] == str(home / SETTINGS_FILE)


def test_absent_root_is_reported_and_not_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "missing"
    monkeypatch.setenv("JIGANE_HOME", str(home))
    check = run_json(capsys)["data_home_check"]
    assert check == {"exists": False, "stops": False, "kind": "absent"}
    assert not home.exists()


def test_windows_is_not_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    monkeypatch.setattr("jigane.storage.store.sys.platform", "win32")
    check = run_json(capsys)["data_home_check"]
    assert check == {"exists": True, "stops": False, "kind": "not_checked"}


def test_windows_absent_root_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "missing"))
    monkeypatch.setattr("jigane.storage.store.sys.platform", "win32")
    check = run_json(capsys)["data_home_check"]
    assert check == {"exists": False, "stops": False, "kind": "absent"}


def test_text_output_has_three_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "missing"
    monkeypatch.setenv("JIGANE_HOME", str(home))
    assert commands.main(["debug", "paths"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"data_home\t{home}",
        "data_home_check\tabsent",
        f"repository_docs_settings\t{home / SETTINGS_FILE}",
    ]
    assert not home.exists()
