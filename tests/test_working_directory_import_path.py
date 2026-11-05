"""作業ディレクトリのファイルが標準のモジュールを覆わないことの試験。

レビュー対象のリポジトリを作業ディレクトリにして起動する。PYTHONPATH の空の項目や `python -m` の
先頭の項目は作業ディレクトリを指し、PYTHONSAFEPATH でも空の項目は外れない。
"""

import os
import sys
from pathlib import Path

import pytest
from dev_launch import run_tool

# 空の項目の位置（先頭、途中、末尾）と、値全体が空の形
PYTHONPATH_VALUES = [":/nonexistent/a", "/nonexistent/a::/nonexistent/b", "/nonexistent/a:", ""]
OVERRIDE_MARKER = "OVERRIDDEN-STANDARD-MODULE"
# email は jigane が import する標準のモジュール。glob と fnmatch は pathlib が読むモジュールで、
# jigane/__init__.py が pathlib を import していた版では覆われて落ちた。
# functools と operator は仮想環境の起動処理が jigane より先に読むので、対象にしない
SHADOWED_MODULES = ["email", "glob", "fnmatch"]


def installed_entry() -> str:
    name = "jigane.exe" if os.name == "nt" else "jigane"
    return str(Path(sys.executable).parent / name)


@pytest.mark.parametrize("pythonpath", PYTHONPATH_VALUES)
@pytest.mark.parametrize("safe_path_env", [{}, {"PYTHONSAFEPATH": "1"}])
@pytest.mark.parametrize("entry", ["module", "installed"])
@pytest.mark.parametrize("module_name", SHADOWED_MODULES)
def test_repository_file_does_not_shadow_standard_module(
    tmp_path: Path, pythonpath: str, safe_path_env: dict[str, str], entry: str, module_name: str
) -> None:
    # 標準のモジュールと同じ名前のファイルを、レビュー対象のリポジトリに置く
    (tmp_path / f"{module_name}.py").write_text(
        f"print({OVERRIDE_MARKER!r})\nraise SystemExit(7)\n", encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}
    env["PYTHONPATH"] = pythonpath
    env.update(safe_path_env)
    argv = (
        [sys.executable, "-m", "jigane", "--version"]
        if entry == "module"
        else [installed_entry(), "--version"]
    )
    output = run_tool(argv, cwd=tmp_path, env=env)
    assert output.startswith("jigane ")
    assert OVERRIDE_MARKER not in output
