from pathlib import Path

import pytest
from fakes import MacroFake, MicroFake, clear_policy, d_finding, discovered
from test_security import SecurityFake, s_finding, write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.subcommands.run import group_defaults
from jigane.groups.definitions import DIAGRAM_SHAPE, GROUPS, MACRO, MICRO, RunShape
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review, create_feature_review


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def review_of(project: Path) -> Review:
    return create_feature_review(project, ["src"], "p")


def test_group_shapes_are_typed_and_only_the_policy_looks_up_the_diagram_shape() -> None:
    by_id = {group.identifier: group.shape for group in GROUPS}
    assert by_id[MACRO] is DIAGRAM_SHAPE
    assert all(isinstance(shape, RunShape) for shape in by_id.values())
    assert group_defaults(MACRO) is DIAGRAM_SHAPE
    assert group_defaults(MICRO) is by_id[MICRO]


def test_macro_invocation_carries_the_diagram_shape(project: Path) -> None:
    # 変更前の値を直書きで固定する。形の持ち方を変えても、CLI に渡る引数は変わらない
    review = review_of(project)
    fake = MacroFake([discovered("M2")])
    assert main(["run", str(review.id), "macro"], invoke=fake) == EXIT_OK
    (call,) = fake.calls
    assert (call.model, call.effort, call.budget.cli_usd, call.max_turns) == (
        "claude-sonnet-5-5",
        "high",
        0.5,
        2,
    )
    assert call.tools == ()
    command = claude.build_command(call)
    assert command[command.index("--max-turns") + 1] == "2"
    assert command[command.index("--effort") + 1] == "high"


def test_micro_and_security_invocations_pass_no_effort_and_no_turn_limit(project: Path) -> None:
    review = review_of(project)
    clear_policy(review)
    micro = MicroFake({1: [d_finding("D1")]})
    assert main(["run", str(review.id), "micro"], invoke=micro) == EXIT_OK
    write_mcp()
    security = SecurityFake({1: [s_finding()]})
    assert main(["run", str(review.id), "security"], invoke=security) == EXIT_OK
    calls = [*micro.calls, *security.calls]
    assert calls
    for call in calls:
        assert (call.effort, call.max_turns) == (None, None)
        command = claude.build_command(call)
        assert "--effort" not in command
        assert "--max-turns" not in command
