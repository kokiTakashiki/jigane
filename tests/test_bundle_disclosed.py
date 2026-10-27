"""開示済みの限界の束ねの役の起動。LLM は呼ばず、偽の起動で応答を与える。"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import PROMPT_MARKER, RECORD, d_finding, settings_for
from pydantic import ValidationError

from jigane.findings.records import StoredFinding
from jigane.llm_runs import claude
from jigane.llm_runs.finding_model import KEY_DISCLOSED_AT
from jigane.skills.bundle_disclosed import (
    Bundled,
    BundleOutcome,
    InvalidOutput,
    LaunchFailed,
    TooFewTargets,
    bundle_disclosed,
    groups_schema,
    target_positions,
)
from jigane.skills.disclosed import disclosed_as_nit
from jigane.skills.output_models import BundleGroups


def nit(number: int, place: str) -> StoredFinding:
    candidate = {
        **d_finding(),
        "claim": "限界 A",
        "severity": "nit",
        KEY_DISCLOSED_AT: place,
    }
    raw = {
        "id": f"F{number}",
        **disclosed_as_nit(candidate),
        "group": "implementation",
        "theme": None,
        "triage": None,
    }
    return StoredFinding.of_model_output(raw, None)


def plain(number: int) -> StoredFinding:
    raw = {
        "id": f"F{number}",
        **d_finding(),
        "group": "implementation",
        "theme": None,
        "triage": None,
    }
    return StoredFinding.of_model_output(raw, None)


class Fake:
    def __init__(self, output: dict[str, Any] | None) -> None:
        self.output = output
        self.prompts: list[str] = []

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        self.prompts.append(invocation.prompt)
        if self.output is None:
            raise claude.ClaudeInvocationError("boom", RECORD)
        return claude.InvocationResult(self.output, RECORD)


def run(findings: list[StoredFinding], fake: Fake) -> BundleOutcome:
    return bundle_disclosed(findings, working_directory=Path(), settings=settings_for(fake))


def ids(findings: Sequence[StoredFinding]) -> list[str]:
    return [f.body.id for f in findings]


def test_targets_are_only_disclosed_nits() -> None:
    assert target_positions([plain(1), nit(2, "a.md"), nit(3, "b.md")]) == [1, 2]


def test_fewer_than_two_targets_do_not_invoke() -> None:
    fake = Fake({"groups": []})
    findings = [plain(1), nit(2, "a.md")]
    outcome = run(findings, fake)
    assert fake.prompts == []
    assert outcome.findings == tuple(findings)
    assert isinstance(outcome, TooFewTargets)


def test_grouped_nits_become_one_finding() -> None:
    fake = Fake({"groups": [[0, 1]]})
    outcome = run([plain(1), nit(2, "a.md"), nit(3, "b.md")], fake)
    assert len(fake.prompts) == 1
    assert PROMPT_MARKER in fake.prompts[0]
    assert ids(outcome.findings) == ["F1", "F2"]
    assert isinstance(outcome, Bundled)
    assert (outcome.targets, outcome.groups, outcome.discarded_groups) == (2, 1, 0)
    assert outcome.to_dict() == {
        "targets": 2,
        "failed": False,
        "failure": None,
        "groups": 1,
        "discarded_groups": 0,
    }


def test_failed_invocation_keeps_findings() -> None:
    findings = [nit(1, "a.md"), nit(2, "b.md")]
    outcome = run(findings, Fake(None))
    assert outcome.findings == tuple(findings)
    assert isinstance(outcome, LaunchFailed)
    assert "boom" in outcome.failure
    assert outcome.to_dict()["failed"] is True


def test_schema_mismatch_keeps_findings() -> None:
    findings = [nit(1, "a.md"), nit(2, "b.md")]
    outcome = run(findings, Fake({"groups": "x"}))
    assert outcome.findings == tuple(findings)
    assert isinstance(outcome, InvalidOutput)
    assert outcome.to_dict()["failed"] is True
    assert "スキーマ" in outcome.failure


def test_invalid_groups_are_discarded() -> None:
    findings = [nit(1, "a.md"), nit(2, "b.md")]
    outcome = run(findings, Fake({"groups": [[0, 5], [1]]}))
    assert outcome.findings == tuple(findings)
    assert isinstance(outcome, Bundled)
    assert outcome.discarded_groups == 2
    assert outcome.groups == 0


def test_groups_schema_keeps_the_accepted_shape() -> None:
    assert groups_schema() == {
        "type": "object",
        "additionalProperties": False,
        "required": ["groups"],
        "properties": {
            "groups": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "integer", "minimum": 0}},
            }
        },
    }


@pytest.mark.parametrize(
    ("output", "accepted"),
    [
        ({"groups": []}, True),
        ({"groups": [[0, 1], [2]]}, True),
        ({}, False),
        ({"groups": "x"}, False),
        ({"groups": [0]}, False),
        ({"groups": [[-1]]}, False),
        ({"groups": [["0"]]}, False),
        ({"groups": [[True]]}, False),
        ({"groups": [[1.0]]}, True),
        ({"groups": [[1.5]]}, False),
        ({"groups": [], "extra": 1}, False),
    ],
)
def test_model_accepts_what_the_schema_accepts(*, output: dict[str, Any], accepted: bool) -> None:
    assert jsonschema.Draft202012Validator(groups_schema()).is_valid(output) is accepted
    try:
        BundleGroups.model_validate(output)
    except ValidationError:
        assert accepted is False
    else:
        assert accepted is True
