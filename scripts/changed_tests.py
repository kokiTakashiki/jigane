"""変更に関わる試験だけを選んで pytest で走らせる。`just quick-check` が lint のあとに呼ぶ。

基準は、main（無ければ origin/main）との merge-base からの差分と、
未コミットの変更（追跡外の新規ファイルを含む）。
選ぶのは次の三つで、一つも無ければ pytest を走らせない。

- 変わった tests/ 直下の test_*.py
- 変わった jigane/ のモジュールに対応する tests/ 直下の試験。
  jigane/a/b.py なら test_b*.py と test_a_b*.py
- 変わった jigane/ のモジュールを import している tests/ 直下の test_*.py。
  名前の対応では拾えない、別名の試験が使う側のモジュールを確かめるため

テストの全体は走らせない。全体は push して CI で確かめる。

使い方: changed_tests.py [--list]
--list は選んだ試験を出すだけで、pytest を走らせない。
pytest へ渡す追加の引数は PYTEST_ADDOPTS で足す。
"""

import ast
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from dev_launch import run_tool

from jigane.git.process import CommandError, run_command

_TOP_LEVEL_TEST_PARTS = 2  # tests とファイル名

ROOT = Path(__file__).resolve().parent.parent
BASE_BRANCHES = ("main", "origin/main")

# 引数は git の引数列。標準出力を返す
GitRunner = Callable[[list[str]], str]


def git_runner(root: Path) -> GitRunner:
    def run(args: list[str]) -> str:
        return run_command(["git", *args], cwd=root)

    return run


def find_base(git: GitRunner) -> str:
    for branch in BASE_BRANCHES:
        try:
            return git(["merge-base", "HEAD", branch]).strip()
        except CommandError:
            # その名前の枝が無い。次の候補を試す
            continue
    raise RuntimeError(f"比べる元の枝が無い: {' と '.join(BASE_BRANCHES)}")


def changed_files(git: GitRunner) -> set[str]:
    base = find_base(git)
    # 削除は --diff-filter=d で除く。消えたファイルに対応する試験は選べない
    tracked = git(["diff", "--name-only", "--diff-filter=d", "-z", base]).split("\0")
    untracked = git(["ls-files", "-z", "--others", "--exclude-standard"]).split("\0")
    return {name for name in (*tracked, *untracked) if name}


def module_stems(path: str) -> list[str]:
    """jigane/ の Python ファイルに対応する試験名の語幹。a/b.py なら b と a_b。

    パッケージの __init__.py は、パッケージの名前を末尾として扱う。
    """
    parts = list(Path(path).with_suffix("").parts[1:])
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return []
    return list(dict.fromkeys([parts[-1], "_".join(parts)]))


def module_name(path: str) -> str:
    """jigane/ の Python ファイルの点区切りの名前。__init__.py はパッケージの名前になる。"""
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_names(source: str) -> set[str]:
    """試験の本文が import する点区切りの名前。`from a import b` は a と a.b の両方を含める。

    ast で読むのは、文字列や注記に現れる名前を拾わないため。試験のファイルは数百で、
    必要になったときに一度だけ読めば足りる。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # 読めない試験は import を数えられない。名前の対応と変更の検出が拾う
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def importing_tests(modules: set[str], root: Path) -> set[str]:
    """modules のどれかを import する tests/ 直下の test_*.py。"""
    if not modules:
        return set()
    return {
        candidate.relative_to(root).as_posix()
        for candidate in (root / "tests").glob("test_*.py")
        if modules & imported_names(candidate.read_text(encoding="utf-8"))
    }


def select_tests(changed: Iterable[str], root: Path) -> list[str]:
    selected: set[str] = set()
    changed_modules: set[str] = set()
    for name in changed:
        path = Path(name)
        if path.suffix != ".py":
            continue
        if (
            path.parts[0] == "tests"
            and len(path.parts) == _TOP_LEVEL_TEST_PARTS
            and path.name.startswith("test_")
        ):
            if (root / name).is_file():
                selected.add(name)
        elif path.parts[0] == "jigane":
            changed_modules.add(module_name(name))
            for stem in module_stems(name):
                selected.update(
                    candidate.relative_to(root).as_posix()
                    for candidate in (root / "tests").glob(f"test_{stem}*.py")
                )
    selected.update(importing_tests(changed_modules, root))
    return sorted(selected)


def run_pytest(tests: list[str], root: Path) -> int:
    try:
        output = run_tool([sys.executable, "-m", "pytest", *tests], cwd=root)
    except CommandError as error:
        if error.returncode is None:
            # pytest を起動できなかった。落ちた試験の出力が無いので、そのまま上へ出す
            raise
        sys.stdout.write(error.stdout + error.stderr)
        return error.returncode
    sys.stdout.write(output)
    return 0


def main(argv: list[str]) -> int:
    tests = select_tests(changed_files(git_runner(ROOT)), ROOT)
    if not tests:
        print("変更に関わる試験は無い。pytest は走らせない。")
        return 0
    print(f"変更に関わる試験 {len(tests)} 件:")
    for test in tests:
        print(f"  {test}")
    if "--list" in argv:
        return 0
    return run_pytest(tests, ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
