import ast
from pathlib import Path

JIGANE_DIRECTORY = Path(__file__).resolve().parent.parent / "jigane"
ENTRY_NAMES = {"load", "from_loaded"}


def _assembly_input_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ENTRY_NAMES
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "AssemblyInput"
    ]


def test_every_production_assembly_input_call_passes_docs_explicitly() -> None:
    found = 0
    missing: list[str] = []
    for path in sorted(JIGANE_DIRECTORY.rglob("*.py")):
        for call in _assembly_input_calls(ast.parse(path.read_text(encoding="utf-8"))):
            found += 1
            if "docs" not in {keyword.arg for keyword in call.keywords}:
                missing.append(f"{path.relative_to(JIGANE_DIRECTORY)}:{call.lineno}")
    assert found >= 3
    assert missing == []
