"""preflight が make のゲートの最初で、ほかの検査に進ませないことを確かめる。

make に頼るので POSIX だけの置き場に置く。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
MAKE = shutil.which("make")
UV = shutil.which("uv")


@pytest.fixture
def unprepared_tree(tmp_path: Path) -> Path:
    # .venv の無い木。Makefile と台本だけを写す
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "Makefile", tree / "Makefile")
    shutil.copy(ROOT / "scripts" / "check_setup.py", tree / "scripts" / "check_setup.py")
    return tree


@pytest.mark.skipif(MAKE is None or UV is None, reason="make と uv が要る")
@pytest.mark.parametrize("gate", ["quick-check", "check"])
def test_gate_runs_preflight_before_any_other_step(gate: str) -> None:
    assert MAKE is not None
    dry_run = subprocess.run(
        [MAKE, "-n", "-C", str(ROOT), gate], capture_output=True, text=True, check=True
    )
    commands = [line for line in dry_run.stdout.splitlines() if "uv run" in line]
    assert "check_setup.py" in commands[0]


@pytest.mark.skipif(MAKE is None or UV is None, reason="make と uv が要る")
@pytest.mark.parametrize("gate", ["quick-check", "check"])
def test_unprepared_tree_stops_before_the_other_steps(gate: str, unprepared_tree: Path) -> None:
    assert MAKE is not None
    result = subprocess.run(
        [MAKE, "-C", str(unprepared_tree), gate], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "作業ツリーが setup されていない" in result.stderr
    # 後ろの段が走っていれば、ruff などの出力か .venv の作成が残る
    assert "ruff" not in result.stdout
    assert not (unprepared_tree / ".venv").exists()
