import json
import re
from pathlib import Path

import pytest
from fakes import MicroFake, clear_policy, d_finding, settings_for
from markdown_support import items, section_prose
from shard_prompt_support import shard_prompt

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_INCOMPLETE_COVERAGE, EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.findings.records import FINDINGS_FILE, triaged_dicts
from jigane.groups.definitions import MICRO
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import micro, shards
from jigane.skills.assembly import AssemblyInput


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    for name in ("a", "b"):
        (root / "src" / f"{name}.py").write_text(
            f"# {name}\n" + "x = 1\n" * 3500, encoding="utf-8"
        )
    (root / "src" / "gen.lock").write_text("generated\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def run(review: Review, fake: MicroFake) -> micro.MicroRun:
    clear_policy(review)
    return micro.run_micro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))


def shard_paths(review: Review) -> list[list[str]]:
    return [
        s.paths for s in shards.assign_shards(shards.build_units(review), shards.FullTextShard)
    ]


def test_all_shards_invoked_and_scoped(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert shard_paths(review) == [["src/a.py"], ["src/b.py"]]
    fake = MicroFake({1: [d_finding("D1", "src/a.py")], 2: [d_finding("T2", "src/b.py")]})
    result = run(review, fake)
    assert sorted(fake.shard_prompts) == [1, 2]
    assert "# a" in fake.shard_prompts[1]
    assert "gen.lock" not in fake.shard_prompts[1]
    assert sorted(f["viewpoint"] for f in triaged_dicts(result.findings)) == ["D1", "T2"]
    assert all(f["group"] == "micro" for f in triaged_dicts(result.findings))
    state = result.run_state.to_json()
    assert (state["shards"], state["succeeded_shards"]) == (2, 2)
    assert (state["target_files"], state["reviewed_files"]) == (2, 2)


def test_evidence_outside_shard_is_dropped(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    mixed = d_finding("D1", "src/b.py")
    mixed["evidence"].append({"path": "src/a.py", "start_line": 1, "end_line": 2})
    fake = MicroFake({1: [d_finding("D1", "src/b.py"), mixed], 2: []})
    result = run(review, fake)
    assert len(triaged_dicts(result.findings)) == 1
    assert result.run_state.dropped_outside_shard == 1


def test_viewpoint_outside_micro_is_schema_failure(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [d_finding("M2", "src/a.py")], 2: []})
    result = run(review, fake)
    assert result.failed_shards == 1
    assert result.run_state.succeeded_shards == 1


def test_viewpoints_match_the_prompt_viewpoint_lines() -> None:
    prompt = (
        Path(micro.__file__).parent.joinpath("prompts", "micro.md").read_text(encoding="utf-8")
    )
    listed = [item.split(maxsplit=1)[0] for item in items(prompt) if re.match(r"[DT]\d+ ", item)]
    assert tuple(listed) == micro.VIEWPOINTS


def test_partial_shard_failure(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [d_finding("D1", "src/a.py")], 2: None})
    result = run(review, fake)
    assert result.failed_shards == 1
    assert len(triaged_dicts(result.findings)) == 1
    state = result.run_state.to_json()
    assert (state["shards"], state["succeeded_shards"]) == (2, 1)
    assert (state["target_files"], state["reviewed_files"]) == (2, 1)
    runs = json.loads((review.id.directory() / "runs.json").read_text(encoding="utf-8"))
    assert runs["micro"]["reviewed_files"] == 1


def test_cli_exit_codes(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    name = str(review.id)
    partial = MicroFake({1: [d_finding("D1", "src/a.py")], 2: None})
    assert main(["run", name, "micro"], invoke=partial) == EXIT_INCOMPLETE_COVERAGE
    none = MicroFake({1: None, 2: None})
    assert main(["run", name, "micro"], invoke=none) == EXIT_PROCESSING_FAILURE
    ok = MicroFake({1: [], 2: []})
    assert main(["run", name, "micro"], invoke=ok) == EXIT_OK
    capsys.readouterr()


def test_sharded_run_report_shows_elapsed_time(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    capsys.readouterr()
    assert main(["run", str(review.id), "micro"], invoke=MicroFake({1: [], 2: []})) == EXIT_OK
    assert "所要 " in capsys.readouterr().out


def test_numbers_continue_and_show(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    name = str(review.id)
    fake = MicroFake(
        {1: [d_finding("D8", "src/a.py"), d_finding("D1", "src/a.py")], 2: []},
    )
    assert main(["run", name, "micro"], invoke=fake) == EXIT_OK
    first = json.loads((review.id.directory() / "findings.json").read_text(encoding="utf-8"))
    assert [f["id"] for f in first["groups"]["micro"]] == ["F1", "F2"]
    assert main(["run", name, "micro"], invoke=fake) == EXIT_OK
    second = json.loads((review.id.directory() / "findings.json").read_text(encoding="utf-8"))
    assert [f["id"] for f in second["groups"]["micro"]] == ["F3", "F4"]
    capsys.readouterr()
    assert main(["show", name, "micro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[B3 claim D8]" in out or "[B3 claim D1]" in out
    assert "実装の指摘: 2 件" in out


def test_estimate(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert main(["estimate", str(review.id), "micro", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert len(data["shard_bytes"]) == 2
    assert data["target_files"] == 2
    assert data["max_budget_usd_per_call"] == 0.5


def test_show_without_run_is_a_state(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert main(["show", str(review.id), "micro"]) == EXIT_OK


def test_findings_always_carry_adjudication(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    result = run(review, MicroFake({1: [d_finding("D1")], 2: []}))
    assert triaged_dicts(result.findings)
    assert all(f["adjudication"] is not None for f in triaged_dicts(result.findings))


def test_discovery_prompt_has_the_history_section(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    shard = shards.assign_shards(shards.build_units(review), shards.FullTextShard)[0]
    prompt = shard_prompt(MICRO, AssemblyInput.load(review), shard, 1, 2)
    assert "### 前の回の指摘\n\n前の回の指摘は無い。" in prompt


RULE_OPENINGS = (
    "「前の回の指摘」にあるものと同じ論点を出し直すときは",
    "前の回の指摘の直し方そのものへの新しい候補",
)


def write_previous_micro_finding(review: Review, path: str) -> None:
    stored = {
        "last_number": 1,
        "groups": {
            "micro": [
                {
                    "id": "F1",
                    "viewpoint": "D1",
                    "severity": "warning",
                    "claim": "前の回の主張",
                    "evidence": [{"path": path, "start_line": 3, "end_line": 4}],
                    "triage": "must_read",
                    "adjudication": None,
                }
            ]
        },
    }
    (review.id.directory() / FINDINGS_FILE).write_text(json.dumps(stored), encoding="utf-8")


def discovery_prompts(review: Review) -> list[str]:
    planned = shards.assign_shards(shards.build_units(review), shards.FullTextShard)
    assembly = AssemblyInput.load(review)
    return [
        shard_prompt(MICRO, assembly, shard, number, len(planned))
        for number, shard in enumerate(planned, start=1)
    ]


def test_previous_round_rules_are_absent_without_previous_findings(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    for prompt in discovery_prompts(review):
        assert not any(opening in prompt for opening in RULE_OPENINGS)


def test_previous_round_rules_follow_only_the_shard_with_previous_findings(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_previous_micro_finding(review, "src/a.py")
    with_history, without_history = discovery_prompts(review)
    assert all(opening in with_history for opening in RULE_OPENINGS)
    assert not any(opening in without_history for opening in RULE_OPENINGS)
    assert "前の回の主張" in with_history


def _ranges(prefix: str) -> list[str]:
    numbers = [int(item[1:]) for item in micro.VIEWPOINTS if item.startswith(prefix)]
    runs: list[list[int]] = []
    for number in numbers:
        if runs and runs[-1][-1] + 1 == number:
            runs[-1].append(number)
        else:
            runs.append([number])
    return [f"{prefix}{run[0]}〜{prefix}{run[-1]}" for run in runs]


def test_readme_lists_the_viewpoints_of_micro() -> None:
    readme = (Path(micro.__file__).parents[2] / "README.md").read_text(encoding="utf-8")
    assert f"実装（{'、'.join(_ranges('D') + _ranges('T'))}）" in section_prose(
        readme, "## 実装済みの機能"
    )
