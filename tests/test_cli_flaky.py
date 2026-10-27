import json
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_GATE_VIOLATION, EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.git.process import run_command

FIXTURES = Path(__file__).parent / "fixtures" / "flaky" / "python"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def _put(root: Path, path: str, fixture: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text((FIXTURES / fixture).read_text(encoding="utf-8"), encoding="utf-8")


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(["flaky", *argv])
    return code, capsys.readouterr().out


def test_violation_exits_1_with_three_sentences_and_location(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _put(project, "tests/test_a.py", "time-bad.txt")
    code, out = _run(capsys)
    assert code == EXIT_GATE_VIOLATION
    assert "G tests/test_a.py:7 [FLK-TIME]" in out
    assert "懸念:" in out
    assert "理由:" in out
    assert "直す向き:" in out


def test_clean_tree_exits_0(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _put(project, "tests/test_a.py", "time-good.txt")
    code, out = _run(capsys)
    assert code == EXIT_OK
    assert "違反 0 件、警告 0 件" in out


def test_warning_only_exits_0_and_strict_exits_1(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _put(project, "tests/test_a.py", "concurrency-bad.txt")
    code, out = _run(capsys)
    assert code == EXIT_OK
    assert "W tests/test_a.py" in out
    strict_code, _ = _run(capsys, "--strict")
    assert strict_code == EXIT_GATE_VIOLATION


def test_json_shape(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _put(project, "tests/test_a.py", "time-bad.txt")
    _put(project, "tests/test_b.py", "concurrency-bad.txt")
    code, out = _run(capsys, "--json")
    assert code == EXIT_GATE_VIOLATION
    result = json.loads(out)
    assert result["counts"] == {
        "violations": len(result["violations"]),
        "warnings": len(result["warnings"]),
    }
    assert result["violations"]
    assert result["warnings"]
    first = result["violations"][0]
    assert set(first) == {"id", "kind", "path", "line", "concern", "violation", "direction"}
    assert first["kind"] == "violation"
    assert result["warnings"][0]["kind"] == "warning"


def test_without_paths_checks_all_work_files(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _put(project, "a/test_a.py", "time-bad.txt")
    _put(project, "b/test_b.py", "time-bad.txt")
    _, out = _run(capsys, "--json")
    paths = {entry["path"] for entry in json.loads(out)["violations"]}
    assert paths == {"a/test_a.py", "b/test_b.py"}


def test_directory_argument_narrows_to_files_below(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _put(project, "a/test_a.py", "time-bad.txt")
    _put(project, "b/test_b.py", "time-bad.txt")
    _, out = _run(capsys, "a", "--json")
    paths = {entry["path"] for entry in json.loads(out)["violations"]}
    assert paths == {"a/test_a.py"}


@pytest.mark.usefixtures("project")
def test_missing_path_fails_as_processing_failure(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["flaky", "nowhere"])
    assert code == EXIT_PROCESSING_FAILURE
    assert "パスがありません" in capsys.readouterr().err


def test_writes_nothing_to_jigane_home(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _put(project, "tests/test_a.py", "time-bad.txt")
    _run(capsys)
    assert not (tmp_path / "home").exists()


def test_file_argument_checks_only_that_file(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _put(project, "a/test_a.py", "time-bad.txt")
    _put(project, "a/test_c.py", "time-bad.txt")
    _, out = _run(capsys, "a/test_a.py", "--json")
    paths = {entry["path"] for entry in json.loads(out)["violations"]}
    assert paths == {"a/test_a.py"}


def test_directory_argument_excludes_directory_sharing_its_name_prefix(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _put(project, "tests/test_a.py", "time-bad.txt")
    _put(project, "tests_extra/test_b.py", "time-bad.txt")
    _, out = _run(capsys, "tests", "--json")
    paths = {entry["path"] for entry in json.loads(out)["violations"]}
    assert paths == {"tests/test_a.py"}


@pytest.mark.usefixtures("project")
def test_path_outside_working_directory_fails_as_processing_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test_o.py").write_text("x = 1\n", encoding="utf-8")
    code = main(["flaky", str(outside)])
    assert code == EXIT_PROCESSING_FAILURE
    assert "作業ディレクトリの外のパス" in capsys.readouterr().err


def test_untracked_file_in_work_tree_is_checked_unless_ignored(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_command(["git", "init", "-q"], cwd=project)
    (project / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _put(project, "tests/test_new.py", "time-bad.txt")
    _put(project, "ignored/test_skip.py", "time-bad.txt")
    _, out = _run(capsys, "--json")
    paths = {entry["path"] for entry in json.loads(out)["violations"]}
    assert paths == {"tests/test_new.py"}
