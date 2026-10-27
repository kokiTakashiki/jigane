import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pr_support import commit_all

from jigane.commands.views.review import map_lines
from jigane.design.division import SECTION_KINDS, section_kind
from jigane.design.review_documents import create_design_doc_review
from jigane.design.section_map import build_section_map
from jigane.judgment_map.kinds import Kind, SectionKind
from jigane.judgment_map.map_kind import MAP_FORMAT, CodeMap, DesignDocMap, dump_map, load_map
from jigane.judgment_map.region_table import (
    CodeRegion,
    LineCounts,
    RegionTable,
    SectionRegion,
)
from jigane.reviews.lifecycle import create_feature_review, judge_files, load_review

DOCUMENT = """# 題

## 1. 背景

きっかけ。
続き。

## 2. 非目標

やらないこと。
もう一つ。

## 3. リスクと限界

危ない点。

### 3.1 補足

詳しく。

## 4. 提案設計

`Cart` を持つ。
形。

## 5. 検証方法

試す。
確かめる。
"""


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "shop-design.md").write_text(DOCUMENT, encoding="utf-8")
    commit_all(root, "add design")
    return root


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("2. 非目標", SectionKind.NON_GOAL),
        ("3. リスクと限界", SectionKind.RISK),
        ("4. 提案設計", SectionKind.PROPOSAL),
        ("5. 検証方法", SectionKind.VERIFICATION),
        ("1. 背景", SectionKind.OTHER),
    ],
)
def test_section_kind_comes_from_heading_words(title: str, expected: SectionKind) -> None:
    assert section_kind(title, SectionKind.OTHER) is expected
    assert expected in SECTION_KINDS


def test_child_without_words_inherits_parent_kind() -> None:
    assert section_kind("3.1 補足", SectionKind.RISK) is SectionKind.RISK


def test_sections_become_regions_with_kinds() -> None:
    parts = build_section_map("docs/d.md", DOCUMENT)
    by_title = {region.summary: region for region in parts.regions}
    assert by_title["2. 非目標"].kind is SectionKind.NON_GOAL
    assert by_title["3.1 補足"].kind is SectionKind.RISK
    assert by_title["4. 提案設計"].kind is SectionKind.PROPOSAL
    assert all(region.path == "docs/d.md" for region in parts.regions)
    assert all(isinstance(region, SectionRegion) and region.kind_label for region in parts.regions)
    # 親の節の行数は子の節を含まない
    parent_span = by_title["3. リスクと限界"].new_span
    child_span = by_title["3.1 補足"].new_span
    assert parent_span
    assert child_span
    assert parent_span.end_line < child_span.start_line


def test_design_review_map_uses_sections_and_keeps_code_as_reference(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/shop-design.md")
    stored = load_review(review.id).map_kind.to_dict()
    assert stored["mode"] == "design_doc"
    assert {region["path"] for region in stored["regions"]} == {"docs/shop-design.md"}
    kinds = {region["kind"] for region in stored["regions"]}
    assert {"non_goal", "risk", "proposal", "verification"} <= kinds
    assert all(region["id"].startswith("J") for region in stored["regions"])
    # コードの箇所は地図の箇所に混ざらず、いまの形として別に載る
    assert stored["current_form"]["regions"]
    assert all(region["path"] == "cart.py" for region in stored["current_form"]["regions"])
    on_disk = json.loads((review.id.directory() / "map.json").read_text(encoding="utf-8"))
    assert on_disk["mode"] == "design_doc"
    text = "\n".join(map_lines(load_review(review.id)))
    assert "いまの形（推しのコード）" in text
    assert "docs/shop-design.md" in text


def test_map_kinds_write_the_same_keys_and_read_back_by_kind(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/shop-design.md")
    stored = dump_map(load_review(review.id).map_kind)
    assert set(stored) == {"format", "mode", "segments", "regions", "line_counts", "current_form"}
    assert set(stored["current_form"]) == {"regions", "line_counts"}
    design = load_map(stored)
    assert isinstance(design, DesignDocMap)
    assert dump_map(design) == stored
    assert design.current_form.to_dict() == stored["current_form"]

    code_stored = {
        "format": MAP_FORMAT,
        "mode": "diff",
        "segments": [],
        "regions": [],
        "line_counts": {"by_kind": {}, "by_level": {}},
    }
    code = load_map(code_stored)
    assert isinstance(code, CodeMap)
    assert dump_map(code) == code_stored


@pytest.mark.parametrize("mode_name", ["feature", "diff"])
def test_code_map_reads_back_in_each_code_mode(mode_name: str) -> None:
    code_stored = {
        "format": MAP_FORMAT,
        "mode": mode_name,
        "segments": [],
        "regions": [],
        "line_counts": {"by_kind": {}, "by_level": {}},
    }
    code = load_map(code_stored)
    assert isinstance(code, CodeMap)
    assert dump_map(code) == code_stored


def test_feature_review_map_is_a_code_map_drawn_without_current_form(repo: Path) -> None:
    review = load_review(create_feature_review(repo, ["cart.py"], "feat").id)
    assert review.map_kind.to_dict()["mode"] == "feature"
    assert "current_form" not in review.map_kind.to_dict()
    assert isinstance(load_map(dump_map(review.map_kind)), CodeMap)
    text = "\n".join(map_lines(review))
    assert "判断の地図" in text
    assert "いまの形" not in text


def test_region_table_round_trips_the_stored_pair() -> None:
    code_row = {
        "key": "a.py::f::logic",
        "kind": "logic",
        "path": "b.py",
        "qualified_name": "f",
        "summary": "変更: f",
        "old_span": {"start_line": 1, "end_line": 3},
        "new_span": {"start_line": 2, "end_line": 5},
        "line_count": 4,
        "content_hash": "abc",
        "bot_comment_ids": [7, 9],
        "old_path": "a.py",
        "id": "J1",
    }
    design_row: dict[str, Any] = {
        "key": "d.md::2. 非目標::non_goal",
        "kind": "non_goal",
        "kind_label": "非目標",
        "path": "d.md",
        "qualified_name": "2. 非目標",
        "summary": "2. 非目標",
        "old_span": None,
        "new_span": {"start_line": 3, "end_line": 6},
        "line_count": 4,
        "content_hash": "def",
        "bot_comment_ids": [],
        "old_path": None,
    }
    stored = {
        "regions": [code_row, design_row],
        "line_counts": {"by_kind": {}, "by_level": {}},
    }
    table = RegionTable.from_dict(stored)
    assert table.to_dict() == stored
    # 保存の形には、節の行の空の二つの鍵が無い
    stored_section = table.to_stored_dict()["regions"][1]
    assert "bot_comment_ids" not in stored_section
    assert "old_path" not in stored_section
    assert table.to_stored_dict()["regions"][0] == code_row
    code, section = table.regions
    assert isinstance(code, CodeRegion)
    assert code.kind is Kind.LOGIC
    assert code.old_path == "a.py"
    assert code.bot_comment_ids == (7, 9)
    assert isinstance(section, SectionRegion)
    assert section.kind is SectionKind.NON_GOAL
    assert section.kind_label == "非目標"


@pytest.mark.parametrize(
    "stored",
    [
        {"by_kind": {"logic": 3}, "by_level": {"required": 3}},
        {
            "by_kind": {"overview": 3},
            "by_level": {"machine": 3},
            "kind_labels": {"overview": "概要"},
            "kind_levels": {"overview": "machine"},
        },
    ],
    ids=["code", "section"],
)
def test_line_counts_round_trip_keeps_the_stored_keys(stored: dict[str, Any]) -> None:
    counts = LineCounts.from_dict(stored)
    assert counts.to_dict() == stored
    assert (counts.section_kinds is None) == ("kind_labels" not in stored)


@pytest.mark.parametrize("present", ["kind_labels", "kind_levels"])
def test_line_counts_reject_a_stored_half_of_the_section_kind_pair(present: str) -> None:
    stored: dict[str, Any] = {"by_kind": {}, "by_level": {}, present: {}}
    with pytest.raises(ValueError, match="一組"):
        LineCounts.from_dict(stored)


def test_review_json_keeps_the_line_counts_keys(repo: Path) -> None:
    review = create_feature_review(repo, ["cart.py"], "payment")
    shown = review.to_dict()["judgment_map"]["line_counts"]
    assert set(shown) == {"by_kind", "by_level", "by_file"}
    assert shown["by_level"] == review.map_kind.table.line_counts.by_level


def test_design_review_json_keeps_the_section_kind_keys(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/shop-design.md")
    shown = review.to_dict()["judgment_map"]["line_counts"]
    assert set(shown) == {"by_kind", "by_level", "by_file", "kind_labels", "kind_levels"}


def test_code_and_section_kind_values_do_not_overlap() -> None:
    # region_from_dict は kind の値で行の型を決めるので、重なれば節の行がコードの行になる
    assert not {kind.value for kind in Kind} & {kind.value for kind in SectionKind}


def test_design_doc_current_form_is_the_code_map_region_table(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/shop-design.md")
    design = load_map(dump_map(load_review(review.id).map_kind))
    assert isinstance(design, DesignDocMap)
    code_judgment_map = judge_files(repo, ["cart.py"])
    code_table = code_judgment_map.region_table()
    # 空の表どうしの一致で通らないよう、コードの領域があることを先に確かめる
    assert code_table.regions
    assert design.current_form == code_table
