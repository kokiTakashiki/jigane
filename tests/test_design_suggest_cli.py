import json
from pathlib import Path

import pytest
from pr_support import git

from jigane import commands


def _setup(tmp_path: Path) -> Path:
    (tmp_path / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    design = tmp_path / "design.md"
    design.write_text("# 題\n\n## 1. 序論\n\n`Cart` と `Ghost`。\n", encoding="utf-8")
    return design


def test_suggest_prints_candidates_and_match_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    design = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert commands.main(["design", "suggest", str(design)]) == 0
    out = capsys.readouterr().out
    assert "1. cart.py（直接、1 節）" in out
    assert "1 節が本文で `Cart` を名指しました" in out
    assert "上限で切った候補: 0 件" in out
    assert "Ghost（type、1 節、5 行）: 引き当たりませんでした" in out
    assert "Cart（type、1 節、5 行）: class cart.py:1" in out


def test_suggest_json_and_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    design = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert commands.main(["design", "suggest", str(design), "--json", "--limit", "1"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["path"] for c in data["candidates"]] == ["cart.py"]
    assert data["omitted"] == 0
    reason = data["candidates"][0]["reasons"][0]
    assert reason["trace"] == "named"
    assert reason["section"] == {"number": "1", "title": "1. 序論"}
    assert {m["name"]: m["resolved"] for m in data["matches"]} == {"Cart": True, "Ghost": False}
    cart = next(m for m in data["matches"] if m["name"] == "Cart")
    assert cart["locations"][0]["kind"] == "class"


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_limit_rejects_non_positive_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    design = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as raised:
        commands.main(["design", "suggest", str(design), "--limit", value])
    assert raised.value.code == 2
    assert "--limit" in capsys.readouterr().err


def test_missing_design_file_is_a_user_facing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert commands.main(["design", "suggest", str(tmp_path / "none.md")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("jigane: ")
    assert "none.md" in err
    assert "Traceback" not in err


def test_unreadable_design_file_is_a_user_facing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "design.md"
    directory.mkdir()
    assert commands.main(["design", "suggest", str(directory), "--json"]) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert json.loads(captured.out)["error"]["kind"] == "filesystem"


def test_non_utf8_design_file_is_a_user_facing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    design = tmp_path / "design.md"
    design.write_bytes(b"\xff\xfe\x00")
    assert commands.main(["design", "suggest", str(design)]) == 2
    assert "Traceback" not in capsys.readouterr().err


def _setup_ambiguous(tmp_path: Path) -> Path:
    (tmp_path / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (tmp_path / "helpers.py").write_text("def twin() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "more.py").write_text("def twin() -> int:\n    return 2\n", encoding="utf-8")
    design = tmp_path / "design.md"
    design.write_text(
        "# 題\n\n## 1. 序論\n\n`Cart` と `Ghost`。\n\n## 2. 本論\n\n`twin()` を呼ぶ。\n",
        encoding="utf-8",
    )
    return design


def test_suggest_text_shows_found_not_found_and_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    design = _setup_ambiguous(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert commands.main(["design", "suggest", str(design)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("- Cart（") and "class cart.py:1" in line for line in lines)
    ghost = next(line for line in lines if line.startswith("- Ghost（"))
    assert ghost.endswith("引き当たりませんでした")
    twin = next(line for line in lines if line.startswith("- twin（"))
    assert "曖昧（候補 " in twin
    assert "helpers.py:1" in twin
    assert "more.py:1" in twin


def test_suggest_json_shows_three_match_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    design = _setup_ambiguous(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert commands.main(["design", "suggest", str(design), "--json"]) == 0
    matches = {m["name"]: m for m in json.loads(capsys.readouterr().out)["matches"]}
    assert (matches["Cart"]["status"], matches["Cart"]["resolved"]) == ("found", True)
    assert (matches["Ghost"]["status"], matches["Ghost"]["resolved"]) == ("not_found", False)
    assert matches["Ghost"]["locations"] == []
    twin = matches["twin"]
    assert (twin["status"], twin["resolved"]) == ("ambiguous", False)
    assert sorted(loc["path"] for loc in twin["locations"]) == ["helpers.py", "more.py"]


def test_untracked_files_carry_a_mark_in_text_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    design = _setup(tmp_path)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "design.md")
    monkeypatch.chdir(tmp_path)
    assert commands.main(["design", "suggest", str(design)]) == 0
    out = capsys.readouterr().out
    assert "1. cart.py（直接、1 節、追跡外）" in out
    assert "Cart（type、1 節、5 行）: class cart.py:1（追跡外）" in out
    assert commands.main(["design", "suggest", str(design), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["candidates"][0]["untracked"] is True
    assert data["matches"][0]["locations"][0]["untracked"] is True


def _setup_accessor(tmp_path: Path) -> Path:
    (tmp_path / "paths.py").write_text('NOTES_FILE = "notes.json"\n', encoding="utf-8")
    (tmp_path / "reader.py").write_text(
        "from paths import NOTES_FILE\n\n\ndef load():\n    return NOTES_FILE\n", encoding="utf-8"
    )
    design = tmp_path / "design.md"
    design.write_text("# 題\n\n## 1. 序論\n\n`notes.json` を保存する。\n", encoding="utf-8")
    return design


def test_accessor_reason_is_shown_in_text_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    design = _setup_accessor(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert commands.main(["design", "suggest", str(design)]) == 0
    out = capsys.readouterr().out
    assert "1 節が名指した保存物 `notes.json` を読む関数（一段のたどり）" in out
    assert commands.main(["design", "suggest", str(design), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    reader = next(c for c in data["candidates"] if c["path"] == "reader.py")
    assert [r["trace"] for r in reader["reasons"]] == ["reader"]
