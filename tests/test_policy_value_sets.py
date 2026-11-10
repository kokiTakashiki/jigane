import json
from pathlib import Path

import pytest
from review_fakes import make_review
from test_policy_classes import whole

from jigane.languages.base import ComparisonKind, DefinitionKind, StringComparison
from jigane.policy_materials import value_sets
from jigane.policy_materials.value_sets import (
    FileCounts,
    ValueSetLocation,
    ValueSetRow,
)
from jigane.reviews.lifecycle import Review
from jigane.reviews.review_formats import PAIRS_FILE
from jigane.skills import policy_value_sets
from jigane.skills.assembly import AssemblyInput
from jigane.skills.macro_principles_run import estimate_policy
from jigane.skills.policy_figures import REGISTRY, FigureSlot, diagrams_of
from jigane.skills.policy_principles import principle_prompts
from jigane.storage.store import ReviewKind

COMMON_FILES = 100
TITLES_VALUE_SETS = policy_value_sets.SECTION_VALUE_SETS


def _write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _set_files(prefix: str, count: int = 3) -> dict[str, str]:
    """`prefix` ごとに別々の値の組を持つファイルと、その値を使うコードと文書。"""
    values = [f"{prefix}_{i}" for i in range(count)]
    return {
        f"{prefix}/defs.py": f"{prefix.upper()} = {tuple(values)!r}\n",
        f"{prefix}/use.py": f'if v == "{values[0]}":\n    pass\n',
        f"{prefix}/doc.md": " ".join(values) + "\n",
    }


def _review(root: Path, files: dict[str, str], changed: list[str]) -> Review:
    _write(root, {f"common/c{i}.py": "x = 1\n" for i in range(COMMON_FILES)})
    _write(root, files)
    return make_review(
        root,
        changed,
        judgment_map={
            "segments": [whole(path, 1) for path in changed],
            "regions": [],
            "line_counts": {},
        },
        kind=ReviewKind.FEATURE,
    )


def test_table_lists_only_sets_touched_by_changed_files(tmp_path: Path) -> None:
    files = {**_set_files("alpha"), **_set_files("beta")}
    figure = policy_value_sets.value_set_table(_review(tmp_path, files, ["alpha/use.py"]))
    assert figure is not None
    assert [row.cells[0] for row in figure.rows] == ["alpha/defs.py:1 ALPHA"]
    assert figure.rows[0].cells[2:] == ("1", "1", "0", "1 (alpha/use.py:1 等値)", "なし")


def test_changed_definition_file_selects_its_set(tmp_path: Path) -> None:
    files = {**_set_files("alpha"), **_set_files("beta")}
    figure = policy_value_sets.value_set_table(_review(tmp_path, files, ["beta/defs.py"]))
    assert figure is not None
    assert [row.cells[0] for row in figure.rows] == ["beta/defs.py:1 BETA"]


def test_changed_comparison_file_selects_its_set(tmp_path: Path) -> None:
    files = {**_set_files("alpha"), "other/cmp.py": 'v == "alpha_1"\n'}
    figure = policy_value_sets.value_set_table(_review(tmp_path, files, ["other/cmp.py"]))
    assert figure is not None
    assert figure.rows[0].cells[0] == "alpha/defs.py:1 ALPHA"
    assert figure.rows[0].cells[-2].startswith("2 (")


def test_none_when_no_changed_file_touches_a_set(tmp_path: Path) -> None:
    files = {**_set_files("alpha"), "plain.py": "y = 2\n"}
    assert policy_value_sets.value_set_table(_review(tmp_path, files, ["plain.py"])) is None


def test_rows_sort_by_code_plus_document_then_test_count(tmp_path: Path) -> None:
    files = {**_set_files("alpha"), **_set_files("beta"), **_set_files("gamma")}
    # beta はコードと文書が増える。alpha と gamma は同数で、テストの多い gamma が先に並ぶ
    files["beta/more.py"] = "beta_0 beta_1 beta_2\n"
    files["gamma/test_x.py"] = "gamma_0 gamma_1 gamma_2\n"
    changed = ["alpha/defs.py", "beta/defs.py", "gamma/defs.py"]
    figure = policy_value_sets.value_set_table(_review(tmp_path, files, changed))
    assert figure is not None
    assert [row.cells[0].split("/")[0] for row in figure.rows] == ["beta", "gamma", "alpha"]


def test_rows_sort_by_number_of_non_test_definitions_first(tmp_path: Path) -> None:
    # alpha は定義が一か所でコードと文書が多い。beta は定義が二か所で少ない
    files = {**_set_files("alpha"), "alpha/x.py": "alpha_0 alpha_1 alpha_2\n"}
    files["alpha/y.py"] = "alpha_0 alpha_1 alpha_2\n"
    files["beta/d1.py"] = "B1 = ('beta_0', 'beta_1', 'beta_2')\n"
    files["beta/d2.py"] = "B2 = ('beta_0', 'beta_1', 'beta_2')\n"
    changed = ["alpha/defs.py", "beta/d1.py"]
    figure = policy_value_sets.value_set_table(_review(tmp_path, files, changed))
    assert figure is not None
    assert [row.cells[0].split("/")[0] for row in figure.rows] == ["beta", "alpha"]


def test_rows_over_the_limit_are_summarized_in_the_note(tmp_path: Path) -> None:
    files = {**_set_files("alpha"), **_set_files("beta"), **_set_files("gamma")}
    changed = ["alpha/defs.py", "beta/defs.py", "gamma/defs.py"]
    figure = policy_value_sets.value_set_table(_review(tmp_path, files, changed), max_rows=2)
    assert figure is not None
    assert len(figure.rows) == 2
    assert figure.note == "上限を超えた 1 件は載せていない。"


def test_values_cell_shows_six_values_and_the_remainder(tmp_path: Path) -> None:
    files = _set_files("alpha", count=8)
    figure = policy_value_sets.value_set_table(_review(tmp_path, files, ["alpha/defs.py"]))
    assert figure is not None
    assert figure.rows[0].cells[1].endswith("ほか 2 個")
    assert figure.rows[0].cells[1].count("`") == 12


def test_registered_after_regions(tmp_path: Path) -> None:
    entry = next(e for e in REGISTRY if e.key == "value_sets")
    assert entry.title == "値の組の表"
    assert entry.slot == FigureSlot.AFTER_REGIONS
    review = _review(tmp_path, _set_files("alpha"), ["alpha/defs.py"])
    assert "value_sets" in [p.key for p in diagrams_of(review).figures]


def test_comparison_cell_lists_places_non_test_files_first(tmp_path: Path) -> None:
    files = {
        **_set_files("alpha"),
        "other/b.py": 'v == "alpha_1"\n',
        "other/a.py": 'v.startswith("alpha_2")\n',
        "other/test_c.py": 'v == "alpha_0"\n',
    }
    figure = policy_value_sets.value_set_table(_review(tmp_path, files, ["other/a.py"]))
    assert figure is not None
    assert figure.rows[0].cells[-2] == (
        "4 (alpha/use.py:1 等値, other/a.py:1 接頭辞, other/b.py:1 等値 ほか 1 件)"
    )


def test_comparison_cell_without_comparisons_is_zero() -> None:
    row = ValueSetRow(
        definitions=(),
        values=(),
        files=FileCounts(0, 0, 0),
        containing_paths=frozenset(),
        comparisons=(),
    )
    assert policy_value_sets._comparison_cell(row) == "0"


def test_definition_cell_lists_places_up_to_the_limit() -> None:
    locations = tuple(ValueSetLocation(f"d{i}.py", 1) for i in range(5))
    row = ValueSetRow(
        definitions=locations,
        values=(),
        files=FileCounts(0, 0, 0),
        containing_paths=frozenset(),
        comparisons=(),
    )
    assert policy_value_sets._definition_cell(row) == "d0.py:1, d1.py:1, d2.py:1 ほか 2 件"


def _row(**kwargs: object) -> ValueSetRow:
    base: dict[str, object] = {
        "definitions": (ValueSetLocation("d.py", 1),),
        "values": ("aa", "bb"),
        "files": FileCounts(0, 0, 0),
        "containing_paths": frozenset(),
        "comparisons": (),
    }
    return ValueSetRow(**{**base, **kwargs})  # type: ignore[arg-type]


def test_touch_cell_definition_added(tmp_path: Path) -> None:
    assert (
        policy_value_sets._touch_cell(_row(), {"d.py": {1: "X = ('aa', 'bb')"}}, tmp_path)
        == "定義"
    )


def test_touch_cell_comparison_added(tmp_path: Path) -> None:
    row = _row(comparisons=(StringComparison("aa", ComparisonKind.EQUALS, "c.py", 5),))
    assert policy_value_sets._touch_cell(row, {"c.py": {5: 'v == "aa"'}}, tmp_path) == "比較"


def test_touch_cell_reference_added_in_non_test_file(tmp_path: Path) -> None:
    assert policy_value_sets._touch_cell(_row(), {"u.py": {3: 'call("aa")'}}, tmp_path) == "参照"


def test_touch_cell_reference_in_test_file_is_ignored(tmp_path: Path) -> None:
    assert (
        policy_value_sets._touch_cell(_row(), {"tests/test_u.py": {3: 'call("aa")'}}, tmp_path)
        == "なし"
    )


def test_touch_cell_unquoted_value_is_not_a_reference(tmp_path: Path) -> None:
    assert policy_value_sets._touch_cell(_row(), {"u.py": {3: "# aa bb"}}, tmp_path) == "なし"


def test_touch_cell_does_not_count_definition_line_value_as_reference(tmp_path: Path) -> None:
    assert (
        policy_value_sets._touch_cell(_row(), {"d.py": {1: "X = ('aa', 'bb')"}}, tmp_path)
        == "定義"
    )


def test_touch_cell_joins_in_definition_reference_comparison_order(tmp_path: Path) -> None:
    row = _row(comparisons=(StringComparison("aa", ComparisonKind.EQUALS, "c.py", 5),))
    added = {"d.py": {1: "X"}, "c.py": {5: 'v == "aa"'}, "u.py": {2: "f('bb')"}}
    assert policy_value_sets._touch_cell(row, added, tmp_path) == "定義、参照、比較"


def test_added_lines_read_from_the_saved_diff(tmp_path: Path) -> None:
    review = make_review(tmp_path, ["a.py"], kind=ReviewKind.RANGE, base_tree=tmp_path / "base")
    directory = review.id.directory()
    directory.mkdir(parents=True)
    (directory / PAIRS_FILE).write_text(json.dumps([["a.py", "a.py"]]), encoding="utf-8")
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,4 @@\n keep\n-old\n+new\n+newer\n tail\n"
    (directory / "changes.diff").write_text(diff, encoding="utf-8")
    assert policy_value_sets._added_lines(review) == {"a.py": {2: "new", 3: "newer"}}


def _named_row() -> ValueSetRow:
    return _row(definitions=(ValueSetLocation("a/security.py", 1, "VIEWPOINTS"),))


def test_touch_cell_set_name_in_added_line_is_a_reference(tmp_path: Path) -> None:
    added = {"a/security.py": {3: "call(viewpoints=VIEWPOINTS)"}}
    assert policy_value_sets._touch_cell(_named_row(), added, tmp_path) == "参照"


def test_touch_cell_set_name_inside_another_word_is_not_a_reference(tmp_path: Path) -> None:
    added = {"u.py": {3: "call(MY_VIEWPOINTS_X)"}}
    assert policy_value_sets._touch_cell(_named_row(), added, tmp_path) == "なし"


def _two_viewpoint_rows() -> tuple[ValueSetRow, ValueSetRow]:
    micro = _row(definitions=(ValueSetLocation("a/micro.py", 1, "VIEWPOINTS"),))
    security = _row(definitions=(ValueSetLocation("a/security.py", 1, "VIEWPOINTS"),))
    return micro, security


def test_touch_cell_same_name_reference_is_limited_to_the_defining_module(tmp_path: Path) -> None:
    micro, security = _two_viewpoint_rows()
    added = {"a/micro.py": {9: "call(viewpoints=VIEWPOINTS)"}}
    assert policy_value_sets._touch_cell(micro, added, tmp_path) == "参照"
    assert policy_value_sets._touch_cell(security, added, tmp_path) == "なし"


def test_touch_cell_name_imported_from_the_module_is_a_reference(tmp_path: Path) -> None:
    micro, security = _two_viewpoint_rows()
    _write(tmp_path, {"a/macro.py": "from a.security import VIEWPOINTS\n\nuse(VIEWPOINTS)\n"})
    added = {"a/macro.py": {3: "use(VIEWPOINTS)"}}
    assert policy_value_sets._touch_cell(security, added, tmp_path) == "参照"
    assert policy_value_sets._touch_cell(micro, added, tmp_path) == "なし"


def test_touch_cell_name_qualified_by_the_module_stem_is_a_reference(tmp_path: Path) -> None:
    micro, security = _two_viewpoint_rows()
    added = {"a/macro.py": {3: "use(security.VIEWPOINTS)"}}
    assert policy_value_sets._touch_cell(security, added, tmp_path) == "参照"
    assert policy_value_sets._touch_cell(micro, added, tmp_path) == "なし"


def test_touch_cell_name_import_in_another_language_needs_stem_and_name(tmp_path: Path) -> None:
    row = _row(definitions=(ValueSetLocation("a/Security.java", 1, "NAMES"),))
    _write(tmp_path, {"a/Use.java": "import a.Security.NAMES;\nuse(NAMES);\n"})
    added = {"a/Use.java": {2: "use(NAMES);"}}
    assert policy_value_sets._touch_cell(row, added, tmp_path) == "参照"
    _write(tmp_path, {"a/Use.java": "import a.Other;\nuse(NAMES);\n"})
    assert policy_value_sets._touch_cell(row, added, tmp_path) == "なし"


def test_touch_cell_unreadable_body_does_not_count_an_import(tmp_path: Path) -> None:
    _, security = _two_viewpoint_rows()
    added = {"a/missing.py": {1: "use(VIEWPOINTS)"}}
    assert policy_value_sets._touch_cell(security, added, tmp_path) == "なし"


def test_small_tree_keeps_a_set_of_three_values(tmp_path: Path) -> None:
    files = {**_set_files("alpha"), "other/cmp.py": 'v == "alpha_1"\n'}
    _write(tmp_path, files)
    review = make_review(
        tmp_path,
        ["alpha/defs.py"],
        judgment_map={"segments": [whole("alpha/defs.py", 1)], "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )
    figure = policy_value_sets.value_set_table(review)
    assert figure is not None
    assert [row.cells[0] for row in figure.rows] == ["alpha/defs.py:1 ALPHA"]


def test_value_found_in_many_files_is_not_rare_in_a_large_tree(tmp_path: Path) -> None:
    files = {
        "defs.py": "COMMON = ('id', 'name', 'rare_a', 'rare_b')\n",
        **{f"use{i}.py": "id name\n" for i in range(10)},
    }
    review = _review(tmp_path, files, ["defs.py"])
    assert policy_value_sets.value_set_table(review) is None


def test_definition_cell_shows_named_literal_and_undetermined_places() -> None:
    row = _row(
        definitions=(
            ValueSetLocation("a.py", 1, "STATUSES", DefinitionKind.NAMED),
            ValueSetLocation("b.py", 2, None, DefinitionKind.LITERAL),
            ValueSetLocation("c.rb", 3),
        )
    )
    assert policy_value_sets._definition_cell(row) == "a.py:1 STATUSES, b.py:2（リテラル）, c.rb:3"


def _counting_value_set_rows(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    real = value_sets.value_set_rows

    def counting(root: Path) -> tuple[ValueSetRow, ...]:
        calls.append(root)
        return real(root)

    monkeypatch.setattr(policy_value_sets, "value_set_rows", counting)
    return calls


def test_one_policy_estimate_reads_value_sets_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path, _set_files("alpha"), ["alpha/use.py"])
    assert policy_value_sets.value_set_table(review) is not None
    calls = _counting_value_set_rows(monkeypatch)
    estimate_policy(AssemblyInput.load(review))
    assert calls == [tmp_path]


def test_remembered_value_sets_give_the_same_principle_prompts(tmp_path: Path) -> None:
    review = _review(tmp_path, _set_files("alpha"), ["alpha/use.py"])
    reused = AssemblyInput.load(review)
    first = principle_prompts(reused)
    # 覚えた後に組み直しても、新しい AssemblyInput で組んだものと文字列で一致する
    assert principle_prompts(reused) == first
    assert principle_prompts(AssemblyInput.load(review)) == first
    assert any(TITLES_VALUE_SETS in item.prompt for item in first)
