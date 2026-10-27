import contextlib
import importlib.util
import io
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
from pr_support import git

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_no_git_stash.py"
# 検査の対象にこのファイル自身が掛からないよう、語を分けて組み立てる。
WORD = "sta" + "sh"
VIOLATION = f"git {WORD} pop"


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_no_git_stash", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_checker(root: Path, *prefixes: str) -> tuple[int, str]:
    # 標準エラーに出る場所の表示まで、main を通して確かめる。
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = load_checker().main([str(root), *prefixes])
    return code, buffer.getvalue()


def test_sources_do_not_call_git_stash() -> None:
    assert load_checker().find_offenders(ROOT, load_checker().DEFAULT_PREFIXES) == []


def test_checker_fails_and_shows_the_place_of_a_violation(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.sh").write_text(f"echo ok\n{VIOLATION}\n", encoding="utf-8")
    code, output = run_checker(tmp_path, "scripts/")
    assert code == 1
    assert "scripts/run.sh:2" in output


def test_checker_passes_on_a_tree_without_violations(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.sh").write_text("echo ok\n", encoding="utf-8")
    assert run_checker(tmp_path, "scripts/")[0] == 0


def test_checker_catches_an_option_between_git_and_the_subcommand(tmp_path: Path) -> None:
    (tmp_path / "justfile").write_text(f"task:\n    git -C dir {WORD}\n", encoding="utf-8")
    assert run_checker(tmp_path, "justfile")[0] == 1


def test_checker_reads_only_code_blocks_of_markdown(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text(f"説明に `{VIOLATION}` と書く。\n", encoding="utf-8")
    (tmp_path / "docs" / "b.md").write_text(f"手順\n\n```sh\n{VIOLATION}\n```\n", encoding="utf-8")
    assert run_checker(tmp_path, "docs/a.md")[0] == 0
    code, output = run_checker(tmp_path, "docs/b.md")
    assert code == 1
    assert "docs/b.md:4" in output


def test_checker_uses_the_default_prefixes_without_arguments(tmp_path: Path) -> None:
    (tmp_path / "justfile").write_text(f"task:\n    {VIOLATION}\n", encoding="utf-8")
    code, output = run_checker(tmp_path)
    assert code == 1
    assert "justfile:2" in output


def test_checker_ignores_prose_after_the_fence_closes(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "c.md").write_text(
        f"```sh\necho ok\n```\n\n{VIOLATION} と書かない。\n", encoding="utf-8"
    )
    assert run_checker(tmp_path, "docs/")[0] == 0


def test_checker_finds_tracked_and_untracked_files_in_a_git_tree(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "tracked.sh").write_text(f"{VIOLATION}\n", encoding="utf-8")
    git(tmp_path, "add", "scripts/tracked.sh")
    (tmp_path / "scripts" / "new.sh").write_text(f"echo ok\n{VIOLATION}\n", encoding="utf-8")
    code, output = run_checker(tmp_path, "scripts/")
    assert code == 1
    assert "scripts/tracked.sh:1" in output
    assert "scripts/new.sh:2" in output


def test_checker_leaves_python_files_to_the_ast_grep_rules(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.py").write_text(f"# {VIOLATION}\n", encoding="utf-8")
    assert run_checker(tmp_path, "scripts/")[0] == 0


def test_ast_grep_scans_the_directories_of_the_default_prefixes() -> None:
    entrypoints = tomllib.loads((ROOT / "entrypoints.toml").read_text(encoding="utf-8"))
    commands = [
        command
        for task in entrypoints["task"]
        for command in task.get("commands", [])
        if "ast-grep scan" in command
    ]
    assert len(commands) == 1
    scanned = commands[0].split("ast-grep scan")[1].split()
    directories = [p.rstrip("/") for p in load_checker().DEFAULT_PREFIXES if p.endswith("/")]
    assert scanned == directories


@pytest.mark.parametrize(
    "line",
    [f"{VIOLATION};", f"{VIOLATION} | cat", f"({VIOLATION})"],
)
def test_checker_catches_a_form_with_symbols_attached(tmp_path: Path, line: str) -> None:
    (tmp_path / "justfile").write_text(f"task:\n    {line}\n", encoding="utf-8")
    assert run_checker(tmp_path, "justfile")[0] == 1
