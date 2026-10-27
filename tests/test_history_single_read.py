import json
import logging
from collections import Counter
from pathlib import Path

import pytest
from fakes import MacroFake, discovered
from test_history_warning import _break_history
from test_review_relations import finding, save, write_findings

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings import records as findings_module
from jigane.findings.records import HISTORY_FILE
from jigane.storage.store import ReviewKind


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _count_reads(monkeypatch: pytest.MonkeyPatch) -> Counter[Path]:
    reads: Counter[Path] = Counter()
    original = findings_module._read_history

    def counting(directory: Path) -> findings_module.HistoryRead:
        reads[directory] += 1
        return original(directory)

    monkeypatch.setattr(findings_module, "_read_history", counting)
    return reads


def _history_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "履歴" in r.getMessage()]


def test_run_reads_the_own_history_once_and_each_related_history_once(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    write_findings(older, [finding(1, "old")])
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    reads = _count_reads(monkeypatch)
    assert main(["run", str(current.id), "macro"], invoke=MacroFake([discovered("M4")])) == EXIT_OK
    assert reads[current.id.directory()] == 1
    assert reads[older.id.directory()] == 1


def test_a_corrupt_related_history_is_reported_and_not_logged(
    project: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    older = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    (older.id.directory() / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        arguments = ["run", str(current.id), "macro", "--json"]
        assert main(arguments, invoke=MacroFake([discovered("M4")])) == EXIT_OK
    [warning] = json.loads(capsys.readouterr().out)["history_warnings"]
    assert warning["origin"] == {"kind": "related", "review": "@equiv1"}
    assert warning["path"] == str(older.id.directory() / HISTORY_FILE)
    assert "関連するレビュー @equiv1" in warning["text"]
    assert _history_logs(caplog) == []


def test_a_corrupt_related_history_shows_in_the_body(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    older = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    (older.id.directory() / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    assert main(["run", str(current.id), "macro"], invoke=MacroFake([discovered("M4")])) == EXIT_OK
    assert "警告: 関連するレビュー @equiv1 の履歴" in capsys.readouterr().out


def test_own_corrupt_history_is_reported_once_and_not_logged(
    project: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    current = save(project, "solo", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    warning = _break_history(current)
    with caplog.at_level(logging.WARNING):
        arguments = ["run", str(current.id), "macro", "--json"]
        assert main(arguments, invoke=MacroFake([discovered("M4")])) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["history_warnings"] == [warning.to_json()]
    assert _history_logs(caplog) == []


@pytest.mark.parametrize("as_json", [False, True])
def test_estimate_reports_own_and_related_corrupt_histories(
    project: Path, capsys: pytest.CaptureFixture[str], *, as_json: bool
) -> None:
    older = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    (older.id.directory() / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    own = _break_history(current)
    arguments = ["estimate", str(current.id), "macro", *(["--json"] if as_json else [])]
    assert main(arguments) == EXIT_OK
    out = capsys.readouterr().out
    if as_json:
        own_json, related_json = json.loads(out)["history_warnings"]
        assert own_json == own.to_json()
        assert own_json["origin"] == {"kind": "own"}
        assert related_json["origin"] == {"kind": "related", "review": "@equiv1"}
    else:
        assert f"警告: {own.text()}" in out
        assert "警告: 関連するレビュー @equiv1 の履歴" in out


def test_estimate_json_has_no_history_warnings_for_sound_histories(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    current = save(project, "solo", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    assert main(["estimate", str(current.id), "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["history_warnings"] == []
