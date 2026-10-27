from pathlib import Path

import pytest
from fakes import MacroFake, settings_for, with_budget
from test_design_materials import DOCUMENT, _work

from jigane.policy_materials.design_doc import design_materials
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import adversarial, macro, macro_shared, pipeline
from jigane.skills.assembly import AssemblyInput
from jigane.skills.macro_design import (
    build_design_prompt,
    stamp_design_prompt,
)

HISTORY = "前の回の指摘は無い。"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _prompt(tmp_path: Path, declaration: str = "カートを直す。") -> str:
    materials = design_materials(_work(tmp_path), "docs/d.md", DOCUMENT, ["shop/cart.py"])
    return build_design_prompt(materials, HISTORY, declaration)


def test_prompt_carries_every_section_in_order(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path)
    headings = [
        "### 設計書の全文",
        "### 設計書の構造",
        "### 名指しの照合の表",
        "### 選んだファイルと推しの理由",
        "### いまの形の図",
        "### 前の回の指摘",
    ]
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "カートを直す。" in prompt
    assert HISTORY in prompt
    assert "   5  `Cart` を直す。" in prompt


def test_prompt_has_no_unresolved_placeholder_and_no_tools(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path)
    assert "$" not in prompt
    # 道具を渡さない。図だけの形と同じく、空の組を起動に渡す
    assert macro.DIAGRAM_TOOLS == ()


def test_prompt_instructs_the_four_checks(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path)
    assert "意図した変更なのか、既存の構造との矛盾なのか" in prompt
    assert "設計書に書かれていないことを推測しません" in prompt
    assert "設計書の行（設計書のパスと行の範囲）" in prompt
    assert "非目標と突き合わせます" in prompt
    assert "「図の注記」を「設計書の節」と読み替えます" in prompt


def test_defect_catalog_and_contract_criteria_are_shared(project: Path, tmp_path: Path) -> None:
    parts = macro.prompt_parts()
    assert "閉じた値の集合を文字列や数で持つ" in parts["standards"]
    assert "前の回の指摘と突き合わせる" in parts["history_rule"]
    # 共有の部品は雛形の側に写しを持たず、二つの形のプロンプトに同じ文で入る
    for name in ("macro_diagram", "macro_design"):
        template = adversarial.template_text(name)
        assert "閉じた値の集合を文字列や数で持つ" not in template
        assert "前の回の指摘と突き合わせる" not in template
        assert "$standards" in template
        assert "$history_rule" in template
    design = _prompt(tmp_path / "work")
    review = create_feature_review(project, ["src"], "p")
    diagram = macro.build_diagram_prompt(AssemblyInput.load(review))
    for prompt in (design, diagram):
        assert parts["standards"] in prompt
        assert parts["history_rule"] in prompt


def test_stitching_is_subordinated_to_the_root_in_both_forms(
    project: Path, tmp_path: Path
) -> None:
    rule = "写しを残したまま縫い止める勧めは、別の指摘として出さず"
    shared = macro.prompt_parts()["history_rule"]
    assert shared.count(rule) == 1
    assert "「それまでの間の手当て」" in shared
    assert "縫い止めを指摘として出してよいのは、根の形の候補が無いときだけです" in shared
    for name in ("macro_diagram", "macro_design"):
        assert "それまでの間の手当て" not in adversarial.template_text(name)
    design = _prompt(tmp_path / "work")
    review = create_feature_review(project, ["src"], "p")
    diagram = macro.build_diagram_prompt(AssemblyInput.load(review))
    for prompt in (design, diagram):
        assert prompt.count(rule) == 1


def test_version_changes_with_the_shared_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = _prompt(tmp_path)
    before = stamp_design_prompt(prompt)
    assert before.chars == len(prompt)
    assert macro.stamp_diagram_prompt(prompt).version != before.version
    original = macro.prompt_parts()
    monkeypatch.setattr(
        macro_shared, "prompt_parts", lambda: {**original, "standards": "別の基準"}
    )
    assert stamp_design_prompt(prompt).version != before.version


def test_input_budget_check_is_the_same(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path)
    settings = with_budget(settings_for(MacroFake([]), max_budget_usd=0.0000001), model="m")
    with pytest.raises(pipeline.InputBudgetError):
        macro.check_input_budget(prompt, settings)
    roomy = with_budget(settings_for(MacroFake([]), max_budget_usd=50), model="claude-sonnet-5-5")
    assert macro.check_input_budget(prompt, roomy) is None
