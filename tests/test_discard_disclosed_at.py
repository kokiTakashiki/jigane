import pytest

from jigane.findings.discard import (
    Considered,
    DiscardedCandidate,
    DiscardKind,
    DisclosedClaim,
    DisclosedNitReason,
    ExtractedConcern,
    Nit,
    NoConcern,
    OrdinaryClaim,
    OrdinaryNitReason,
    claim_of_kind,
    discard_claim,
    nit_reason_from_json,
    nit_reason_of_claim,
    places_of,
)
from jigane.llm_runs.finding_model import KEY_DISCLOSED_AT
from jigane.skills import macro
from jigane.skills.pipeline import PolicyGroupRun

CONCERN = "保存形式の旧版を読めない点に懸念がある。"


def _nit_reason(candidate: DiscardedCandidate) -> DisclosedNitReason | OrdinaryNitReason:
    decision = candidate.decision()
    assert isinstance(decision, Nit)
    return decision.reason


def test_conclude_schema_accepts_disclosed_at_on_discarded_items() -> None:
    item = macro.conclude_schema()["properties"]["discarded"]["items"]
    field = item["properties"][KEY_DISCLOSED_AT]
    assert field["type"] == "string"
    assert field["minLength"] == 1
    assert "読点" in field["description"]
    assert KEY_DISCLOSED_AT not in item["required"]


def test_disclosed_limitation_candidate_carries_places() -> None:
    candidate = DiscardedCandidate(
        "S1",
        "README の既知の限界に書かれている。",
        DiscardKind.DISCLOSED_LIMITATION,
        CONCERN,
        "README.md の既知の限界、docs/design.md の 3.2",
    )
    reason = _nit_reason(candidate)
    assert isinstance(reason, DisclosedNitReason)
    assert reason.places == ("README.md の既知の限界", "docs/design.md の 3.2")


def test_other_kinds_drop_disclosed_at() -> None:
    candidate = DiscardedCandidate(
        "S1", "r", DiscardKind.NOT_A_CONTRACT, CONCERN, "README.md の既知の限界"
    )
    reason = _nit_reason(candidate)
    assert isinstance(reason, OrdinaryNitReason)
    assert reason.to_json()["places"] == []


def test_disclosed_at_round_trips_and_is_optional_in_stored_runs() -> None:
    with_places = DiscardedCandidate(
        "S1", "r", DiscardKind.DISCLOSED_LIMITATION, CONCERN, "README.md"
    )
    assert DiscardedCandidate.from_json(with_places.to_json()) == with_places
    assert DiscardedCandidate("S1", "r").to_json() == {"source": "S1", "reason": "r"}
    old = PolicyGroupRun.from_json(
        {"discovered": 2, "discarded": [{"source": "S1", "reason": "r"}]}
    ).considered
    assert old == Considered(2, (DiscardedCandidate("S1", "r"),))


def test_claim_sum_arms_map_to_their_nit_reason_types() -> None:
    concern = ExtractedConcern(CONCERN)
    disclosed = nit_reason_of_claim("r", DisclosedClaim(concern, ("a", "b")))
    assert disclosed == DisclosedNitReason("r", concern, ("a", "b"))
    ordinary = nit_reason_of_claim("r", OrdinaryClaim(DiscardKind.NOT_A_CONTRACT, concern))
    assert ordinary == OrdinaryNitReason(DiscardKind.NOT_A_CONTRACT, "r", concern)


def test_boundary_builds_places_only_for_a_disclosed_limitation() -> None:
    at = "README.md の既知の限界、docs/design.md の 3.2"
    disclosed = discard_claim("r", DiscardKind.DISCLOSED_LIMITATION, CONCERN, at)
    assert disclosed == DisclosedClaim(ExtractedConcern(CONCERN), places_of(at))
    for kind in DiscardKind:
        if kind is DiscardKind.DISCLOSED_LIMITATION:
            continue
        claim = discard_claim("r", kind, CONCERN, at)
        assert claim == OrdinaryClaim(kind, ExtractedConcern(CONCERN))
        assert not hasattr(claim, "places")


def test_nit_reason_json_round_trips_each_arm() -> None:
    for kind in (DiscardKind.DISCLOSED_LIMITATION, DiscardKind.OTHER):
        candidate = DiscardedCandidate("S1", "r", kind, CONCERN, "README.md、docs")
        reason = _nit_reason(candidate)
        assert nit_reason_from_json(reason.to_json()) == reason


def test_claim_of_kind_disclosed_limitation_keeps_places() -> None:
    claim = claim_of_kind(DiscardKind.DISCLOSED_LIMITATION, ExtractedConcern(CONCERN), ("A", "B"))
    assert claim == DisclosedClaim(ExtractedConcern(CONCERN), ("A", "B"))


def test_claim_of_kind_other_kind_without_places_is_ordinary() -> None:
    claim = claim_of_kind(DiscardKind.OTHER, NoConcern(), ())
    assert claim == OrdinaryClaim(DiscardKind.OTHER, NoConcern())


def test_claim_of_kind_rejects_places_on_other_kind() -> None:
    with pytest.raises(ValueError, match="開示の場所を持てません"):
        claim_of_kind(DiscardKind.OTHER, NoConcern(), ("A",))


def test_ordinary_claim_rejects_disclosed_limitation_kind() -> None:
    with pytest.raises(ValueError, match="DisclosedClaim"):
        OrdinaryClaim(DiscardKind.DISCLOSED_LIMITATION, NoConcern())
