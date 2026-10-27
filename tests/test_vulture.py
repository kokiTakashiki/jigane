import sys
import tomllib
from pathlib import Path

from dev_launch import run_tool

from jigane.git.process import CommandError

ROOT = Path(__file__).resolve().parent.parent


def run_vulture(*paths: Path) -> int:
    """make lint と同じ設定（[tool.vulture]）で vulture を走らせ、終了コードを返す。"""
    try:
        run_tool([sys.executable, "-m", "vulture", *map(str, paths)], cwd=ROOT)
    except CommandError as error:
        return error.returncode or 1
    return 0


def test_an_unused_function_fails_the_check(tmp_path: Path) -> None:
    (tmp_path / "unused.py").write_text(
        "def never_called() -> None:\n    pass\n", encoding="utf-8"
    )
    assert run_vulture(tmp_path) != 0


def test_a_used_function_passes_the_check(tmp_path: Path) -> None:
    (tmp_path / "used.py").write_text(
        "def called() -> None:\n    pass\n\n\ncalled()\n", encoding="utf-8"
    )
    assert run_vulture(tmp_path) == 0


def test_the_current_tree_passes_with_the_whitelist() -> None:
    assert run_vulture() == 0


def test_the_whitelist_is_in_the_targets_with_confidence_60() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "vulture"
    ]
    assert config["min_confidence"] == 60
    assert set(config["paths"]) == {
        "jigane",
        "tests",
        "tests_posix",
        "tests_windows",
        "scripts",
        "examples",
        "vulture_whitelist.py",
    }


def test_lint_runs_vulture() -> None:
    definition = tomllib.loads((ROOT / "entrypoints.toml").read_text(encoding="utf-8"))
    lint = next(task for task in definition["task"] if task["name"] == "lint")
    assert "uv run --locked vulture" in lint["commands"]
