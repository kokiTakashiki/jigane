"""設計書のレビューの必読（節の種類・受け持ちの印・付属の文書）の試験。"""

import json
import shutil
from pathlib import Path

import pytest
from pr_support import commit_all

from jigane import commands
from jigane.commands.views.review import SHORT_SHA, origin_lines
from jigane.design.division import section_marks
from jigane.design.parse import parse_design
from jigane.design.review_documents import (
    attachment_candidates,
    create_design_doc_review,
    document_version,
)
from jigane.design.section_map import build_section_map
from jigane.groups.definitions import MACRO
from jigane.reviews.lifecycle import DesignDocOrigin
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput

DOCUMENT = "docs/shop-design.md"
MEMO = "docs/reports/history-split.md"

# 設計書の見出しの並びの見本。題に「設計」、3 章に「提案設計」を持つ
SAMPLE_DESIGN_LIKE = """# ストアを切り出す設計

## 1. 序論

背景を書く。
続きを書く。

## 2. 目的

狙いを書く。
続きを書く。

### 2.1 補足

細部を書く。
続きを書く。

## 3. 提案設計

方針を書く。
続きを書く。
"""


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "docs" / "reports").mkdir(parents=True, exist_ok=True)
    (root / DOCUMENT).write_text("# 題\n\n## 1. 序論\n\n`Cart` を持つ。\n", encoding="utf-8")
    (root / MEMO).write_text("# 検証メモ\n\n分割で履歴が残る。\n", encoding="utf-8")
    (root / "docs" / "reports" / "other.md").write_text("# 別\n", encoding="utf-8")
    commit_all(root, "add design")
    return root


def test_document_title_does_not_pass_its_kind_to_chapters_without_words() -> None:
    parts = build_section_map("docs/d.md", SAMPLE_DESIGN_LIKE)
    kinds = {region.summary: region.kind for region in parts.regions}
    assert kinds["1. 序論"] == "other"
    assert kinds["2. 目的"] == "other"
    assert kinds["2.1 補足"] == "other"
    assert kinds["3. 提案設計"] == "proposal"


def test_document_title_words_do_not_mark_chapters_without_words() -> None:
    document = parse_design(
        "# 実装計画と検証方法\n\n## 1. 序論\n\n本文。\n\n## 2. 実装計画\n\n本文。\n"
    )
    marks = section_marks(document.sections)
    by_title = {section.title: marks[section] for section in document.all_sections()}
    assert by_title["実装計画と検証方法"] == MACRO
    assert by_title["1. 序論"] == MACRO
    assert by_title["2. 実装計画"] != MACRO


def test_candidates_are_empty_for_a_document_outside_the_repository(
    repo: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere"
    (outside / "reports").mkdir(parents=True)
    (outside / "reports" / "x.md").write_text("# x\n", encoding="utf-8")
    assert attachment_candidates(repo, str(outside / "design.md")) == []


def test_suggest_continues_for_a_document_outside_the_repository(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "elsewhere"
    (outside / "reports").mkdir(parents=True)
    (outside / "reports" / "x.md").write_text("# x\n", encoding="utf-8")
    design = outside / "design.md"
    design.write_text("# 題\n\n## 1. 序論\n\n`Cart` を持つ。\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert commands.main(["design", "suggest", str(design), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["attachment_candidates"] == []


def test_changed_attachment_warns_to_refresh_before_asking(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    (repo / MEMO).write_text("# 検証メモ\n\n改訂した。\n", encoding="utf-8")
    origin = review.origin
    assert isinstance(origin, DesignDocOrigin)
    plan = macro.design_plan(AssemblyInput.load(review), origin)
    assert plan.warning is not None
    assert f"付属の文書 {MEMO} が保存した版と違います" in plan.warning
    assert f"jigane refresh {review.id}" in plan.warning


def test_suggest_lists_attachment_candidates(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert commands.main(["design", "suggest", str(repo / DOCUMENT), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["attachment_candidates"] == [MEMO, "docs/reports/other.md"]
    assert commands.main(["design", "suggest", str(repo / DOCUMENT)]) == 0
    assert f"  {MEMO}" in capsys.readouterr().out


def test_overview_lists_attachments_and_unattached_candidates(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    text = "\n".join(origin_lines(review))
    version = document_version((repo / MEMO).read_bytes())[:SHORT_SHA]
    assert "付属の文書: 1 件" in text
    assert f"{MEMO}（版 {version}）" in text
    assert "付属の候補（--with で選ぶ）:" in text
    assert "docs/reports/other.md" in text
    # 付け済みの文書は候補に重ねて出さない
    assert text.count(MEMO) == 1
