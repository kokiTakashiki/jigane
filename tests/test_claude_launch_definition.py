"""claude の起動の口と claude とみなす名前の定義が、三か所で食い違わないことを縛る。

三か所とは、実行時の拒否（jigane.git.process）、ast-grep の規則、scripts/check_egress.py の
LAUNCHERS である。
"""

import ast
import importlib.util
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

from ruamel.yaml import YAML

from jigane.git.process import LAUNCHABLE_EXECUTABLES, _is_claude

ROOT = Path(__file__).resolve().parent.parent
PROCESS_PATH = ROOT / "jigane" / "git" / "process.py"
CLAUDE_PATH = ROOT / "jigane" / "llm_runs" / "claude.py"
RULE_PATH = ROOT / "ast-grep" / "rules" / "no-claude-launch-outside-llm-runs.yml"
EGRESS_PATH = ROOT / "scripts" / "check_egress.py"

# 規則が拒む名前を、規則の正規表現に当てる候補。規則が拾う範囲を固定するため、
# 拾わない形も並べる。
# ast-grep-ignore: no-claude-launch-outside-llm-runs 起動ではなく、規則に当てる候補の列
RULE_CANDIDATES = ("claude", "claude.exe", "claude.py", "claudex", "claude_helper")


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_egress", EGRESS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _top_level_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _public_launchers() -> set[str]:
    """process.py の公開の起動の口。run_ で始まる公開の関数を、ast で読んで取る。"""
    return {name for name in _top_level_functions(PROCESS_PATH) if name.startswith("run_")}


def _rule() -> dict[str, Any]:
    loaded = YAML(typ="safe").load(RULE_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _nodes(node: object) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _nodes(item)


def _anchored_body(regex: str) -> str:
    return regex.removeprefix("^").removesuffix("$")


def _rule_call_names() -> set[str]:
    """pattern の呼び出し形から、規則が落とす呼び出しの名前を取る。"""
    names: set[str] = set()
    for node in _nodes(_rule()["rule"]):
        pattern = node.get("pattern")
        if isinstance(pattern, str):
            names.update(re.findall(r"(\w+)\(\$\$\$\)", pattern))
    return names


def _rule_identifier_names() -> set[str]:
    return {
        _anchored_body(node["regex"])
        for node in _nodes(_rule()["rule"])
        if node.get("kind") == "identifier"
    }


def _rule_string_regexes() -> list[str]:
    return [node["regex"] for node in _nodes(_rule()["rule"]) if node.get("kind") == "string"]


def test_rule_names_are_defined_in_code() -> None:
    identifiers = _rule_identifier_names()
    calls = _rule_call_names()

    assert "run_claude_resolved" in identifiers
    assert "claude_binary" in calls
    assert identifiers <= _top_level_functions(PROCESS_PATH)
    assert calls <= _top_level_functions(CLAUDE_PATH)


def test_rule_refused_strings_match_the_claude_name_in_code() -> None:
    regexes = _rule_string_regexes()
    assert regexes

    matched = {
        name for name in RULE_CANDIDATES if any(re.search(regex, f'"{name}"') for regex in regexes)
    }

    assert matched == {"claude", "claude.exe"}
    assert all(_is_claude([name]) for name in matched)


def test_rule_ignores_exist() -> None:
    ignores = _rule()["ignores"]
    assert ignores

    missing = [path for path in ignores if not (ROOT / path).is_file()]

    assert missing == []


def test_egress_launchers_cover_every_process_launcher() -> None:
    checker = _load_checker()
    launchers = _public_launchers()

    assert {
        "run_resolved",
        "run_command",
        "run_claude_resolved",
    } <= launchers
    assert launchers <= set(checker.LAUNCHERS)


def test_launchable_executables_match_the_egress_check() -> None:
    checker = _load_checker()

    assert LAUNCHABLE_EXECUTABLES == checker.LAUNCHABLE_EXECUTABLES
