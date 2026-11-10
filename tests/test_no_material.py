import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from design_materials_support import work
from fakes import MacroFake, as_run, settings_for
from group_state_support import seed_group
from test_design_materials import DOCUMENT

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.figures.models import NO_FILES_TEXT
from jigane.findings.records import RUNS_FILE
from jigane.groups.definitions import MACRO
from jigane.policy_materials.changed_files import PrebuiltSection
from jigane.policy_materials.design_doc import design_materials
from jigane.policy_materials.missing import (
    UNKNOWN_MISSING_TEXT,
    MissingMaterial,
    RecordedMissing,
    recorded_missing,
)
from jigane.reviews.lifecycle import DeclaredInputs, create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput
from jigane.skills.group_result import (
    NO_MATERIAL_SUFFIX,
    HasFindings,
    NoFindings,
    NoMaterial,
    group_outcome,
    outcome_line,
)
from jigane.skills.pipeline import PolicyGroupRun

MISSING = (MissingMaterial.DOCUMENT, MissingMaterial.CURRENT_SHAPE)
MISSING_VALUES = ["document", "current_shape"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "home"))
    return root


def _without_figures(sections: macro.PolicySections) -> macro.PolicySections:
    tree = PrebuiltSection.TREE
    emptied = {**sections.figures, tree: f"### {tree}\n\n{NO_FILES_TEXT}"}
    return replace(sections, figures=emptied)


def _show_json(capsys: pytest.CaptureFixture[str], group: str) -> dict[str, Any]:
    assert main(["show", "shop:p", group, "--json"]) == EXIT_OK
    document: dict[str, Any] = json.loads(capsys.readouterr().out)
    return document


def test_no_material_is_a_state_apart_from_no_findings() -> None:
    stored = {"discovered": 0, "discarded": [], "no_material": MISSING_VALUES}
    assert group_outcome(as_run(stored, policy=True), 0) == NoMaterial(RecordedMissing(MISSING))
    assert group_outcome(as_run({"discovered": 0, "discarded": []}), 0) == NoFindings(0, 0)
    # 空の記録は、材料なしの印にならない
    assert group_outcome(as_run({**stored, "no_material": []}, policy=True), 0) == NoFindings(0, 0)
    assert group_outcome(as_run(stored, policy=True), 1) == HasFindings()


def test_design_materials_without_anything_become_no_material(tmp_path: Path) -> None:
    materials = design_materials(work(tmp_path), "docs/d.md", "", [])
    outcome = group_outcome(
        as_run({"no_material": [kind.value for kind in materials.missing]}, policy=True), 0
    )
    assert outcome == NoMaterial(RecordedMissing(materials.missing))
    assert len(outcome.missing.known) == 4
    # 本文があれば、図や名指しが空でも材料なしにならない
    some = design_materials(work(tmp_path), "docs/d.md", DOCUMENT, [])
    assert not some.is_empty


def test_show_renders_no_material_in_every_exit(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    seed_group(
        review, MACRO, [], {"discovered": 0, "discarded": [], "no_material": MISSING_VALUES}
    )
    body = _show_json(capsys, "macro")
    assert body["result"] == "no_material"
    assert body["no_material"] == MISSING_VALUES
    assert body["no_material_texts"] == [
        "設計書の本文が渡っていません",
        "いまの形の図が描けません",
    ]
    assert body["result_title"] == "材料なし"
    expected_note = (
        "（設計書の本文が渡っていません、いまの形の図が描けません。指摘が無かったとは言えません）"
    )
    assert body["result_note"] == expected_note
    assert body["result_note_without_count"] == expected_note

    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    text = capsys.readouterr().out
    assert f"材料なし{expected_note}" in text
    assert "指摘なし" not in text


def test_code_scope_without_figures_and_declaration_is_missing_material(project: Path) -> None:
    (project / "README.md").write_text("# Shop\n\nlead\n", encoding="utf-8")
    with_declaration = create_feature_review(project, ["src"], "d")
    (project / "README.md").unlink()
    review = create_feature_review(project, ["src"], "p")
    sections = macro.policy_sections(review)
    # 変更したファイルがあれば図は描ける
    assert not sections.figures_missing
    assert macro.missing_material(review, sections) == ()
    empty = _without_figures(sections)
    assert macro.missing_material(review, empty) == (
        MissingMaterial.FIGURES,
        MissingMaterial.DECLARATION,
    )
    # 宣言があれば、図が空でも材料なしにしない
    assert macro.missing_material(with_declaration, empty) == ()
    intent = create_feature_review(project, ["src"], "i", DeclaredInputs(intent="買い物を助ける"))
    assert macro.missing_material(intent, empty) == ()


def test_run_saves_no_material_without_calling_the_model(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    real = macro.policy_sections
    monkeypatch.setattr(macro, "policy_sections", lambda r: _without_figures(real(r)))
    fake = MacroFake([])
    result = macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    assert fake.calls == []
    assert result.findings == ()
    assert result.run_state.to_json()["invocations"] == []
    assert result.run_state.to_json()["no_material"] == ["figures", "declaration"]
    assert result.notice is not None
    assert "材料なし" in result.notice
    stored = json.loads((review.id.directory() / RUNS_FILE).read_text(encoding="utf-8"))
    assert stored["macro"]["no_material"] == result.run_state.to_json()["no_material"]
    body = _show_json(capsys, "macro")
    assert body["result"] == "no_material"
    assert body["run"]["invocation_count"] == 0


def test_run_with_material_still_calls_the_model(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([])
    result = macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    assert len(fake.calls) == 1
    assert result.run_state.no_material is None


def test_unknown_values_in_no_material_are_named_next_to_known_ones_and_kept() -> None:
    stored = {"discovered": 0, "no_material": ["document", "from_a_future_version"]}
    run = PolicyGroupRun.from_json(stored)
    assert run.no_material == ("document", "from_a_future_version")
    assert run.missing_materials == RecordedMissing((MissingMaterial.DOCUMENT,), has_unknown=True)
    outcome = group_outcome(run, 0)
    assert outcome == NoMaterial(RecordedMissing((MissingMaterial.DOCUMENT,), has_unknown=True))
    assert isinstance(outcome, NoMaterial)
    # 知っている種類の文の後ろに、知らない種類の文が並ぶ
    assert outcome.to_json()["no_material_texts"] == [
        MissingMaterial.DOCUMENT.text,
        UNKNOWN_MISSING_TEXT,
    ]
    line = outcome_line(outcome)
    assert line is not None
    assert f"{MissingMaterial.DOCUMENT.text}、{UNKNOWN_MISSING_TEXT}" in line
    # 書き戻しても未知の値は残る
    assert run.to_json()["no_material"] == ["document", "from_a_future_version"]


def test_no_material_with_only_unknown_values_is_a_no_material_record() -> None:
    stored = {"discovered": 0, "discarded": [], "no_material": ["unheard_of"]}
    unknown = PolicyGroupRun.from_json(stored)
    assert unknown.missing_materials == RecordedMissing((), has_unknown=True)
    outcome = group_outcome(unknown, 0)
    assert outcome == NoMaterial(RecordedMissing((), has_unknown=True))
    assert isinstance(outcome, NoMaterial)
    assert outcome.to_json()["no_material_texts"] == [UNKNOWN_MISSING_TEXT]
    assert outcome_line(outcome) == f"材料なし（{UNKNOWN_MISSING_TEXT}{NO_MATERIAL_SUFFIX}）"
    # 記録は書き戻しで保たれる
    assert unknown.to_json()["no_material"] == ["unheard_of"]
    # 空の配列と欄なしは、材料なしの印にならない
    empty = PolicyGroupRun.from_json({"discovered": 0, "discarded": [], "no_material": []})
    assert empty.no_material == ()
    assert group_outcome(empty, 0) == NoFindings(0, 0)
    assert empty.missing_materials is None
    absent = PolicyGroupRun.from_json({"discovered": 0, "discarded": []})
    assert absent.no_material is None
    assert absent.missing_materials is None
    assert group_outcome(absent, 0) == NoFindings(0, 0)


def test_no_material_round_trips_as_values() -> None:
    run = PolicyGroupRun.from_json({"discovered": 0, "no_material": MISSING_VALUES})
    assert run.no_material == tuple(MISSING_VALUES)
    assert run.missing_materials == RecordedMissing(MISSING)
    assert run.to_json()["no_material"] == MISSING_VALUES
    assert PolicyGroupRun.from_json(run.to_json()).no_material == tuple(MISSING_VALUES)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["current_shape", "document"], RecordedMissing(MISSING)),
        (["unheard_of"], RecordedMissing((), has_unknown=True)),
        (
            ["document", "unheard_of"],
            RecordedMissing((MissingMaterial.DOCUMENT,), has_unknown=True),
        ),
        ([], RecordedMissing(())),
    ],
    ids=["known-only", "unknown-only", "mixed", "empty"],
)
def test_recorded_missing_reads_known_and_unknown_in_one_pass(
    values: list[str], expected: RecordedMissing
) -> None:
    assert recorded_missing(iter(values)) == expected
