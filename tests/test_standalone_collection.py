import sys
from pathlib import Path

import pytest
from dev_launch import run_tool

from jigane.git.process import CommandError

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
# 試験ファイルを単独で読み込む。import の順に依存する循環は、全体の収集では先に読まれた
# モジュールに隠れ、一つのファイルだけを走らせたときに初めて現れる。
LOADER = """
import importlib.util, os, sys
scripts = os.path.join(sys.argv[1], "..", "scripts")
sys.path[:0] = [sys.argv[1], scripts, os.path.dirname(sys.argv[2])]
spec = importlib.util.spec_from_file_location("standalone_test_module", sys.argv[2])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
"""
FILES = sorted(path for path in TESTS.rglob("test_*.py") if path != Path(__file__).resolve())


@pytest.mark.parametrize("path", FILES, ids=lambda path: str(path.relative_to(TESTS)))
def test_test_file_imports_on_its_own(path: Path) -> None:
    try:
        run_tool([sys.executable, "-c", LOADER, str(TESTS), str(path)], cwd=ROOT)
    except CommandError as error:
        pytest.fail(str(error)[-2000:])
