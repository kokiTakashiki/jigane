import ast
import inspect
import re
from pathlib import Path

import pytest

from jigane.groups.definitions import GROUPS, GroupId
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import estimator
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import (
    DiagramPolicyEstimate,
    ImplementationEstimate,
    SecurityEstimate,
)

PACKAGE = Path(__file__).parent.parent / "jigane"
READERS = (
    PACKAGE / "commands" / "consent.py",
    PACKAGE / "commands" / "subcommands" / "estimate.py",
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_every_group_maps_to_one_estimate_type(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assembly = AssemblyInput.load(review)
    expected = {
        GroupId.MACRO: DiagramPolicyEstimate,  # 機能のレビューは図の形
        GroupId.MICRO: ImplementationEstimate,
        GroupId.SECURITY: SecurityEstimate,
    }
    assert set(expected) == {group.identifier for group in GROUPS}
    for group in GROUPS:
        estimate = estimator.estimate_of(assembly, group.identifier)
        assert type(estimate) is expected[group.identifier]


def _group_branches(source: str) -> tuple[int, int, set[str]]:
    """match 文の数、`assert_never(group)` の呼び出しの数、`case GroupId.<名前>` の名前の集合。"""
    tree = ast.parse(source)
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.Match)]
    never_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assert_never"
        and [arg.id for arg in node.args if isinstance(arg, ast.Name)] == ["group"]
    ]
    cases = {
        node.value.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.MatchValue)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "GroupId"
    }
    return len(matches), len(never_calls), cases


def test_the_group_branch_lives_in_one_match_with_assert_never() -> None:
    matches, never_calls, cases = _group_branches(inspect.getsource(estimator))
    assert matches == 1
    assert never_calls == 1
    assert cases == {each.name for each in GroupId}


def test_a_group_branch_that_is_split_or_not_closed_is_found() -> None:
    closed = (
        "def f(group):\n    match group:\n        case GroupId.MACRO:\n            return 1\n"
        "        case _:\n            assert_never(group)\n"
    )
    assert _group_branches(closed) == (1, 1, {"MACRO"})
    open_ended = (
        "def f(group):\n    match group:\n        case GroupId.MACRO:\n            return 1\n"
    )
    assert _group_branches(open_ended) == (1, 0, {"MACRO"})
    assert _group_branches(closed + closed)[0] == 2
    assert (
        _group_branches("def f(group):\n    if group == GroupId.MACRO:\n        return 1\n")[0]
        == 0
    )


@pytest.mark.parametrize("path", READERS, ids=lambda path: path.name)
def test_readers_do_not_branch_on_a_group_for_the_estimate(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"estimate_(macro|micro|security)\b", text)
    assert not re.search(r"estimate\[.(planned_invocations|shard_bytes).", text)
