import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import RECORD, RECORD_ROW, MacroFake, discovered, settings_for, with_budget
from legacy_schemas import load_finding_schema
from review_fakes import with_map, with_range_source

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.figures.models import TREE_INTRO
from jigane.findings.records import triaged_dicts
from jigane.groups.definitions import DIAGRAM_SHAPE, GROUP_BY_ID, MACRO
from jigane.groups.policy_runs import PolicyRole
from jigane.judgment_map.declaration import demote_headings
from jigane.llm_runs import claude
from jigane.policy_materials import changed_files
from jigane.reviews.lifecycle import (
    DiffSource,
    PullRequestInfo,
    PullRequestOrigin,
    Review,
    create_feature_review,
)
from jigane.skills import input_budget, macro, pipeline
from jigane.skills.assembly import AssemblyInput
from jigane.technology_list import materials as technology_materials
from jigane.technology_list import tables as technology_tables
from jigane.technology_list.build import build_model

# 抜粋の本文を重ねて送っていないことを確かめるための、プロンプトの長さの上限
PROMPT_LENGTH_LIMIT = 20_000


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# Shop\n\nlead\n\n## Use\n\nbuy\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def run(review: Review, fake: MacroFake) -> macro.MacroRun:
    return macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))


def test_prompt_without_declaration(project: Path) -> None:
    (project / "README.md").unlink()
    review = create_feature_review(project, ["src"], "p")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert macro.NO_DECLARATION_TEXT in prompt
    assert "- 方式: feature" in prompt
    assert "- src/a.py" in prompt
    assert "| J1 |" in prompt


def test_prompt_with_excerpt(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert "- README.md:1-3 # Shop" in prompt
    assert "- README.md:5-7 ## Use" in prompt
    assert "buy" not in prompt
    assert "lead" not in prompt
    assert "$" not in prompt


def test_prompt_length_stays_under_command_line_limit(project: Path) -> None:
    (project / "docs").mkdir()
    (project / "docs" / "d.md").write_text(
        "".join(f"## S{i} a.py\n\n{'x' * 2000}\n\n" for i in range(40)),
        encoding="utf-8",
    )
    review = create_feature_review(project, ["src"], "p")
    assert len(review.declaration.excerpts) > 40
    assert len(macro.build_diagram_prompt(AssemblyInput.load(review))) < PROMPT_LENGTH_LIMIT


def test_prompt_with_intent(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p", intent="買い物を助ける")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert "買い物を助ける" in prompt
    assert "出典:" not in prompt


def test_normalize_drop_and_m5(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    absolute = str(project / "src" / "a.py")
    fake = MacroFake(
        [
            discovered("M2", absolute),
            discovered("M0", "/elsewhere/x.py"),
            discovered("M5"),
            discovered("M4"),
        ]
    )
    result = run(review, fake)
    assert fake.calls[0].working_directory == Path(review.repository_root)
    assert sorted(f["viewpoint"] for f in triaged_dicts(result.findings)) == ["M2", "M4"]
    assert all(f["evidence"][0]["path"] == "src/a.py" for f in triaged_dicts(result.findings))
    state = result.run_state.to_json()
    assert state["dropped_without_evidence"] == 1
    assert state["dropped_without_bot_findings"] == 1
    assert state["discovered"] == 4


def test_ids_monotonic_and_not_reused(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    first = run(review, MacroFake([discovered(), discovered("M4")]))
    assert [f["id"] for f in triaged_dicts(first.findings)] == ["F1", "F2"]
    second = run(review, MacroFake([discovered()]))
    assert [f["id"] for f in triaged_dicts(second.findings)] == ["F3"]
    saved = json.loads((review.id.directory() / "findings.json").read_text("utf-8"))
    assert saved["last_number"] == 3
    assert [f["id"] for f in saved["groups"]["macro"]] == ["F3"]
    for finding in saved["groups"]["macro"]:
        assert finding["group"] == "macro"
        assert finding["theme"] is None
        assert finding["triage"] == "must_read"


def test_invalid_schema_is_dropped(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = create_feature_review(project, ["src"], "p")
    original = pipeline.prepare_candidates

    def corrupting(
        discovered_findings: list[dict[str, Any]],
        working_directory: Path,
        *,
        has_bot_findings: bool,
        drops: pipeline.DropCounts,
    ) -> list[dict[str, Any]]:
        candidates = original(
            discovered_findings, working_directory, has_bot_findings=has_bot_findings, drops=drops
        )
        candidates[0]["severity"] = "bogus"
        return candidates

    monkeypatch.setattr(pipeline, "prepare_candidates", corrupting)
    outcome = run(review, MacroFake([discovered()]))
    assert triaged_dicts(outcome.findings) == []
    assert outcome.run_state.dropped_invalid_schema == 1


def test_run_files(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run(review, MacroFake([discovered()]))
    directory = review.id.directory()
    runs = json.loads((directory / "runs.json").read_text("utf-8"))["macro"]
    assert runs["model"] == "m"
    assert runs["shape"] == "diagram"
    assert [(i["role"], i["split_index"]) for i in runs["invocations"]] == [("diagram", None)]
    assert "failed_splits" not in runs
    assert "unverified_candidates" not in runs
    validator = jsonschema.Draft202012Validator(load_finding_schema())
    saved = json.loads((directory / "findings.json").read_text("utf-8"))
    for finding in saved["groups"]["macro"]:
        validator.validate(finding)


def test_estimate(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p", intent="abc")
    estimate = macro.estimate_macro(AssemblyInput.load(review)).to_record()
    prompt_bytes = len(macro.build_diagram_prompt(AssemblyInput.load(review)).encode("utf-8"))
    assert estimate["materials_bytes"] == prompt_bytes
    assert estimate["planned_invocations"] == 1
    assert estimate["shape"] == "diagram"
    assert estimate["discovery_input_tokens"] == claude.estimate_input_tokens(prompt_bytes)
    assert estimate["past_run"] is None
    assert "file_bytes" not in estimate
    assert "cached_input_multiplier" not in estimate
    assert "estimated_input_tokens_with_cache" not in estimate
    assert "materials_bytes_by_role" not in estimate


def test_cli_estimate(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    create_feature_review(project, ["src"], "p")
    assert main(["estimate", "shop:p", "macro", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["max_budget_usd_per_call"] == 0.5
    assert data["planned_invocations"] == 1
    assert data["shape"] == "diagram"
    assert "estimated_input_tokens_with_cache" not in data
    assert main(["estimate", "shop:p", "macro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "入力トークンの見積もり（素朴な推定）" in out
    assert "0.5 USD" in out
    # 倍率の行（×25）は出ない
    assert "×" not in out
    assert "キャッシュ込み" not in out


@pytest.mark.parametrize("command", ["run", "estimate"])
@pytest.mark.parametrize("value", ["single", "full", "diagram"])
def test_policy_shape_argument_is_an_argument_error(
    project: Path, command: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M4")])
    with pytest.raises(SystemExit) as raised:
        main([command, "shop:p", "macro", "--policy-shape", value], invoke=fake)
    assert raised.value.code == 2
    assert "--policy-shape" in capsys.readouterr().err
    assert fake.calls == []


def test_cli_run_and_show(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    key = review.map_kind.to_dict()["regions"][0]["key"]
    with_region = {**discovered("M2"), "judgment_region": key}
    fake = MacroFake([with_region, discovered("M4")])
    assert main(["run", "shop:p", "macro"], invoke=fake) == EXIT_OK
    assert len(fake.calls) == 1
    assert fake.calls[0].budget.cli_usd == 0.5
    assert fake.calls[0].model == "claude-sonnet-5-5"
    assert fake.calls[0].effort == "high"
    out = capsys.readouterr().out
    assert "実行全体の金額" in out
    assert "所要 " in out

    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    text = capsys.readouterr().out
    assert "[J1 " in text
    assert "[変更全体]" in text
    assert "F1 warning M2 claim M2" in text
    assert "根拠: src/a.py:1-2" in text
    for label in ("代案:", "擁護:", "反証:", "判定:"):
        assert label not in text

    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert shown["stale"] is False
    assert shown["group_label"] == GROUP_BY_ID[macro.GROUP].label
    assert [f["id"] for f in shown["findings"]] == ["F1", "F2"]


def test_cli_discovery_failure(project: Path) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([], fail_discovery=True)
    assert main(["run", "shop:p", "macro"], invoke=fake) == EXIT_PROCESSING_FAILURE


def test_run_failure_raises_discovery_error(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    with pytest.raises(pipeline.DiscoveryError):
        run(review, MacroFake([], fail_discovery=True))


def test_cli_run_per_call_flag_and_totals(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M4")])
    arguments = ["run", "shop:p", "macro", "--max-budget-usd-per-call", "2", "--json"]
    assert main(arguments, invoke=fake) == EXIT_OK
    output = json.loads(capsys.readouterr().out)
    assert fake.calls[0].budget.cli_usd == 2.0
    invocations = output["run"]["invocations"]
    assert output["totals"]["invocation_count"] == len(invocations)
    assert output["totals"]["cost_usd"] == sum(i["cost_usd"] for i in invocations)
    with pytest.raises(SystemExit):
        main(["run", "shop:p", "macro", "--max-budget-usd", "2"], invoke=fake)


def test_estimate_includes_past_run(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run(review, MacroFake([discovered("M4")]))
    past_run = macro.estimate_macro(AssemblyInput.load(review)).to_record()["past_run"]
    assert past_run["invocation_count"] == 1
    assert past_run["input_tokens"] == pipeline.input_token_total(RECORD)


@pytest.mark.parametrize("shape", ["single", "full"])
def test_estimate_past_run_ignores_other_shapes(project: Path, shape: str) -> None:
    review = create_feature_review(project, ["src"], "p")
    runs = {"macro": {"model": "m", "shape": shape, "invocations": [RECORD_ROW] * 4}}
    (review.id.directory() / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    assert macro.estimate_macro(AssemblyInput.load(review)).to_record()["past_run"] is None
    run(review, MacroFake([discovered("M4")]))
    stored = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))
    assert stored["macro"]["shape"] == "diagram"
    assert (
        macro.estimate_macro(AssemblyInput.load(review)).to_record()["past_run"][
            "invocation_count"
        ]
        == 1
    )


def test_diagram_estimate_ignores_a_design_run(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    design_run = {"macro": {"model": "m", "shape": "design", "invocations": [RECORD_ROW]}}
    (review.id.directory() / "runs.json").write_text(json.dumps(design_run), encoding="utf-8")
    assert macro.estimate_macro(AssemblyInput.load(review)).to_record()["past_run"] is None


def test_diff_review_prompt_carries_changes(project: Path) -> None:
    feature = create_feature_review(project, ["src"], "p")
    directory = feature.id.directory()
    (directory / "changes.diff").write_text(
        "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-removed_line\n+kept_line\n",
        encoding="utf-8",
    )
    diff_review = Review(
        feature.id,
        feature.created_at,
        feature.repository_root,
        feature.paths,
        feature.declaration,
        [],
        feature.map_kind,
        PullRequestOrigin(PullRequestInfo("u", 1, "t"), DiffSource("b", "h", "m", "x")),
    )
    prompt = macro.build_diagram_prompt(AssemblyInput.load(diff_review))
    assert "-removed_line" not in prompt
    assert "変更のツリー図" in prompt
    fake = MacroFake([discovered("M2")])
    result = macro.run_macro(
        AssemblyInput.load(diff_review), settings_for(fake, max_budget_usd=1.0)
    )
    assert triaged_dicts(result.findings)
    assert len(fake.calls) == 1


def test_prompt_without_regions_says_so(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    empty = with_map(review, {**review.map_kind.to_dict(), "regions": []})
    prompt = macro.build_diagram_prompt(AssemblyInput.load(empty))
    assert changed_files.NO_REGIONS_TEXT in prompt


def test_discovery_schema_restricts_viewpoint_to_macro(project: Path) -> None:
    schema = macro.conclude_schema()
    assert schema["properties"]["findings"]["items"]["properties"]["viewpoint"] == {
        "enum": ["M0", "M1", "M2", "M3", "M4", "M5", "M6"],
        "type": "string",
    }
    review = create_feature_review(project, ["src"], "q")
    fake = MacroFake([discovered("M4 利用者に向けたインターフェース")])
    with pytest.raises(pipeline.DiscoveryError):
        run(review, fake)


def test_show_of_group_never_run_is_a_state_not_a_failure(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    for group in ("macro", "micro", "security"):
        assert main(["show", "shop:p", group]) == EXIT_OK
        assert f"{group} は未実行です。`jigane run local/shop:p {group}` で実行してください" in (
            capsys.readouterr().out
        )
        assert main(["show", "shop:p", group, "--json"]) == EXIT_OK
        shown = json.loads(capsys.readouterr().out)
        assert shown["findings"] == []
        assert shown["run"] is None


def test_verification_is_not_run(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M2"), discovered("M4")])
    result = run(review, fake)
    assert len(fake.calls) == 1
    assert {i["role"] for i in result.run_state.to_json()["invocations"]} == {PolicyRole.DIAGRAM}
    assert [f["adjudication"] for f in triaged_dicts(result.findings)] == [None, None]
    saved = json.loads((review.id.directory() / "findings.json").read_text("utf-8"))
    assert [f["adjudication"] for f in saved["groups"]["macro"]] == [None, None]


def test_show_json_has_no_adjudication_and_run_record(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    arguments = ["run", "shop:p", "macro", "--json"]
    assert main(arguments, invoke=MacroFake([discovered("M4")])) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert shown["findings"][0]["adjudication"] is None
    assert shown["run"]["invocation_count"] == 1
    assert shown["run"]["model"]
    assert shown["run"]["elapsed_s"] >= 0


def test_materials_have_technology_section(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert "### 技術選定" in macro.build_diagram_prompt(AssemblyInput.load(review))
    text = macro.diagram_materials_text(AssemblyInput.load(review), macro.policy_sections(review))
    assert text.index("### 同じ形の箇所") < text.index("### 技術選定")


def _full_sections(review: Review) -> str:
    # 全体の表の各節。短い形の「技術選定」は、これらの節を持たない
    return "\n\n".join(technology_materials._section(s) for s in build_model(review)["sections"])


def test_diff_materials_have_technology_section(project: Path, tmp_path: Path) -> None:
    base = tmp_path / "base"
    (base / "src").mkdir(parents=True)
    (base / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (project / "src" / "a.py").write_text("import requests\n\nx = 1\n", encoding="utf-8")
    review = create_feature_review(project, ["src"], "p")
    diff_review = with_range_source(review, base)
    section = _full_sections(diff_review)
    assert "#### 使っている技術" in section
    assert "| requests | python | 言語と OS | 1 | 3 | 0 / 0 | 増えた | - |" in section
    assert technology_tables.MANIFEST_UNCHANGED_TEXT in section
    assert technology_tables.NO_BRIDGES_TEXT in section
    assert "#### 配備の下限" in section
    assert technology_tables.NO_PLATFORMS_TEXT in section


def test_technology_materials_show_deployment_minimum(project: Path) -> None:
    (project / "Package.swift").write_text("platforms: [.iOS(.v13)]\n", encoding="utf-8")
    review = create_feature_review(project, ["src"], "p")
    section = _full_sections(review)
    assert technology_tables.PLATFORMS_GUIDE_TEXT in section
    assert "| iOS | 13 | - | - | Package.swift |" in section


@pytest.mark.usefixtures("project")
def test_m6_finding_passes_schema() -> None:
    item = {**discovered("M6"), "in_scope": True, "scope_reason": "r"}
    macro.lenient_conclude_model().model_validate(
        {"findings": [item], "merged": [], "discarded": []}
    )


def test_prompt_says_no_bot_findings_without_them(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert macro.NO_BOT_FINDINGS_TEXT in macro.build_diagram_prompt(AssemblyInput.load(review))


def test_usage_row_is_the_diagram_role(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    run(review, MacroFake([discovered("M2")]))
    assert main(["usage", "shop:p", "--json"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)["rows"]
    assert [r["role"] for r in rows if r["role"] != "合計"] == ["図"]


def test_materials_put_sequence_section_between_tree_and_regions(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    text = macro.diagram_materials_text(AssemblyInput.load(review), macro.policy_sections(review))
    assert text.index("### 変更のツリー図") < text.index("### シーケンス図")
    assert text.index("### シーケンス図") < text.index("### 判断の箇所の一覧")


def test_prompt_gets_the_diagram_sections_and_not_the_code_sections(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    for name in (
        changed_files.PrebuiltSection.TREE,
        changed_files.PrebuiltSection.SEQUENCE,
        changed_files.PrebuiltSection.REGIONS,
        macro.SECTION_REPEATS,
        "技術選定",
        macro.SECTION_HISTORY,
    ):
        assert f"### {name}\n" in prompt, name
    assert "### 使われ方とテスト\n" not in prompt


def test_prompt_uses_short_technology_and_repeats_without_lines(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    sections = macro.policy_sections(review)
    assert technology_materials.short_section_text(review) in macro.build_diagram_prompt(
        AssemblyInput.load(review)
    )
    assert "#### 使っている技術" not in sections.technology
    assert "代表の行" not in sections.repeats


def test_sections_explain_themselves_in_their_first_sentence(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    sections = macro.policy_sections(review)
    assert sections.figures[changed_files.PrebuiltSection.TREE].startswith(
        f"### 変更のツリー図\n\n{TREE_INTRO}"
    )
    assert sections.technology.startswith(f"### 技術選定\n\n{technology_materials.SHORT_INTRO}")


def test_run_builds_sections_once(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = create_feature_review(project, ["src"], "p")
    real = macro.policy_sections
    calls: list[str] = []

    def counting(target: Review) -> macro.PolicySections:
        calls.append("built")
        return real(target)

    monkeypatch.setattr(macro, "policy_sections", counting)
    run(review, MacroFake([discovered("M2")]))
    assert calls == ["built"]


def test_given_sections_are_not_rebuilt(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = create_feature_review(project, ["src"], "p")
    sections = macro.policy_sections(review)
    calls: list[str] = []

    def fail(_: Review) -> macro.PolicySections:
        calls.append("built")
        return sections

    monkeypatch.setattr(macro, "policy_sections", fail)
    macro.build_diagram_prompt(AssemblyInput.load(review), sections)
    assert calls == []


def test_estimated_materials_are_inside_the_real_prompt(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p", intent="abc")
    sections = macro.policy_sections(review)
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review), sections)
    assert macro.diagram_materials_text(AssemblyInput.load(review), sections) in prompt
    assert macro.declaration_prompt_text(review) in prompt
    assert macro.background_prompt_text(review) in prompt


def test_history_section_lists_the_previous_findings(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert macro.NO_HISTORY_TEXT in macro.build_diagram_prompt(AssemblyInput.load(review))
    run(review, MacroFake([discovered("M2")]))
    line = "- 前の回 F1 M2 warning in_scope=true 区分=必読 claim M2（src/a.py:1）"
    assert line in macro.build_diagram_prompt(AssemblyInput.load(review))


def history_section(review: Review) -> str:
    return macro.history_text(AssemblyInput.load(review))


def test_history_keeps_the_first_run_when_the_second_finds_nothing(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run(review, MacroFake([discovered("M2")]))
    run(review, MacroFake([]))
    section = history_section(review)
    assert section.splitlines() == [
        "- 前の回: 指摘なし",
        "- 2 回前 F1 M2 warning in_scope=true 区分=必読 claim M2（src/a.py:1）",
    ]
    assert section in macro.build_diagram_prompt(AssemblyInput.load(review))


def test_history_orders_three_runs_newest_first_with_stable_numbers(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run(review, MacroFake([discovered("M2")]))
    run(review, MacroFake([]))
    run(review, MacroFake([discovered("M3")]))
    expected = [
        "- 前の回 F2 M3 warning in_scope=true 区分=必読 claim M3（src/a.py:1）",
        "- 2 回前: 指摘なし",
        "- 3 回前 F1 M2 warning in_scope=true 区分=必読 claim M2（src/a.py:1）",
    ]
    assert history_section(review).splitlines() == expected
    # 読むだけでは履歴は増えない
    macro.build_diagram_prompt(AssemblyInput.load(review))
    assert history_section(review).splitlines() == expected


def test_history_has_no_cap(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    many = [{**discovered("M2"), "claim": f"claim {n}"} for n in range(30)]
    run(review, MacroFake(many))
    run(review, MacroFake([]))
    section = history_section(review)
    assert len(section.splitlines()) == 31
    assert "ほか" not in section


def test_history_is_empty_text_when_nothing_was_ever_found(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run(review, MacroFake([]))
    assert history_section(review) == macro.NO_HISTORY_TEXT


def test_conclusion_out_of_scope_keeps_severity(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    final = {
        **discovered("M2"),
        "severity": "critical",
        "in_scope": False,
        "scope_reason": "最初の一手が lib/ の共通部に及ぶ",
    }
    fake = MacroFake([], output={"findings": [final], "merged": [], "discarded": []})
    result = run(review, fake)
    assert [(f["severity"], f["triage"]) for f in triaged_dicts(result.findings)] == [
        ("critical", "out_of_scope")
    ]
    assert "in_scope" not in triaged_dicts(result.findings)[0]
    assert triaged_dicts(result.findings)[0]["out_of_scope"] == {
        "paths": [],
        "reason": "最初の一手が lib/ の共通部に及ぶ",
    }
    assert "- src/a.py" in fake.prompt


def test_concluded_in_scope_wins_over_all_evidence_outside(project: Path) -> None:
    (project / "lib").mkdir()
    (project / "lib" / "b.py").write_text("x = 1\n", encoding="utf-8")
    review = create_feature_review(project, ["src"], "p")
    result = run(review, MacroFake([discovered("M2", "lib/b.py")]))
    assert [f["triage"] for f in triaged_dicts(result.findings)] == ["must_read"]


def test_in_scope_evidence_stays_must_read(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    result = run(review, MacroFake([discovered("M2")]))
    assert [f["triage"] for f in triaged_dicts(result.findings)] == ["must_read"]


def test_conclude_schema_requires_in_scope() -> None:
    item = macro.conclude_schema()["properties"]["findings"]["items"]
    assert "in_scope" in item["required"]
    assert "scope_reason" in item["required"]
    assert item["properties"]["in_scope"] == {"type": "boolean"}


def test_run_passes_no_tools_and_one_call_with_turn_limit(project: Path) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M4")])
    assert main(["run", "shop:p", "macro", "--json"], invoke=fake) == EXIT_OK
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.tools == ()
    assert call.json_schema == macro.conclude_schema()
    assert call.max_turns == DIAGRAM_SHAPE.max_turns
    assert "--max-turns" in claude.build_command(call)


def test_effort_option_overrides_group_default(project: Path) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M4")])
    assert main(["run", "shop:p", "macro", "--effort", "medium"], invoke=fake) == EXIT_OK
    assert fake.calls[0].effort == "medium"


def known_model_settings(fake: MacroFake, max_budget_usd: float) -> claude.RunSettings:
    return with_budget(
        settings_for(fake, max_budget_usd=max_budget_usd), model="claude-sonnet-5-5"
    )


def test_run_stops_before_calling_when_input_exceeds_budget(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([])
    with pytest.raises(pipeline.InputBudgetError) as raised:
        macro.run_macro(AssemblyInput.load(review), known_model_settings(fake, 0.0001))
    assert fake.calls == []
    message = str(raised.value)
    tokens = claude.estimate_input_tokens(
        len(macro.build_diagram_prompt(AssemblyInput.load(review)).encode("utf-8"))
    )
    assert f"{tokens} トークン" in message
    assert "上限 0.0001 USD" in message
    assert "大きい節: " in message
    assert message.count("バイト）") == input_budget.LARGEST_SECTION_COUNT


def test_failure_lists_largest_sections_in_descending_order() -> None:
    prompt = "### 小\nab\n### 大\n" + "x" * 100 + "\n### 中\n" + "y" * 20 + "\n### 最小\n"
    sections = input_budget.largest_sections(prompt)
    assert [entry.heading for entry in sections] == ["大", "中", "小"]
    assert sections[0].bytes > sections[1].bytes > sections[2].bytes


def test_run_continues_when_input_is_under_budget(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([])
    macro.run_macro(AssemblyInput.load(review), known_model_settings(fake, 0.5))
    assert len(fake.calls) == 1


def test_run_stops_for_model_without_price_using_highest_price(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([])
    with pytest.raises(pipeline.InputBudgetError) as raised:
        macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.0000001))
    assert fake.calls == []
    message = str(raised.value)
    assert (
        "m は単価の表に無いため、表の最高の単価（10 USD / 100 万トークン）で見積もりました"
        in message
    )


def test_run_prints_assumed_price_line_for_model_without_price(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M4")])
    assert main(["run", "shop:p", "macro", "--model", "m"], invoke=fake) == EXIT_OK
    err = capsys.readouterr().err
    assert (
        "m は単価の表に無いため、表の最高の単価（10 USD / 100 万トークン）で見積もりました" in err
    )
    assert len(fake.calls) == 1


def test_estimate_json_reports_assumed_price_for_model_without_price(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    create_feature_review(project, ["src"], "p")
    unpriced = replace(GROUP_BY_ID[MACRO], model="unpriced-model")
    monkeypatch.setitem(GROUP_BY_ID, MACRO, unpriced)
    arguments = ["estimate", "shop:p", "macro", "--max-budget-usd-per-call", "0.0001"]
    assert main([*arguments, "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["input_price_assumed"] is True
    assert data["input_price_usd_per_million_tokens"] == 10.0
    assert data["input_cost_usd"] > 0
    assert data["exceeds_budget"] is True
    assert main(arguments) == EXIT_OK
    assert "表の最高の単価（10 USD / 100 万トークン）で見積もりました" in capsys.readouterr().out


def test_cli_run_reports_input_budget_failure(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([])
    code = main(["run", "shop:p", "macro", "--max-budget-usd-per-call", "0.0001"], invoke=fake)
    assert code == EXIT_PROCESSING_FAILURE
    assert fake.calls == []
    assert "入力だけで 1 回の起動の上限に達する見込み" in capsys.readouterr().err


def test_estimate_json_marks_budget_excess(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    assert main(["estimate", "shop:p", "macro", "--json"]) == EXIT_OK
    within = json.loads(capsys.readouterr().out)
    assert within["exceeds_budget"] is False
    assert within["input_cost_usd"] > 0
    assert within["input_price_assumed"] is False
    assert (
        main(["estimate", "shop:p", "macro", "--json", "--max-budget-usd-per-call", "0.0001"])
        == EXIT_OK
    )
    exceeded = json.loads(capsys.readouterr().out)
    assert exceeded["exceeds_budget"] is True
    assert "budget_stop_warning" not in within
    assert len(exceeded["largest_sections"]) == input_budget.LARGEST_SECTION_COUNT
    assert main(["estimate", "shop:p", "macro", "--max-budget-usd-per-call", "0.0001"]) == EXIT_OK
    text_out = capsys.readouterr().out
    assert "実行は止まります" in text_out
    assert exceeded["budget_stop_warning"] in text_out


def test_declaration_headings_start_below_the_prompt_section() -> None:
    body = "## 目的\n本文\n### 詳細\n#### 細目"
    assert demote_headings(body, 4) == "#### 目的\n本文\n##### 詳細\n###### 細目"


def test_declaration_heading_depth_stops_at_six() -> None:
    assert demote_headings("# a\n###### b", 4) == "#### a\n###### b"


def test_declaration_code_blocks_keep_their_hashes() -> None:
    body = "## a\n```\n## in\n```\n~~~\n# tilde\n~~~\n## b"
    assert demote_headings(body, 4) == "#### a\n```\n## in\n```\n~~~\n# tilde\n~~~\n#### b"


def test_declaration_non_headings_are_kept() -> None:
    body = "## a\n#123 を直す\n文中の ## は残る"
    assert demote_headings(body, 4) == "#### a\n#123 を直す\n文中の ## は残る"


def test_declaration_without_headings_is_unchanged() -> None:
    assert demote_headings("#123\n本文", 4) == "#123\n本文"


def test_prompt_declaration_has_no_heading_above_the_section(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p", intent="## 目的\n本文\n### 詳細")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    start = prompt.index("### 宣言")
    body = prompt[start:].split("\n", 1)[1]
    # 宣言の節は、次の ### の見出しで終わる
    body = body.split("\n### ", 1)[0]
    marks = [line.split()[0] for line in body.splitlines() if line.startswith("#")]
    levels = [len(mark) for mark in marks]
    assert levels == [4, 5]
