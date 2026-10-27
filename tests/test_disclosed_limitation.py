import json
from pathlib import Path

import pytest
from fakes import MacroFake, discovered

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings.discard import (
    DiscardedCandidate,
    DiscardKind,
    Nit,
    discard_reason_table,
)
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro, shared_rules
from jigane.skills.assembly import AssemblyInput

DISCLOSED = DiscardKind.DISCLOSED_LIMITATION
DISCLOSURE_SENTENCE = "既知の限界として書かれた論点は、指摘を消さず"
CONCERN = "保存形式の旧版を読めない点に懸念がある（README の既知の限界）。"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_disclosed_limitation_round_trips_through_its_value() -> None:
    assert DiscardKind("disclosed_limitation") is DISCLOSED
    assert DISCLOSED.label == "開示済みの限界"


def test_reason_table_lists_the_fifth_reason_with_its_kind() -> None:
    assert "| 既知の限界として開示済みの論点 | disclosed_limitation |" in discard_reason_table()


def test_disclosed_candidate_is_a_nit_and_stays_with_the_policy() -> None:
    candidate = DiscardedCandidate("S1", "README の既知の限界に書かれている。", DISCLOSED, CONCERN)
    decision = candidate.decision()
    assert isinstance(decision, Nit)
    assert decision.reason.kind is DISCLOSED
    assert decision.reason.claim == CONCERN
    assert decision.reason.sent_to_micro is False


def test_scope_rule_enters_every_discovery_prompt_once(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert shared_rules.prompt_text("scope_rule").count(DISCLOSURE_SENTENCE) == 1
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert prompt.count(DISCLOSURE_SENTENCE) == 1
    assert "五つだけです" in prompt


def test_disclosed_limitation_is_not_counted_as_must_read(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    output = {
        "findings": [{**discovered("M2"), "in_scope": True, "scope_reason": ""}],
        "merged": [],
        "discarded": [
            {
                "source": "S1",
                "reason": "README の既知の限界に書かれている。",
                "kind": "disclosed_limitation",
                "concern": CONCERN,
            }
        ],
    }
    assert main(["run", "shop:p", "macro"], invoke=MacroFake([], output=output)) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    nits = [f for f in shown["findings"] if f["viewpoint"] == "discarded"]
    assert [(f["severity"], f["triage"], f["claim"]) for f in nits] == [
        ("nit", "remaining", CONCERN)
    ]
    assert nits[0]["nit_reason"]["kind"] == "disclosed_limitation"
    assert [f["triage"] for f in shown["findings"]].count("must_read") == 1
