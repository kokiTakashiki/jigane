import contextlib
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fakes import settings_for
from test_security import SecurityFake, write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.subcommands.estimate import input_cost_lines
from jigane.llm_runs.claude import RunBudget, estimate_input_tokens, input_cost_usd
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import pipeline, security
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import SecurityEstimate
from jigane.skills.mcp import NO_MCP, McpSelection

NO_FINDINGS: dict[int, list[dict[str, object]] | None] = {number: [] for number in range(1, 20)}
MODEL = "m"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    for name in ("a", "b"):
        (root / "src" / f"{name}.py").write_text(
            f"# {name}\n" + "x = 1\n" * 3500, encoding="utf-8"
        )
    monkeypatch.chdir(root)
    return root


def shard_prompt_sizes(fake: SecurityFake) -> list[int]:
    sizes: dict[int, int] = {}
    for call in fake.calls:
        match = re.search(r"担当のシャード: (\d+) / (\d+)", call.prompt)
        assert match
        sizes[int(match.group(1))] = len(call.prompt.encode("utf-8"))
    return [sizes[number] for number in sorted(sizes)]


def estimate_of(
    project: Path, mcp: McpSelection = NO_MCP
) -> tuple[AssemblyInput, SecurityEstimate]:
    assembly = AssemblyInput.load(create_feature_review(project, ["src"], "p"))
    return assembly, security.estimate_security(assembly, mcp)


def biggest_shard_usd(estimate: SecurityEstimate) -> float:
    return max(
        input_cost_usd(MODEL, estimate_input_tokens(size)).usd for size in estimate.prompt_bytes
    )


@pytest.mark.parametrize("with_mcp", [False, True], ids=["without_mcp", "with_mcp"])
def test_security_estimate_prompt_sizes_match_the_prompts_the_run_sends(
    project: Path, *, with_mcp: bool
) -> None:
    if with_mcp:
        write_mcp()
    mcp = McpSelection.load(("mobile-sec", "server-sec")) if with_mcp else NO_MCP
    assembly, estimate = estimate_of(project, mcp)
    fake = SecurityFake(NO_FINDINGS)

    security.run_security(assembly, settings_for(fake, max_budget_usd=0.5), mcp)

    assert list(estimate.prompt_bytes) == shard_prompt_sizes(fake)
    assert all(
        size > shard
        for size, shard in zip(estimate.prompt_bytes, estimate.shard_bytes, strict=True)
    )


def test_security_estimate_and_pre_run_check_stop_when_a_shard_input_exceeds_half_the_limit(
    project: Path,
) -> None:
    assembly, estimate = estimate_of(project)
    # 最大のシャードの入力が上限の 0.6 倍になる上限。余裕を引く群は 0.5 倍を超えると止まる
    limit_usd = biggest_shard_usd(estimate) / 0.6
    basis = RunBudget(MODEL, limit_usd, reserve_margin=True)
    fake = SecurityFake(NO_FINDINGS)

    record = estimate.input_cost_record(basis)
    lines = input_cost_lines(estimate, basis)
    with pytest.raises(pipeline.InputBudgetError):
        security.run_security(assembly, settings_for(fake, max_budget_usd=limit_usd))

    assert record["exceeds_budget"] is True
    assert "実行は止まります" in record["budget_stop_warning"]
    assert any("実行は止まります" in line for line in lines)
    assert fake.calls == []


def test_security_estimate_does_not_stop_when_every_shard_input_is_under_half_the_limit(
    project: Path,
) -> None:
    assembly, estimate = estimate_of(project)
    limit_usd = biggest_shard_usd(estimate) / 0.4
    fake = SecurityFake(NO_FINDINGS)

    record = estimate.input_cost_record(RunBudget(MODEL, limit_usd, reserve_margin=True))
    security.run_security(assembly, settings_for(fake, max_budget_usd=limit_usd))

    assert record["exceeds_budget"] is False
    assert "budget_stop_warning" not in record
    assert fake.calls


def capture_main(argv: list[str]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert main(argv) == EXIT_OK
    return buffer.getvalue()


def estimate_prompt_bytes(review_id: str, *mcps: str) -> list[int]:
    argv = ["estimate", review_id, "security", "--json"]
    record: dict[str, Any] = json.loads(
        capture_main([*argv, *(["--mcps", *mcps] if mcps else [])])
    )
    return list(record["prompt_bytes"])


def test_estimate_command_with_mcps_matches_the_prompts_of_the_same_run_selection(
    project: Path,
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    without_mcp = estimate_prompt_bytes(str(review.id))
    with_mcp = estimate_prompt_bytes(str(review.id), "mobile-sec", "server-sec")
    fake = SecurityFake(NO_FINDINGS)

    security.run_security(
        AssemblyInput.load(review),
        settings_for(fake, max_budget_usd=0.5),
        McpSelection.load(("mobile-sec", "server-sec")),
    )

    assert with_mcp == shard_prompt_sizes(fake)
    assert all(after > before for after, before in zip(with_mcp, without_mcp, strict=True))


def test_estimate_command_rejects_mcps_for_other_groups_like_run(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    for group in ("micro", "macro"):
        code = main(["estimate", str(review.id), group, "--mcps", "mobile-sec"])
        assert code != EXIT_OK
        assert "--mcps はセキュリティの群だけが受け付けます" in capsys.readouterr().err
