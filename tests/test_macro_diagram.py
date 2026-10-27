import json
from pathlib import Path

import pytest
from fakes import MacroFake, discovered

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.groups.definitions import DIAGRAM_SHAPE
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput

BODY_MARKER = "SECRET_BODY_MARKER"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text(
        "class Widget:\n"
        "    size: int\n"
        "\n"
        "    def build(self, scale: int) -> int:\n"
        f"        return scale  # {BODY_MARKER}\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Shop\n\nlead\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_diagram_prompt_has_diagrams_and_no_code_body(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert "### クラス図" in prompt
    assert "classDiagram" in prompt
    assert "size: int" in prompt
    assert "build(scale: int) int" in prompt
    assert "### 変更のツリー図" in prompt
    assert "### シーケンス図" in prompt
    assert "### 技術選定" in prompt
    assert "### 判断の箇所の一覧" in prompt
    assert "### 前の回の指摘" in prompt
    assert BODY_MARKER not in prompt
    assert "$" not in prompt


def test_diagram_prompt_defines_checking_by_diagrams_and_costly_shapes(
    project: Path,
) -> None:
    review = create_feature_review(project, ["src"], "p")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert "コードを読んで確かめることではありません" in prompt
    assert (
        "図に現れていない事柄（呼び出しの有無、関数の中の処理）に頼る候補だけを捨てます" in prompt
    )
    assert "手戻りの大きさは、その形が契約になるかで測ります" in prompt
    assert "いまの読む側が少ないこと" in prompt
    assert "| 閉じた値の集合を文字列や数で持つ | その値が契約に現れる |" in prompt
    assert "図と矛盾しない指摘は、確信が持てなくても出します" in prompt
    assert "あるべき姿は最初の一手ではなく根の形にします" in prompt
    assert "同じ論点の深掘りです。捨てずに出し、deepens に前の回の番号" in prompt
    assert "この変更より前からある契約" in prompt
    assert "前の回の severity と in_scope を既定にします" in prompt
    assert "往復の候補は、出す前に根を探します" in prompt
    assert "deepens を付けない一件の指摘として出し" in prompt
    assert "| 閉じた値の集合を文字列や数で持つ | その値が契約に現れる | 列挙型にし、" in prompt


def test_diagram_run_passes_no_tools_and_stores_findings(project: Path) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M2")])
    assert main(["run", "shop:p", "macro"], invoke=fake) == EXIT_OK
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.tools == ()
    assert call.max_turns == DIAGRAM_SHAPE.max_turns
    assert call.budget.cli_usd == 0.5
    command = claude.build_command(call)
    assert command[command.index("--tools") + 1] == ""
    stored = json.loads(
        next((project.parent / "home").rglob("findings.json")).read_text(encoding="utf-8")
    )
    assert len(stored["groups"]["macro"]) == 1


def test_diagram_budget_can_be_overridden(project: Path) -> None:
    create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M2")])
    arguments = ["run", "shop:p", "macro", "--max-budget-usd-per-call", "0.8"]
    assert main(arguments, invoke=fake) == EXIT_OK
    assert fake.calls[0].budget.cli_usd == 0.8


def test_diagram_run_keeps_evidence_at_class_diagram_positions(project: Path) -> None:
    create_feature_review(project, ["src"], "p")
    finding = discovered("M2", "src/a.py")
    fake = MacroFake([finding])
    review = create_feature_review(project, ["src"], "p2")
    result = macro.run_macro(
        AssemblyInput.load(review),
        claude.RunSettings(
            budget=claude.RunBudget("m", 1.0, reserve_margin=True), effort=None, invoke=fake
        ),
    )
    assert result.run_state.shape == "diagram"
    assert len(result.findings) == 1
    assert result.run_state.dropped_without_evidence == 0


def test_diagram_prompt_asks_two_questions_and_lists_what_not_to_raise(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert "インターフェースの失敗は手戻りの主な源です" in prompt
    assert "読む側が増える前に決めるべき形か" in prompt
    assert "関数の共有の仕方、テストの抜け、確かめ方の穴、内部ロジックの誤り" in prompt
    assert "実装が決まってから気がつく話" in prompt
    assert "nit は出しません" in prompt
    assert "### 公開のシグネチャの変更前後" not in prompt
