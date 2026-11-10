import csv
import io
import json
from pathlib import Path

import pytest
from fakes import MacroFake, discovered, settings_for
from test_policy_field_copies import _range_review, _tree
from test_usage import invocation

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.groups.policy_runs import PolicyRole, PolicyShape, estimate_basis_row_labels
from jigane.llm_runs import pricing
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro, macro_principles_run
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import DiagramPolicyEstimate
from jigane.skills.policy_principles import principle_prompts

P1_CASE = Path(__file__).parent / "fixtures" / "policy_material" / "principle_prompts" / "p1-a06"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


@pytest.mark.usefixtures("all_principle_shapes")
def test_diagram_estimate_is_five_runs_and_the_input_is_the_sum(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assembly = AssemblyInput.load(review)
    estimate = macro_principles_run.estimate_policy(assembly).to_record()
    prompts = [macro.build_diagram_prompt(assembly)] + [
        item.prompt for item in principle_prompts(assembly)
    ]
    sizes = [len(prompt.encode("utf-8")) for prompt in prompts]
    assert estimate["planned_invocations"] == 5
    assert estimate["materials_bytes"] == sum(sizes)
    assert estimate["discovery_input_tokens"] == sum(
        pricing.billed_input_tokens(size) for size in sizes
    )


@pytest.mark.usefixtures("all_principle_shapes")
def test_consent_line_shows_five_discovery_runs(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    assert main(["estimate", "shop:p", "macro"]) == EXIT_OK
    assert "5 回" in capsys.readouterr().out


@pytest.mark.usefixtures("all_principle_shapes")
def test_estimate_text_shows_the_principle_runs_and_the_sum_of_all_runs(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    assert main(["estimate", "shop:p", "macro"]) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    index = next(i for i, line in enumerate(out) if line.startswith("1 回の起動の目安"))
    assert out[index + 1] == ("原則の起動: claude-haiku-5-5 4 回（1 回の目安 0.04 USD）")
    assert out[index + 2] == "全起動の和の目安（超えることがある）: 0.52 USD"
    assert "principle" not in "".join(out)


def test_estimate_json_gains_no_principle_key(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    assert main(["estimate", "shop:p", "macro", "--json"]) == EXIT_OK
    assert not [key for key in json.loads(capsys.readouterr().out) if "principle" in key]


def test_principle_rows_stay_out_of_the_estimate_basis() -> None:
    assert PolicyRole.PRINCIPLE in PolicyShape.DIAGRAM.roles
    labels = estimate_basis_row_labels()
    assert PolicyRole.DIAGRAM.label in labels
    assert PolicyRole.PRINCIPLE.label not in labels


def test_usage_shows_a_principle_row(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    runs = {
        "macro": {
            "model": "m",
            "shape": "diagram",
            "invocations": [invocation("diagram"), invocation("principle", split_index=1)],
        }
    }
    (review.id.directory() / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    assert main(["usage", str(review.id), "--csv"]) == EXIT_OK
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert PolicyRole.PRINCIPLE.label in [r["role"] for r in rows]


def test_previous_findings_section_reaches_every_principle_prompt(tmp_path: Path) -> None:
    review = _range_review(tmp_path, _tree(P1_CASE / "before"), _tree(P1_CASE / "after"))
    for item in principle_prompts(AssemblyInput.load(review)):
        assert f"### {macro.SECTION_HISTORY}" in item.prompt


def test_previous_principle_finding_reaches_every_principle_prompt(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("P2")])
    macro_principles_run.run_policy(AssemblyInput.load(review), settings_for(fake))
    for item in principle_prompts(AssemblyInput.load(review)):
        section = item.prompt.split(f"### {macro.SECTION_HISTORY}\n\n", 1)[1].split("\n### ", 1)[0]
        assert "- 前の回 F1 P2 warning" in section
        assert "claim P2（src/a.py:1）" in section


@pytest.mark.usefixtures("all_principle_shapes")
def test_policy_estimate_keeps_the_principle_runs_input_tokens_apart(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assembly = AssemblyInput.load(review)
    estimate = macro_principles_run.estimate_policy(assembly)
    prompts = [macro.build_diagram_prompt(assembly)] + [
        item.prompt for item in principle_prompts(assembly)
    ]
    sizes = [pricing.billed_input_tokens(len(p.encode("utf-8"))) for p in prompts]
    assert isinstance(estimate, DiagramPolicyEstimate)
    assert estimate.principle_input_tokens == tuple(
        zip(("P1", "P2", "P3", "P4"), sizes[1:], strict=True)
    )
    assert estimate.discovery_input_tokens == sum(sizes)
