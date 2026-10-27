import ast
from pathlib import Path

import pytest

import jigane
from jigane.design import division
from jigane.design.division import (
    CROSS_RULES,
    DIVISION,
    HEADING_WORDS,
    VIEWPOINTS,
    ConcernRow,
    DesignInserts,
    PolicyRow,
    ViewpointId,
    design_inserts,
    division_text,
    group_words,
    heading_group,
    inherited_value,
    section_kind,
    section_marks,
    viewpoints_text,
)
from jigane.design.parse import Section, parse_design
from jigane.groups.definitions import GROUP_IDS, MACRO, MICRO, SECURITY, GroupId
from jigane.judgment_map.kinds import SectionKind
from jigane.policy_materials.design_doc import design_materials
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import adversarial, macro, macro_shared
from jigane.skills.assembly import AssemblyInput
from jigane.skills.macro_design import build_design_prompt, stamp_design_prompt

# 設計書の見出しの並びの見本（本文は持たない）
SAMPLE_DESIGN_HEADINGS = """\
# ストアを別のリポジトリへ切り出す設計
## 要旨
## 目次
## 1. 序論
## 2. 問題設定と適用範囲
### 2.1 問題定義
## 3. 背景
### 3.2 根のパスと import の依存
## 4. 提案設計
### 4.5 横断的関心事
## 5. 二段のコミットと worktree の運用
## 6. 実装計画
### 6.1 段 1: 新しいリポジトリを作る
## 7. 代替案の検討
## 8. 検証方法
## 9. リスクと限界
### 9.1 リスクと緩和
## 10. 今後の課題
## 11. 参考文献
"""

DISTINCTIVE_WORDS = {"実装計画", "検証方法", "供給網", "脅威"}


def _marks(text: str) -> dict[str, GroupId]:
    document = parse_design(text)
    marks = section_marks(document.sections)
    return {section.title: marks[section] for section in document.all_sections()}


def test_table_is_keyed_by_the_closed_set_of_groups() -> None:
    assert tuple(DIVISION) == GROUP_IDS == tuple(GroupId)
    assert isinstance(DIVISION[MACRO], PolicyRow)
    assert isinstance(DIVISION[MICRO], ConcernRow)
    assert isinstance(DIVISION[SECURITY], ConcernRow)
    assert set(VIEWPOINTS) == set(ViewpointId)
    policy = DIVISION[MACRO]
    assert isinstance(policy, PolicyRow)
    assert policy.viewpoints == frozenset(ViewpointId)
    assert "M5" not in {item.value for item in ViewpointId}


def test_marks_follow_the_heading_words() -> None:
    marks = _marks(SAMPLE_DESIGN_HEADINGS)
    assert marks["ストアを別のリポジトリへ切り出す設計"] == MACRO
    assert marks["1. 序論"] == MACRO
    assert marks["4.5 横断的関心事"] == MACRO
    assert marks["5. 二段のコミットと worktree の運用"] == MICRO
    assert marks["6. 実装計画"] == MICRO
    # 子は親の印を引き継ぐ
    assert marks["6.1 段 1: 新しいリポジトリを作る"] == MICRO
    assert marks["8. 検証方法"] == MICRO
    assert marks["9.1 リスクと緩和"] == MACRO


def test_security_words_mark_security_and_children_inherit() -> None:
    marks = _marks("# 題\n## 4. 認証と権限\n### 4.1 トークン\n## 5. 概要\n")
    assert marks["4. 認証と権限"] == SECURITY
    assert marks["4.1 トークン"] == SECURITY
    assert marks["5. 概要"] == MACRO


def test_structure_text_carries_the_marks(tmp_path: Path) -> None:
    materials = design_materials(tmp_path, "docs/d.md", SAMPLE_DESIGN_HEADINGS, [])
    assert "- 6. 実装計画（" in materials.structure
    lines = {line.strip(): line for line in materials.structure.splitlines()}
    assert any("6. 実装計画" in line and "印 実装" in line for line in lines)
    assert any("1. 序論" in line and "印 方針" in line for line in lines)


def _word_literals(source: str, words: set[str]) -> list[str]:
    """docstring を除く文字列定数のうち、words に入るもの。"""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in words
        and id(node) not in docstrings
    ]


def test_heading_words_are_defined_only_in_the_table() -> None:
    package = Path(jigane.__file__).parent
    words = {word for group in (MICRO, SECURITY) for word in group_words(group)}
    assert words
    # 他の用途で現れる語（テスト、権限など）を除き、この表に固有の語だけを調べる
    distinctive = words & DISTINCTIVE_WORDS
    for path in package.rglob("*.py"):
        if path == Path(division.__file__):
            continue
        found = _word_literals(path.read_text(encoding="utf-8"), distinctive)
        assert found == [], f"{path.name} が語 {found} を持つ"


def test_a_heading_word_literal_is_found_outside_docstrings() -> None:
    words = {"実装計画"}
    assert _word_literals("x = '実装計画'\n", words) == ["実装計画"]
    assert _word_literals("f('実装計画')\n", words) == ["実装計画"]
    assert _word_literals('"""実装計画"""\nx = 1\n', words) == []
    assert _word_literals("x = '実装計画の章'\n", words) == []


def test_design_template_gets_the_table_and_viewpoints(tmp_path: Path) -> None:
    materials = design_materials(tmp_path, "docs/d.md", SAMPLE_DESIGN_HEADINGS, [])
    prompt = build_design_prompt(materials, "前の回の指摘は無い。", "宣言")
    assert division_text() in prompt
    assert viewpoints_text() in prompt
    assert "$" not in prompt
    for rule in CROSS_RULES:
        assert rule in prompt
    assert "あなたは方針の群です" in prompt
    assert "候補にも、捨てた候補（discarded）にもしません" in prompt
    # 観点の文は表から差し込まれ、雛形の本文には無い
    template = adversarial.template_text("macro_design")
    assert "$division" in template
    assert "$viewpoints" in template
    for item in VIEWPOINTS.values():
        assert item.text not in template


def test_diagram_template_is_unchanged_by_the_division(tmp_path: Path) -> None:
    template = adversarial.template_text("macro_diagram")
    assert "$division" not in template
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    review = create_feature_review(root, ["src"], "p")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert division_text() not in prompt
    assert "あなたは方針の群です" not in prompt


def test_stamp_changes_with_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "x"
    before = stamp_design_prompt(text)
    row = DIVISION[MICRO]
    assert isinstance(row, ConcernRow)
    patched = dict(DIVISION)
    patched[MICRO] = ConcernRow(row.concerns + "追加")
    monkeypatch.setattr(division, "DIVISION", patched)
    assert stamp_design_prompt(text).version != before.version


def test_group_id_values_match_the_stored_strings() -> None:
    assert [group.value for group in GroupId] == ["macro", "micro", "security"]
    assert GroupId("macro") is MACRO
    assert str(GroupId.MICRO) == "micro"


def test_inserts_build_the_prompt_and_the_stamp_from_one_value(tmp_path: Path) -> None:
    inserts = design_inserts()
    assert inserts.division == division_text()
    assert inserts.viewpoints == viewpoints_text()
    materials = design_materials(tmp_path, "docs/d.md", SAMPLE_DESIGN_HEADINGS, [])
    prompt = build_design_prompt(materials, "なし", "宣言")
    assert inserts.division in prompt
    assert inserts.viewpoints in prompt
    changed = DesignInserts(inserts.division, inserts.viewpoints + "追加")
    base = macro_shared.stamp_template("macro_design", "x", inserts)
    assert base == stamp_design_prompt("x")
    assert macro_shared.stamp_template("macro_design", "x", changed).version != base.version


def test_diagram_stamp_has_no_inserts() -> None:
    diagram = macro_shared.stamp_template("macro_diagram", "x")
    assert diagram == macro.stamp_template("macro_diagram", "x")
    assert diagram == macro_shared.stamp_template("macro_diagram", "x", None)


def test_inherited_value_keeps_the_parent_value_for_the_document_title() -> None:
    body = Section(level=2, title="本文", start_line=3, end_line=4, children=())
    title = Section(level=1, title="設計書", start_line=1, end_line=4, children=(body,))
    assert inherited_value(title, "parent", lambda _title, _parent: "decided") == "parent"
    assert (
        inherited_value(body, "parent", lambda text, parent: f"{text}:{parent}") == "本文:parent"
    )


_OLD_KIND_WORDS: tuple[tuple[SectionKind, tuple[str, ...]], ...] = (
    (SectionKind.DECISION, ("決定", "判断")),
    (SectionKind.NON_GOAL, ("非目標",)),
    (SectionKind.RISK, ("リスク", "限界")),
    (SectionKind.BOUNDARY, ("範囲", "境目", "スコープ")),
    (SectionKind.PROPOSAL, ("提案", "設計")),
    (SectionKind.VERIFICATION, ("検証", "試験", "テスト")),
)
_OLD_GROUP_WORDS: tuple[tuple[GroupId, tuple[str, ...]], ...] = (
    (MICRO, ("実装計画", "検証方法", "試験", "テスト", "運用", "手順")),
    (SECURITY, ("セキュリティ", "権限", "秘密", "認証", "供給網", "脅威")),
)


def test_heading_word_table_matches_the_two_former_tables_on_every_word() -> None:
    words = sorted({word for row in HEADING_WORDS for word in row.words})
    titles = [*words, *(f"{a}と{b}" for a in words for b in words), "無関係"]
    for title in titles:
        expected_kind = next(
            (kind for kind, ws in _OLD_KIND_WORDS if any(w in title for w in ws)),
            SectionKind.RISK,
        )
        expected_group = next(
            (group for group, ws in _OLD_GROUP_WORDS if any(w in title for w in ws)),
            SECURITY,
        )
        assert section_kind(title, SectionKind.RISK) is expected_kind, title
        assert heading_group(title, SECURITY) == expected_group, title


def test_group_words_match_the_former_group_tables() -> None:
    for group, words in _OLD_GROUP_WORDS:
        assert group_words(group) == words
