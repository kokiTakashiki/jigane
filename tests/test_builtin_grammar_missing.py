import json
from pathlib import Path

import pytest

from jigane import commands
from jigane.commands import failures
from jigane.commands.exit_codes import EXIT_PROCESSING_FAILURE
from jigane.languages import fetch_main, grammars, registry

ACTION = (
    "`jigane languages fetch` を走らせてください。開発の木では `make setup` を走らせてください"
)


@pytest.fixture
def empty_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "empty-grammars"
    monkeypatch.setattr(grammars, "cache_root", lambda: root)
    return root


@pytest.mark.usefixtures("empty_store")
def test_subcommand_stops_at_startup_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = commands.main(["list"])
    err = capsys.readouterr().err
    assert exit_code == EXIT_PROCESSING_FAILURE
    assert err.startswith("jigane: 文法 python が ")
    assert "Traceback" not in err
    lines = err.splitlines()
    # 理由の一行と次の操作の一行。案内を理由の文に重ねて出さない
    assert len(lines) == 2
    assert lines[1] == f"次の操作: {ACTION}"
    assert "make setup" not in lines[0]


@pytest.mark.usefixtures("empty_store")
def test_json_failure_carries_kind_reason_and_action(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = commands.main(["list", "--json"])
    captured = capsys.readouterr()
    assert exit_code == EXIT_PROCESSING_FAILURE
    error = json.loads(captured.out)["error"]
    assert error["kind"] == "grammar"
    assert error["reason"].startswith("文法 python が ")
    assert error["reason"] == captured.err.splitlines()[0].removeprefix("jigane: ")
    assert error["action"] == ACTION
    assert "Traceback" not in captured.err


@pytest.mark.usefixtures("empty_store")
def test_diagrams_also_stops_at_startup(capsys: pytest.CaptureFixture[str]) -> None:
    assert commands.main(["diagrams", "any-review"]) == EXIT_PROCESSING_FAILURE
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.usefixtures("empty_store")
def test_languages_list_runs_without_grammars() -> None:
    assert commands.main(["languages", "list"]) == 0


@pytest.mark.usefixtures("empty_store")
def test_languages_fetch_runs_without_grammars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_main, "fetch_and_report", lambda: fetch_main.Fetched((), ()))
    assert commands.main(["languages", "fetch", "--accept-terms"]) == 0


def test_load_time_grammar_error_is_its_own_kind(tmp_path: Path) -> None:
    library = grammars.library_dir(tmp_path)
    library.mkdir(parents=True)
    (library / "libtree_sitter_python.so").write_bytes(b"not the recorded library")
    with pytest.raises(grammars.GrammarError) as mismatch:
        grammars.verify(tmp_path, "python")
    failure = failures.describe(mismatch.value)
    assert failure.kind == "grammar"
    assert failure.action == ACTION
    assert failure.reason == mismatch.value.problem
    assert "make setup" not in failure.reason


def test_require_ready_passes_with_the_real_builtin_grammars() -> None:
    registry.require_ready()


def test_require_ready_passes_with_only_the_four_builtin_libraries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = grammars.library_dir(tmp_path)
    library.mkdir(parents=True)
    for stem in ("python", "java", "swift", "c_sharp"):
        (library / f"libtree_sitter_{stem}.so").write_bytes(b"")
    monkeypatch.setattr(grammars, "cache_root", lambda: tmp_path)
    registry.require_ready()
