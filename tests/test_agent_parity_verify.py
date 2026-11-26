"""合成差分で両エージェントの検証資料と検証の順序を比較する。"""

from dataclasses import replace
from pathlib import Path

import pytest
from fakes import (
    LOCATION,
    RECORD,
    THEMES_MARKER,
    FakeInvoke,
    d_finding,
    embedded_json,
    settings_for,
)
from test_adversarial import make_candidate
from test_verifier_changes import synthetic_range

from jigane.groups.definitions import MICRO, SECURITY, GroupId
from jigane.judgment_map.changes import CHANGES_FILE
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review
from jigane.skills import adversarial, micro, security
from jigane.skills.assembly import AssemblyInput
from jigane.skills.discovery_errors import DiscoveryError

__all__ = ["synthetic_range"]


@pytest.mark.parametrize("group", [MICRO, SECURITY])
def test_diff_verification_uses_same_batches_and_materials(
    synthetic_range: Review, group: GroupId
) -> None:
    candidate = d_finding("D1" if group == MICRO else "S-CODE", "a.py")
    findings = [
        {**candidate, "claim": f"synthetic finding {i}", "references": ["synthetic reference"]}
        for i in range(11)
    ]

    def record(*, allow_tools: bool) -> list[str]:
        fake = FakeInvoke()

        def invoke(call: claude.Invocation) -> claude.InvocationResult:
            if call.discovery_group is not None:
                return claude.InvocationResult({"findings": findings}, RECORD)
            if THEMES_MARKER in call.prompt:
                return claude.InvocationResult({"themes": []}, RECORD)
            assert call.tools == (("Read",) if allow_tools else ())
            assert not call.reference_mcp
            assert not call.mcp_configs
            assert not call.allowed_mcp_tools
            return fake(call)

        run = micro.run_micro if group == MICRO else security.run_security
        settings = replace(settings_for(invoke), allow_tools=allow_tools)
        result = run(AssemblyInput.load(synthetic_range), settings)
        assert result.failed_splits == 0
        expected = ["rebuttal", "judge", "rebuttal", "judge"]
        assert [row.role for row in result.run_state.invocations or ()][: 1 + len(expected)] == [
            "discovery",
            *expected,
        ]
        return [call.prompt for call in fake.calls]

    claude_bodies = record(allow_tools=True)
    assert record(allow_tools=False) == claude_bodies
    assert [len(embedded_json(body, "候補")) for body in claude_bodies[:4]] == [10, 10, 1, 1]
    for body in claude_bodies:
        assert "+++ b/a.py" in body
        assert "- removed.py\n" in body
        assert not any(marker in body for marker in ("sample-old.pem", "fixture after rename"))


def test_static_check_keeps_other_candidates_in_the_same_batch(tmp_path: Path) -> None:
    unlisted = {**LOCATION, "path": "absent.py"}
    candidates = [
        make_candidate(claim="unconfirmed line"),
        make_candidate(claim="confirmed line"),
        make_candidate("M0", claim="alternative"),
    ]
    fake = FakeInvoke()

    def invoke(call: claude.Invocation) -> claude.InvocationResult:
        if "反証役です" in call.prompt:
            return claude.InvocationResult(
                {
                    "items": [
                        {"index": 0, "challenge": "outside evidence", "checked": [unlisted]},
                        {"index": 1, "challenge": "present evidence", "checked": [LOCATION]},
                    ]
                },
                RECORD,
            )
        return fake(call)

    result = adversarial.verify(
        candidates,
        group=MICRO,
        changes="fixture line 1\nfixture line 2",
        working_directory=tmp_path,
        settings=replace(settings_for(invoke), allow_tools=False),
        presented_lines={"a.py": {1: "fixture line 1", 2: "fixture line 2"}},
    )

    assert [row.role for row in result.invocations] == [
        "rebuttal",
        "judge",
        "alternative",
        "defense",
        "judge",
    ]
    assert [finding.candidate for finding in result.findings] == candidates[1:]
    [unverified] = result.unverified
    assert unverified.candidate == candidates[0]
    assert unverified.reason is adversarial.UnverifiedReason.MATERIALS_MISSING
    assert "absent.py" not in fake.calls[0].prompt


def test_invalid_stored_diff_stops_before_static_discovery(synthetic_range: Review) -> None:
    saved = synthetic_range.id.directory() / CHANGES_FILE
    saved.write_text("synthetic invalid diff\n", encoding="utf-8")

    def unexpected_launch(_call: claude.Invocation) -> claude.InvocationResult:
        pytest.fail("差分を照合できない場合は起動しない")

    settings = replace(settings_for(unexpected_launch), allow_tools=False)
    with pytest.raises(DiscoveryError, match="ファイルの見出しが見つからない"):
        micro.run_micro(AssemblyInput.load(synthetic_range), settings)
