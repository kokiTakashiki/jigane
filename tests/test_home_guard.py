"""試験の始めの見張りが、本物のデータの置き場を指す JIGANE_HOME で pytest を止めること。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


def _run_child(
    tmp_path: Path, jigane_home: str | None, xdg: Path
) -> subprocess.CompletedProcess[str]:
    """見張りだけを載せた子の pytest を起こす。既定の置き場は一時の場所へ差し替える。"""
    project = tmp_path / "child"
    project.mkdir(exist_ok=True)
    (project / "conftest.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(TESTS)!r})\n"
        "from shared_fixtures import pytest_configure\n",
        encoding="utf-8",
    )
    (project / "test_noop.py").write_text("def test_noop():\n    pass\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in {"JIGANE_HOME", "PYTEST_ADDOPTS"}}
    env["XDG_DATA_HOME"] = str(xdg)
    env["LOCALAPPDATA"] = str(xdg)
    if jigane_home is not None:
        env["JIGANE_HOME"] = jigane_home
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(project)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def fake_default(tmp_path: Path) -> Path:
    """差し替えた既定の置き場の親。既定の置き場は、この下の jigane。"""
    return tmp_path / "xdg"


def test_unset_stops(tmp_path: Path, fake_default: Path) -> None:
    result = _run_child(tmp_path, None, fake_default)
    assert result.returncode == 4
    assert "JIGANE_HOME" in result.stdout + result.stderr
    assert "1 passed" not in result.stdout


def test_empty_stops(tmp_path: Path, fake_default: Path) -> None:
    assert _run_child(tmp_path, "", fake_default).returncode == 4


def test_default_home_stops(tmp_path: Path, fake_default: Path) -> None:
    result = _run_child(tmp_path, str(fake_default / "jigane"), fake_default)
    assert result.returncode == 4


def test_below_default_home_stops(tmp_path: Path, fake_default: Path) -> None:
    result = _run_child(tmp_path, str(fake_default / "jigane" / "sub" / ".." / "x"), fake_default)
    assert result.returncode == 4


def test_temporary_home_runs(tmp_path: Path, fake_default: Path) -> None:
    result = _run_child(tmp_path, str(tmp_path / "other"), fake_default)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
