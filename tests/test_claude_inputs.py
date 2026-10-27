"""claude_inputs の対応表の最小の整合の試験。"""

import ast
import re
from pathlib import Path
from string import Template

from jigane.llm_runs.claude_inputs import HEADING_INPUTS, PLACEHOLDER_INPUTS, ClaudeInput

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIRECTORY = REPOSITORY_ROOT / "jigane" / "skills" / "prompts"
SOURCE_SYMBOL = re.compile(r"(jigane/[\w/]+\.py):(\w+)")
INPUT_COUNT = 26


def _defined_names(path: Path) -> set[str]:
    names: set[str] = set()
    tree: ast.Module = ast.parse(path.read_text(encoding="utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    scopes: list[ast.Module | ast.ClassDef] = [tree, *classes]
    for scope in scopes:
        for node in scope.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def test_every_source_names_a_defined_symbol() -> None:
    for item in ClaudeInput:
        assert item.value.source, item.name
        for source in item.value.source:
            match = SOURCE_SYMBOL.fullmatch(source)
            assert match, (item.name, source)
            path = REPOSITORY_ROOT / match.group(1)
            assert path.is_file(), (item.name, source)
            assert match.group(2) in _defined_names(path), (item.name, source)


def test_claude_input_has_no_aliases() -> None:
    # 値が等しい項目は Enum が黙って別名にまとめるので、数が合うことで別名の無さを確かめる
    assert len(ClaudeInput) == len(ClaudeInput.__members__)
    assert len(ClaudeInput) == INPUT_COUNT


def test_placeholder_inputs_hold_only_claude_inputs() -> None:
    for name, inputs in PLACEHOLDER_INPUTS.items():
        assert inputs, name
        assert all(isinstance(item, ClaudeInput) for item in inputs), name


def test_heading_inputs_hold_only_claude_inputs() -> None:
    for heading, inputs in HEADING_INPUTS.items():
        assert inputs, heading
        assert all(isinstance(item, ClaudeInput) for item in inputs), heading


def _placeholders_by_prompt() -> dict[str, set[str]]:
    # 描画は adversarial.load_prompt の string.Template なので、口も Template.pattern で拾う。
    found: dict[str, set[str]] = {}
    for path in sorted(PROMPT_DIRECTORY.glob("*.md")):
        names: set[str] = set()
        for match in Template.pattern.finditer(path.read_text(encoding="utf-8")):
            assert match.group("invalid") is None, (path.name, match.group(0))
            name = match.group("named") or match.group("braced")
            if name is not None:
                names.add(name)
        found[path.name] = names
    return found


def test_every_prompt_placeholder_is_in_the_map() -> None:
    missing = sorted(
        f"{prompt}: ${name}"
        for prompt, names in _placeholders_by_prompt().items()
        for name in names
        if name not in PLACEHOLDER_INPUTS
    )
    assert not missing, missing


def test_every_mapped_placeholder_exists_in_a_prompt() -> None:
    used = set().union(*_placeholders_by_prompt().values())
    unused = sorted(set(PLACEHOLDER_INPUTS) - used)
    assert not unused, unused
