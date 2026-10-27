import ast
from pathlib import Path

import jigane

_ROOT = Path(jigane.__file__).parent


# 文法の定義と言語の基底が registry を読まないことは、pyproject.toml の forbidden 契約が見る。
# 契約は import が関数の中かを区別できない。
# flaky の検出が関数の中で registry を読まないことは、ここで見る。
def _imports_registry(node: ast.AST) -> bool:
    match node:
        case ast.ImportFrom(module=module, names=names):
            return module == "jigane.languages.registry" or (
                module == "jigane.languages" and any(alias.name == "registry" for alias in names)
            )
        case ast.Import(names=names):
            return any(alias.name == "jigane.languages.registry" for alias in names)
        case _:
            return False


def test_flaky_detector_has_no_function_level_registry_import() -> None:
    tree = ast.parse((_ROOT / "flaky/detector.py").read_text(encoding="utf-8"))
    functions = [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    assert not [n for f in functions for n in ast.walk(f) if _imports_registry(n)]
