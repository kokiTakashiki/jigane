import json
from pathlib import Path

import pytest
from fakes import MacroFake, discovered
from test_security import SecurityFake, s_finding, write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings import records as findings_module
from jigane.findings.records import (
    HISTORY_FILE,
    HistoryWarning,
    RelatedHistory,
    load_history_checked,
)
from jigane.reviews.lifecycle import Review, create_feature_review


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    for name in ("a", "b"):
        (root / "src" / f"{name}.py").write_text(
            f"# {name}\n" + "x = 1\n" * 3500, encoding="utf-8"
        )
    monkeypatch.chdir(root)
    return create_feature_review(root, ["src"], "p")


def _break_history(review: Review) -> HistoryWarning:
    directory = review.id.directory()
    (directory / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    [warning] = load_history_checked(directory).warnings
    assert warning.path == directory / HISTORY_FILE
    assert warning.reason.startswith("JSONDecodeError")
    return warning


def _json_of(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    out: dict[str, object] = json.loads(capsys.readouterr().out)
    return out


def test_the_warning_names_the_file_the_reason_and_the_missing_material() -> None:
    path = Path("/r/findings_history.json")
    warning = HistoryWarning(path, "ValueError: x")
    assert warning.text() == (
        f"{path} が壊れています（ValueError: x）。前の回の指摘は資料に載りません"
    )
    assert warning.to_json() == {
        "path": str(path),
        "reason": "ValueError: x",
        "text": warning.text(),
        "origin": {"kind": "own"},
    }


def test_the_related_warning_names_the_review_and_marks_its_origin() -> None:
    path = Path("/r/h.json")
    warning = HistoryWarning(path, "ValueError: x", RelatedHistory("@equiv1"))
    assert warning.text() == (
        f"関連するレビュー @equiv1 の履歴 {path} が壊れています（ValueError: x）。"
        "そのレビューの前の回の指摘は資料に載りません"
    )
    assert warning.to_json() == {
        "path": str(path),
        "reason": "ValueError: x",
        "text": warning.text(),
        "origin": {"kind": "related", "review": "@equiv1"},
    }


# 二度目の実行は書き込みの拒否で止まるので、形ごとに別のレビューで一度だけ走らせる
@pytest.mark.parametrize("as_json", [False, True])
def test_run_shows_the_warning_in_the_body_and_in_json(
    review: Review, capsys: pytest.CaptureFixture[str], *, as_json: bool
) -> None:
    warning = _break_history(review)
    arguments = ["run", str(review.id), "macro", *(["--json"] if as_json else [])]
    assert main(arguments, invoke=MacroFake([discovered("M4")])) == EXIT_OK
    if as_json:
        assert _json_of(capsys)["history_warnings"] == [warning.to_json()]
    else:
        assert f"警告: {warning.text()}" in capsys.readouterr().out


@pytest.mark.parametrize("as_json", [False, True])
def test_run_of_the_sharded_groups_shows_the_warning(
    review: Review, capsys: pytest.CaptureFixture[str], *, as_json: bool
) -> None:
    warning = _break_history(review)
    write_mcp()
    arguments = ["run", str(review.id), "security", *(["--json"] if as_json else [])]
    assert main(arguments, invoke=SecurityFake({1: [s_finding()], 2: []})) == EXIT_OK
    if as_json:
        assert _json_of(capsys)["history_warnings"] == [warning.to_json()]
    else:
        assert f"警告: {warning.text()}" in capsys.readouterr().out


def test_a_healthy_history_shows_no_warning(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    name = str(review.id)
    assert main(["run", name, "macro"], invoke=MacroFake([discovered("M4")])) == EXIT_OK
    assert "壊れている" not in capsys.readouterr().out
    assert main(["run", name, "macro", "--json"], invoke=MacroFake([discovered("M4")])) == EXIT_OK
    assert _json_of(capsys)["history_warnings"] == []
    for view in (None, "macro", "micro", "security"):
        arguments = ["show", name, *([view] if view else [])]
        assert main(arguments) == EXIT_OK
        assert "壊れている" not in capsys.readouterr().out
        assert main([*arguments, "--json"]) == EXIT_OK
        assert _json_of(capsys)["history_warnings"] == []


@pytest.mark.parametrize("view", [None, "macro", "micro", "security"])
def test_show_shows_the_warning_in_the_body_and_in_json(
    review: Review, capsys: pytest.CaptureFixture[str], view: str | None
) -> None:
    name = str(review.id)
    assert main(["run", name, "macro"], invoke=MacroFake([discovered("M4")])) == EXIT_OK
    warning = _break_history(review)
    capsys.readouterr()
    arguments = ["show", name, *([view] if view else [])]
    assert main(arguments) == EXIT_OK
    assert f"警告: {warning.text()}" in capsys.readouterr().out
    assert main([*arguments, "--json"]) == EXIT_OK
    assert _json_of(capsys)["history_warnings"] == [warning.to_json()]


def test_the_checked_read_pairs_the_rounds_with_the_warnings(tmp_path: Path) -> None:
    assert load_history_checked(tmp_path).warnings == ()
    (tmp_path / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    loaded = load_history_checked(tmp_path)
    assert dict(loaded.rounds) == {}
    assert [warning.path for warning in loaded.warnings] == [tmp_path / HISTORY_FILE]


def test_show_reads_the_history_once_per_view(
    review: Review, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    name = str(review.id)
    assert main(["run", name, "macro"], invoke=MacroFake([discovered("M4")])) == EXIT_OK
    _break_history(review)
    capsys.readouterr()
    reads: list[Path] = []
    original = findings_module._read_history

    def counting(directory: Path) -> object:
        reads.append(directory)
        return original(directory)

    monkeypatch.setattr(findings_module, "_read_history", counting)
    for arguments in (["show", name], ["show", name, "--json"], ["show", name, "macro"]):
        reads.clear()
        assert main(arguments) == EXIT_OK
        assert len(reads) == 1, arguments
