"""jigane/ の全体を走査する試験に、whole_tree の印が付いていることを縛る。

印の試験は quick-check が常に走らせる（scripts/changed_tests.py）。印が無いと、変更に関わる
試験の選び方では選ばれず、land の全体のゲートまで落ちない。

拾えるのは、次の二つが同じ最上位の試験関数（同じモジュールの補助関数を辿った先を含む）にある形。

- 走査の呼び出し。`.rglob(...)`、`.glob(...)`、`iter_modules(...)`、`walk_packages(...)`、
  `os.walk(...)`、補助の `json_users_of_stored_file(...)`
- jigane/ の根を指す名前（PACKAGE、PACKAGE_ROOT、SRC、package、package_root）か、
  `x / "jigane"`、`x.joinpath("jigane")`、パッケージの `__path__`。`walk_packages` と
  `json_users_of_stored_file` は、この指す先が無くてもそれだけで根を指すとみなす

拾えないのは、根を別名や引数で受け取って走査する形、`Path.walk()` や `os.scandir` での走査、
別のモジュールの補助関数に走査を任せる形（json_users_of_stored_file だけは名前で拾う）、
.github/ など jigane/ の外の木。
クラスのメソッドの試験も見ない。tmp_path などの一時の木を走査する試験は、
根の名前が無いので拾わない。
tmp_path を受け取る試験は、jigane/ の写しを走査するとみなして除く。
"""

import ast
from pathlib import Path

import pytest
from changed_tests import WHOLE_TREE_MARK, whole_tree_marks

TESTS = Path(__file__).resolve().parent
SCAN_METHODS = {"rglob", "glob", "iter_modules", "walk_packages", "walk"}
SCAN_FUNCTIONS = {"iter_modules", "walk_packages", "json_users_of_stored_file"}
# 呼び出しだけで jigane/ の全体を指す走査（根の名前が無くても拾う）
SELF_ROOTED = {"json_users_of_stored_file", "walk_packages"}
ROOT_NAMES = {"PACKAGE", "PACKAGE_ROOT", "SRC", "package", "package_root"}
# 印を付けずに走らせる試験と、その理由。足すときは理由を書く
UNMARKED: dict[str, str] = {
    "tests/test_package.py": "wheel を作る重い試験。CI が走らせる",
    "tests/test_standalone_collection.py": "tests/ を集め直す重い試験。jigane/ は見ない",
}


def _called_function(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _calls_scan(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr in SCAN_METHODS:
            return True
        if isinstance(func, ast.Name) and func.id in SCAN_FUNCTIONS:
            return True
    return False


def _joins_jigane(node: ast.AST) -> bool:
    """`x / "jigane"` か `x.joinpath("jigane")`。比較や文字列の検索の "jigane" は含めない。"""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        operands: list[ast.expr] = [node.right]
    elif isinstance(node, ast.Call) and _called_function(node) == "joinpath":
        operands = list(node.args)
    else:
        return False
    return any(isinstance(item, ast.Constant) and item.value == "jigane" for item in operands)


def _names_root(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in ROOT_NAMES:
            return True
        if isinstance(child, ast.Attribute) and child.attr == "__path__":
            return True
        if _joins_jigane(child):
            return True
        if isinstance(child, ast.Call) and _called_function(child) in SELF_ROOTED:
            return True
    return False


def _takes_tmp_path(node: ast.FunctionDef) -> bool:
    return any(argument.arg == "tmp_path" for argument in node.args.args)


def _called_names(node: ast.AST) -> set[str]:
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def unmarked_whole_tree_tests(source: str) -> list[str]:
    """走査と jigane/ の根の名前に至る最上位の試験関数のうち、印の無いものの名前。"""
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    reach: dict[str, tuple[bool, bool]] = {}
    for name, node in functions.items():
        reach[name] = (_calls_scan(node), _names_root(node))
    # 同じモジュールの補助関数を呼ぶ側へ、走査と根の名前を伝える。段数は関数の数で打ち切る
    for _ in functions:
        for name, node in functions.items():
            scan, root = reach[name]
            for callee in _called_names(node) & functions.keys():
                scan = scan or reach[callee][0]
                root = root or reach[callee][1]
            reach[name] = (scan, root)
    marks = whole_tree_marks(source)
    if marks == []:
        # モジュール全体に印がある
        return []
    marked = set(marks or [])
    return [
        name
        for name, (scan, root) in reach.items()
        if name.startswith("test_")
        and scan
        and root
        and name not in marked
        and not _takes_tmp_path(functions[name])
    ]


def test_every_whole_tree_test_carries_the_mark() -> None:
    missing: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        relative = f"tests/{path.name}"
        if relative in UNMARKED or path.name == Path(__file__).name:
            continue
        missing.extend(
            f"{relative}::{name}"
            for name in unmarked_whole_tree_tests(path.read_text(encoding="utf-8"))
        )
    assert not missing, (
        f"jigane/ を走査する試験に @pytest.mark.{WHOLE_TREE_MARK} が無い。"
        f"印を付けるか、理由を添えて UNMARKED に足す: {missing}"
    )


def test_unmarked_exceptions_still_exist() -> None:
    assert [name for name in UNMARKED if not (TESTS.parent / name).is_file()] == []


BODY = "def test_a():\n    PACKAGE.rglob('*')\n"
FUNCTION_MARKED = "import pytest\nPACKAGE = 1\n@pytest.mark.whole_tree\n" + BODY
MODULE_MARKED = "import pytest\nPACKAGE = 1\npytestmark = pytest.mark.whole_tree\n" + BODY


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("PACKAGE = 1\ndef test_a():\n    PACKAGE.rglob('*')\n", ["test_a"]),
        (
            "PACKAGE = 1\ndef _h():\n    return PACKAGE.rglob('*')\ndef test_a():\n    _h()\n",
            ["test_a"],
        ),
        (
            "import pkgutil\nSRC = 1\ndef test_a():\n    pkgutil.iter_modules([SRC])\n",
            ["test_a"],
        ),
        ("def test_a(tmp_path):\n    tmp_path.rglob('*')\n", []),
        ("PACKAGE = 1\ndef test_a():\n    PACKAGE.read_text()\n", []),
        (FUNCTION_MARKED, []),
        (MODULE_MARKED, []),
    ],
)
def test_the_detector_reads_each_form(source: str, expected: list[str]) -> None:
    assert unmarked_whole_tree_tests(source) == expected
