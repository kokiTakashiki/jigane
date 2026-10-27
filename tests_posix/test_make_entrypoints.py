"""make の入口を起動する試験。make と sh に頼るので、POSIX だけの置き場に置く。

既定の testpaths の外に置き、macOS と Linux のゲート（Makefile の test）だけが走らせる。
Windows の入口は just で、その入口の試験は tests と tests_windows にある。
"""

import os
import shutil
from pathlib import Path

from dev_launch import run_tool

ROOT = Path(__file__).parent.parent
OUTER_VIRTUAL_ENV = "/nonexistent/outer/.venv"
VIRTUAL_ENV_WARNING = "does not match the project environment path"


def run_entrypoint(tool: str, args: list[str], cwd: Path) -> str:
    executable = shutil.which(tool)
    assert executable is not None, f"入口の検査には {tool} が要る"
    assert shutil.which("uv") is not None, "入口の検査には uv が要る"
    return run_tool([executable, *args], cwd=cwd)


def run_with_outer_virtual_env(args: list[str]) -> str:
    # 外で有効にした仮想環境を模す。存在しない場所なので、uv が渡されたら必ず食い違う。
    # run_tool は成功時に標準エラーを返さないため、sh で標準出力へ寄せる
    env = {**os.environ, "VIRTUAL_ENV": OUTER_VIRTUAL_ENV}
    merged = ["sh", "-c", 'exec "$@" 2>&1', "sh", *args]
    return run_tool(merged, cwd=ROOT, env=env)


def test_make_runs_jigane_from_another_directory(tmp_path: Path) -> None:
    output = run_entrypoint(
        "make", ["-f", str(ROOT / "Makefile"), "jigane", "ARGS=--help"], cwd=tmp_path
    )
    assert "usage: jigane" in output


def test_make_passes_quoted_args_to_the_shell_unchanged(tmp_path: Path) -> None:
    # make は ARGS を文字どおりに展開して sh に渡す。引用は sh が読むので、
    # 空白を含む値は 1 引数のまま届く
    output = run_entrypoint(
        "make",
        ["-n", "-f", str(ROOT / "Makefile"), "jigane", "ARGS=new files src --intent 'a b'"],
        cwd=tmp_path,
    )
    [line] = [text for text in output.splitlines() if " jigane " in text]
    assert line.endswith("jigane new files src --intent 'a b'")


def test_make_jigane_ignores_the_current_directory_in_pythonpath(tmp_path: Path) -> None:
    # PYTHONPATH の空の要素はカレントディレクトリを指す。そこに偽の jigane を置いても読まれない
    make = shutil.which("make")
    assert make is not None, "入口の検査には make が要る"
    package = tmp_path / "jigane"
    package.mkdir()
    (package / "__init__.py").write_text('raise SystemExit("wrong jigane")\n', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": ":"}
    merged = [
        "sh",
        "-c",
        'exec "$@" 2>&1',
        "sh",
        make,
        "-f",
        str(ROOT / "Makefile"),
        "jigane",
        "ARGS=--help",
    ]
    output = run_tool(merged, cwd=tmp_path, env=env)
    assert "usage: jigane" in output
    assert "wrong jigane" not in output


def test_make_jigane_runs_without_the_virtual_env_warning() -> None:
    # fmt は作業ツリーを書き換え、lint と test は重いので走らせない。unexport は Makefile 全体に
    # 掛かるので、uv を呼ぶ最も軽い入口で確かめれば足りる
    make = shutil.which("make")
    assert make is not None, "入口の検査には make が要る"
    output = run_with_outer_virtual_env([make, "jigane", "ARGS=--help"])
    assert "usage: jigane" in output
    assert VIRTUAL_ENV_WARNING not in output
