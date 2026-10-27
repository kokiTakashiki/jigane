import ast
from pathlib import Path

from jigane.groups.definitions import GROUPS

SRC = Path(__file__).resolve().parent.parent / "jigane"


def test_group_labels_are_defined_only_in_the_table() -> None:
    labels = {group.label for group in GROUPS}
    table = SRC / "groups" / "definitions.py"
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path == table:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found.extend(
            f"{path.relative_to(SRC)}:{node.lineno}: {node.value}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in labels
        )
    assert not found, found


def test_group_order_is_policy_implementation_security() -> None:
    assert [group.identifier for group in GROUPS] == ["macro", "micro", "security"]
