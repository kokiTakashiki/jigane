import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fakes import RECORD_ROW, MacroFake, discovered, settings_for
from pr_support import commit_all

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.design.review_documents import create_design_doc_review
from jigane.findings.records import triaged_dicts
from jigane.groups.policy_runs import PolicyRole, PolicyShape, role_of
from jigane.llm_runs import claude
from jigane.policy_materials.missing import MissingMaterial
from jigane.reviews.lifecycle import Review, ReviewError, load_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput

DOCUMENT = "# 題\n\n## 1. 序論\n\n`Cart` を持つ。\n\n" + "長い段落。\n" * 400 + "末尾の一行。\n"
DOCUMENT_PATH = "docs/shop-design.md"


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / DOCUMENT_PATH).write_text(DOCUMENT, encoding="utf-8")
    (root / "docs" / "empty.md").write_text("", encoding="utf-8")
    # 出力の見込みの倍率を 1 にする使用量の表。fake の起動の額は入力に対して桁違いに大きく、
    # 過去の実額から倍率を引くと 2 回目の実行が上限の見込みで止まるため
    usage = root / "docs" / "reviews" / "usage.csv"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text(
        "date,review,jigane_commit,group,role,model,prompt_version,prompt_chars,calls,turns,"
        "input_tokens,cache_creation_tokens,cache_read_tokens,output_tokens,cost_usd\n"
        "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,2.0\n",
        encoding="utf-8",
    )
    commit_all(root, "add design")
    return root


def _run(review: Review, fake: MacroFake) -> macro.MacroRun:
    return macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))


def _on_document(line: int = 5) -> dict[str, Any]:
    finding = discovered("M2", DOCUMENT_PATH)
    finding["evidence"] = [{"path": DOCUMENT_PATH, "start_line": line, "end_line": line}]
    return finding


def test_run_builds_the_design_prompt_with_the_whole_document(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    fake = MacroFake([_on_document()])
    _run(review, fake)
    assert len(fake.calls) == 1
    prompt = fake.prompt
    # 本文は行番号を付けて、切らずに全行が入る
    assert prompt.count("長い段落。") == DOCUMENT.count("長い段落。")
    assert "末尾の一行。" in prompt
    assert "### 設計書の全文" in prompt
    assert "### 前の回の指摘" in prompt
    # 図だけの形の雛形ではない
    assert "### 変更" not in prompt
    assert fake.calls[0].tools == macro.DIAGRAM_TOOLS


def test_findings_on_the_document_are_saved_and_shown(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    outcome = _run(review, MacroFake([_on_document(5), _on_document(7)]))
    assert [finding["evidence"][0]["path"] for finding in triaged_dicts(outcome.findings)] == [
        DOCUMENT_PATH
    ] * 2
    # 設計書は範囲の内で、範囲外の区分にならない
    assert all(finding["triage"] == "must_read" for finding in triaged_dicts(outcome.findings))
    assert outcome.run_state.dropped_without_evidence == 0
    saved = json.loads((review.id.directory() / "findings.json").read_text("utf-8"))
    assert [f["id"] for f in saved["groups"]["macro"]] == ["F1", "F2"]
    assert main(["show", "hello+shop-design", "macro", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert [f["id"] for f in shown["findings"]] == ["F1", "F2"]
    assert shown["findings"][0]["evidence"] == [
        {"path": DOCUMENT_PATH, "start_line": 5, "end_line": 5}
    ]


def test_previous_findings_reach_the_next_prompt(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    _run(review, MacroFake([_on_document()]))
    fake = MacroFake([])
    _run(review, fake)
    assert "claim M2" in fake.prompt


def test_changed_document_warns_to_refresh_and_uses_the_current_text(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    (repo / DOCUMENT_PATH).write_text(DOCUMENT + "追記の一行。\n", encoding="utf-8")
    fake = MacroFake([])
    outcome = _run(load_review(review.id), fake)
    assert "追記の一行。" in fake.prompt
    assert outcome.notice is not None
    assert "refresh" in outcome.notice
    assert str(review.id) in outcome.notice


def test_unchanged_document_has_no_warning(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    assert "refresh" not in (_run(review, MacroFake([])).notice or "")


def test_empty_document_saves_no_material_without_calling_the_model(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_design_doc_review(repo, "docs/empty.md", select=[])
    fake = MacroFake([_on_document()])
    outcome = _run(review, fake)
    assert fake.calls == []
    assert triaged_dicts(outcome.findings) == []
    assert len(outcome.run_state.to_json()["no_material"]) == 4
    assert outcome.notice is not None
    assert main(["show", "hello+empty", "macro", "--json"]) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["result"] == "no_material"
    assert body["no_material"] == [
        MissingMaterial.DOCUMENT.value,
        MissingMaterial.STRUCTURE.value,
        MissingMaterial.MENTIONS.value,
        MissingMaterial.CURRENT_SHAPE.value,
    ]


def test_estimate_uses_the_design_prompt(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    estimate = macro.estimate_macro(AssemblyInput.load(review)).to_record()
    prompt = macro.macro_plan(AssemblyInput.load(review)).build_prompt()
    assert "### 設計書の全文" in prompt
    assert estimate["materials_bytes"] == len(prompt.encode("utf-8"))
    assert estimate["discovery_input_tokens"] == claude.estimate_input_tokens(
        len(prompt.encode("utf-8"))
    )
    assert estimate["planned_invocations"] == 1
    assert "設計書の全文" in estimate["note"]
    assert main(["estimate", "hello+shop-design", "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["materials_bytes"] == estimate["materials_bytes"]


def test_missing_document_stops_before_calling(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    (repo / DOCUMENT_PATH).unlink()
    fake = MacroFake([])
    with pytest.raises(ReviewError, match="設計書がありません"):
        _run(load_review(review.id), fake)
    assert fake.calls == []


def _stored_macro(review: Review) -> dict[str, Any]:
    stored = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))
    macro_run: dict[str, Any] = stored["macro"]
    return macro_run


def test_design_run_records_the_design_shape_and_role(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    result = _run(review, MacroFake([_on_document()]))
    stored = _stored_macro(review)
    assert stored["shape"] == PolicyShape.DESIGN
    assert {i["role"] for i in stored["invocations"]} == {PolicyRole.DESIGN}
    assert result.run_state.shape == PolicyShape.DESIGN


def test_design_no_material_run_records_the_design_shape(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/empty.md", select=[])
    _run(review, MacroFake([]))
    assert _stored_macro(review)["shape"] == PolicyShape.DESIGN


def test_design_estimate_takes_past_run_only_from_the_design_shape(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    path = review.id.directory() / "runs.json"
    diagram_run = {"macro": {"model": "m", "shape": "diagram", "invocations": [RECORD_ROW]}}
    path.write_text(json.dumps(diagram_run), encoding="utf-8")
    assert macro.estimate_macro(AssemblyInput.load(review)).to_record()["past_run"] is None
    _run(review, MacroFake([_on_document()]))
    estimate = macro.estimate_macro(AssemblyInput.load(review)).to_record()
    assert estimate["shape"] == PolicyShape.DESIGN
    assert estimate["past_run"]["invocation_count"] == 1


def test_role_of_covers_every_shape() -> None:
    assert role_of(PolicyShape.DIAGRAM) == PolicyRole.DIAGRAM
    assert role_of(PolicyShape.DESIGN) == PolicyRole.DESIGN
    assert role_of(PolicyShape.SINGLE) == PolicyRole.SINGLE
    with pytest.raises(ValueError, match="廃止"):
        role_of(PolicyShape.FULL)
