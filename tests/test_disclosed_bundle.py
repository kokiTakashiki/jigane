"""開示済みの限界の nit の懸念の文の往復と、同じ限界を指す組のまとめ。"""

from typing import Any

from fakes import d_finding

from jigane.findings.discard import DisclosedNitReason
from jigane.findings.records import StoredFinding
from jigane.llm_runs.finding_model import KEY_DISCLOSED_AT
from jigane.skills.disclosed import (
    disclosed_as_nit,
    merge_groups,
    vet_groups,
)


def nit(number: int, place: str, evidence_path: str, claim: str = "限界 A") -> StoredFinding:
    candidate = {
        **d_finding(path=evidence_path),
        "claim": claim,
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


def places_of(finding: StoredFinding) -> tuple[str, ...]:
    reason = finding.body.nit_reason
    assert isinstance(reason, DisclosedNitReason)
    return reason.places


def test_places_are_stored_as_a_list_and_the_concern_is_the_claim_only() -> None:
    stored = nit(1, "docs/x.md の節、docs/y.md の節", "src/a.py", claim="主張")
    assert places_of(stored) == ("docs/x.md の節", "docs/y.md の節")
    assert stored.body.claim == "主張"


def test_vet_drops_out_of_range_overlapping_and_single_groups() -> None:
    vetted = vet_groups([[0, 1], [1, 2], [2, 9], [3], [2, 2], [-1, 2], [2, 3]], 4)
    assert vetted.groups == ((0, 1), (2, 3))
    assert vetted.discarded == 5


def test_vet_rejects_non_integers() -> None:
    bad: list[list[Any]] = [[0, True], [0, "1"]]
    assert vet_groups(bad, 3).groups == ()


def test_merge_joins_places_and_evidence_and_keeps_the_lead() -> None:
    a = nit(1, "x.md の節", "src/a.py")
    b = nit(2, "y.md の節、x.md の節", "src/b.py", claim="限界 A の言い換え")
    c = nit(3, "z.md の節", "src/c.py", claim="別の限界")
    merged = merge_groups([a, b, c], [[0, 1]])
    assert [f.body.id for f in merged] == ["F1", "F3"]
    first = merged[0]
    assert first.body.claim == "限界 A"
    assert places_of(first) == ("x.md の節", "y.md の節")
    paths = [location["path"] for location in first.body.extra["evidence"]]
    assert paths == ["src/a.py", "src/b.py"]
    assert merged[1] is c


def test_merge_uses_the_first_element_of_the_group_as_the_lead() -> None:
    a = nit(1, "x.md の節", "src/a.py")
    b = nit(2, "y.md の節", "src/b.py")
    merged = merge_groups([a, b], [[1, 0]])
    assert [f.body.id for f in merged] == ["F2"]
    assert places_of(merged[0]) == ("y.md の節", "x.md の節")


def test_merge_without_groups_returns_the_findings_as_they_are() -> None:
    findings = [nit(1, "x.md の節", "src/a.py")]
    assert merge_groups(findings, []) == findings
