import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from fakes import run_command_stub
from pr_support import git

from jigane.git.process import CommandError

ROOT = Path(__file__).resolve().parent.parent


def load_script() -> ModuleType:
    path = ROOT / "scripts" / "changed_tests.py"
    spec = importlib.util.spec_from_file_location("changed_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


changed_tests = load_script()


def write(root: Path, name: str, text: str = "x = 1\n") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_module_stems_use_leaf_and_joined_names() -> None:
    assert changed_tests.module_stems("jigane/a/b.py") == ["b", "a_b"]
    assert changed_tests.module_stems("jigane/storage/store.py") == ["store", "storage_store"]
    assert changed_tests.module_stems("jigane/commands/__init__.py") == ["commands"]
    assert changed_tests.module_stems("jigane/__init__.py") == []


def test_select_tests_maps_changed_tests_and_modules(tmp_path: Path) -> None:
    for name in (
        "tests/test_b.py",
        "tests/test_b_more.py",
        "tests/test_a_b.py",
        "tests/test_other.py",
        "tests/test_store.py",
    ):
        write(tmp_path, name)
    changed = [
        "jigane/a/b.py",
        "tests/test_store.py",
        "tests/helper.py",
        "tests/sub/test_x.py",
        "tests/test_gone.py",
        "README.md",
    ]
    assert changed_tests.select_tests(changed, tmp_path) == [
        "tests/test_a_b.py",
        "tests/test_b.py",
        "tests/test_b_more.py",
        "tests/test_store.py",
    ]


def test_module_name_is_dotted_and_drops_init() -> None:
    assert changed_tests.module_name("jigane/x/y.py") == "jigane.x.y"
    assert changed_tests.module_name("jigane/x/__init__.py") == "jigane.x"


@pytest.mark.parametrize(
    "import_line",
    [
        "from jigane.x.y import Z",
        "import jigane.x.y",
        "from jigane.x import y",
        "from jigane.x import (\n    other,\n    y,\n)",
    ],
)
def test_select_tests_adds_tests_importing_the_changed_module(
    tmp_path: Path, import_line: str
) -> None:
    write(tmp_path, "tests/test_unrelated_name.py", f"{import_line}\n")
    write(tmp_path, "tests/test_no_import.py", "from jigane.x import other\n")
    write(tmp_path, "tests/test_text_only.py", '# jigane.x.y\nNAME = "from jigane.x.y import Z"\n')
    assert changed_tests.select_tests(["jigane/x/y.py"], tmp_path) == [
        "tests/test_unrelated_name.py"
    ]


def test_select_tests_uses_the_package_name_for_init(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_by_package.py", "from jigane.x import something\n")
    write(tmp_path, "tests/test_sibling.py", "from jigane.x.y import Z\n")
    assert changed_tests.select_tests(["jigane/x/__init__.py"], tmp_path) == [
        "tests/test_by_package.py"
    ]


def test_select_tests_is_empty_when_nothing_relates(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_other.py")
    assert changed_tests.select_tests(["docs/x.md", "jigane/nothing.py"], tmp_path) == []


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    write(repo, "jigane/kept.py")
    write(repo, "jigane/gone.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    git(repo, "switch", "-c", "topic")
    return repo


def test_changed_files_cover_committed_uncommitted_and_untracked(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo, "jigane/committed.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "topic")
    write(repo, "jigane/kept.py", "x = 2\n")
    write(repo, "jigane/untracked.py")
    (repo / "jigane" / "gone.py").unlink()
    runner = changed_tests.git_runner(repo)
    assert changed_tests.changed_files(runner) == {
        "jigane/committed.py",
        "jigane/kept.py",
        "jigane/untracked.py",
    }


def test_find_base_fails_without_main(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "trunk")
    write(repo, "a.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    with pytest.raises(RuntimeError, match="枝が無い"):
        changed_tests.find_base(changed_tests.git_runner(repo))


def test_find_base_falls_back_to_origin_main() -> None:
    def fake_git(args: list[str]) -> str:
        if args == ["merge-base", "HEAD", "main"]:
            raise CommandError(["git", *args], "unknown revision")
        return "abc123\n"

    assert changed_tests.find_base(fake_git) == "abc123"


def test_run_pytest_passes_failure_code_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def fake_run(args: list[str]) -> str:
        raise CommandError(args, "err-text", stdout="out-text", returncode=3)

    monkeypatch.setattr(changed_tests, "run_tool", run_command_stub(fake_run))
    assert changed_tests.run_pytest(["tests/test_x.py"], tmp_path) == 3
    assert "out-text" in capsys.readouterr().out


def test_run_pytest_returns_zero_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(changed_tests, "run_tool", run_command_stub(lambda _args: "passed\n"))
    assert changed_tests.run_pytest(["tests/test_x.py"], tmp_path) == 0
    assert "passed" in capsys.readouterr().out


def test_run_pytest_reraises_when_pytest_did_not_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(args: list[str]) -> str:
        raise CommandError(args, "no exec", returncode=None)

    monkeypatch.setattr(changed_tests, "run_tool", run_command_stub(fake_run))
    with pytest.raises(CommandError):
        changed_tests.run_pytest(["tests/test_x.py"], tmp_path)


def patch_main(
    monkeypatch: pytest.MonkeyPatch, tests: list[str], run_pytest: Callable[..., int]
) -> None:
    monkeypatch.setattr(changed_tests, "changed_files", lambda _git: set())
    monkeypatch.setattr(changed_tests, "select_tests", lambda _changed, _root: tests)
    monkeypatch.setattr(changed_tests, "run_pytest", run_pytest)


def test_main_list_selects_without_running_pytest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(_tests: list[str], _root: Path) -> int:
        raise AssertionError("pytest must not run with --list")

    patch_main(monkeypatch, ["tests/test_a.py"], forbidden)
    assert changed_tests.main(["--list"]) == 0
    assert "tests/test_a.py" in capsys.readouterr().out


def test_main_returns_zero_early_when_nothing_selected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(_tests: list[str], _root: Path) -> int:
        raise AssertionError("pytest must not run without tests")

    patch_main(monkeypatch, [], forbidden)
    assert changed_tests.main([]) == 0
    assert "走らせない" in capsys.readouterr().out


def test_main_returns_pytest_failure_code(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run_pytest(tests: list[str], _root: Path) -> int:
        calls.append(tests)
        return 5

    patch_main(monkeypatch, ["tests/test_a.py"], fake_run_pytest)
    assert changed_tests.main([]) == 5
    assert calls == [["tests/test_a.py"]]
