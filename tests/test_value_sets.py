from pathlib import Path

from jigane.languages.base import ComparisonKind, DefinitionKind, ValueSetReader
from jigane.languages.python import PythonAdapter
from jigane.policy_materials import value_sets
from jigane.policy_materials.value_sets import (
    FileCounts,
    ValueSetLocation,
    build_value_set_rows,
    value_set_rows,
)

ASPECTS = ["alpha_x", "beta_x", "gamma_x", "delta_x"]


def _found(source: str) -> list[tuple[int, set[str]]]:
    return [(v.line, set(v.values)) for v in PythonAdapter().value_sets(source, "m.py")]


def test_collects_literal_sequences_of_three_or_more_short_strings() -> None:
    source = (
        'A = ("a1", "b1", "c1")\n'
        'B = {"a2", "b2", "c2"}\n'
        'C = frozenset({"a3", "b3", "c3"})\n'
        'D = ["a4", "b4", "c4"]\n'
        'E = ("a5", "b5")\n'
        'F = ("a6", "b6", 3)\n'
        f'G = ("{"x" * 41}", "b7", "c7")\n'
    )
    assert [line for line, _ in _found(source)] == [1, 2, 3, 4]


def test_collects_enum_member_values() -> None:
    source = (
        "from enum import StrEnum\n"
        "class Color(StrEnum):\n"
        '    RED = "red_v"\n'
        '    GREEN = "green_v"\n'
        '    BLUE = "blue_v"\n'
    )
    assert _found(source) == [(2, {"red_v", "green_v", "blue_v"})]


def _names(source: str) -> list[str | None]:
    return [v.name for v in PythonAdapter().value_sets(source, "m.py")]


def test_names_come_from_assignments_and_annotated_assignments() -> None:
    source = (
        'A = ("a1", "b1", "c1")\n'
        'B: tuple[str, ...] = ("a2", "b2", "c2")\n'
        'C = frozenset({"a3", "b3", "c3"})\n'
        'obj.D = ["a4", "b4", "c4"]\n'
    )
    assert _names(source) == ["A", "B", "C", "D"]


def test_enum_name_is_the_class_name() -> None:
    source = 'class Kind(StrEnum):\n    A = "a"\n    B = "b"\n    C = "c"\n'
    assert _names(source) == ["Kind"]


def test_unnamed_sequence_has_no_name() -> None:
    assert _names('call(("a1", "b1", "c1"))\nx, y = ("a2", "b2", "c2"), 1\n') == [None, None]


def test_adapter_satisfies_the_reader_protocol() -> None:
    assert isinstance(PythonAdapter(), ValueSetReader)


def _repository(extra_common_files: int = 100) -> dict[str, str]:
    files = {f"pkg/common{i}.py": "x = 1\n" for i in range(extra_common_files)}
    files["pkg/a.py"] = f"ASPECTS = {tuple(ASPECTS)!r}\nCOMMON = ('True', 'list', 'None')\n"
    files["pkg/b.py"] = 'if v == "alpha_x" or v == "beta_x" or v == "gamma_x":\n    pass\n'
    files["tests/test_b.py"] = "alpha_x beta_x gamma_x\n"
    files["docs/guide.md"] = "alpha_x beta_x gamma_x delta_x\n"
    for i in range(extra_common_files):
        files[f"pkg/common{i}.py"] += "True list None\n"
    return files


def test_rare_values_drop_words_found_in_most_files() -> None:
    rows = build_value_set_rows(_repository())
    assert len(rows) == 1
    assert rows[0].values == tuple(sorted(ASPECTS))
    assert rows[0].definitions == (
        ValueSetLocation("pkg/a.py", 1, "ASPECTS", DefinitionKind.NAMED),
    )
    assert rows[0].files == FileCounts(code=2, test=1, document=1)


def test_overlapping_sets_are_merged_and_disjoint_ones_are_not() -> None:
    files = _repository()
    files["pkg/c.py"] = (
        "OTHER = ('alpha_x', 'beta_x', 'gamma_x', 'extra_x')\nZ = ('p_y', 'q_y', 'r_y')\n"
    )
    rows = build_value_set_rows(files)
    merged = next(r for r in rows if "extra_x" in r.values)
    assert merged.values == tuple(sorted([*ASPECTS, "extra_x"]))
    assert [d.path for d in merged.definitions] == ["pkg/a.py", "pkg/c.py"]
    assert any(r.values == ("p_y", "q_y", "r_y") for r in rows)
    assert len(rows) == 2


def test_threshold_constants_are_defined() -> None:
    assert value_sets.RARE_VALUE_RATIO == 0.05


def test_reads_rows_from_a_directory(tmp_path: Path) -> None:
    for path, text in _repository().items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    assert [row.values for row in value_set_rows(tmp_path)] == [tuple(sorted(ASPECTS))]


def test_values_with_symbols_are_counted_by_pattern() -> None:
    files = {f"pkg/common{i}.py": "x = 1\n" for i in range(100)}
    files["pkg/a.py"] = "S = ('a b', 'c.d', 'e-f')\n"
    files["docs/g.md"] = "a b and c.d and e-f\n"
    rows = build_value_set_rows(files)
    assert [row.values for row in rows] == [("a b", "c.d", "e-f")]
    assert rows[0].files == FileCounts(code=1, test=0, document=1)


def test_rows_with_equal_counts_sort_by_definition() -> None:
    files = {f"pkg/common{i}.py": "x = 1\n" for i in range(100)}
    files["pkg/a.py"] = "S = ('a_1', 'b_1', 'c_1')\nT = ('a_2', 'b_2', 'c_2')\n"
    rows = build_value_set_rows(files)
    assert [row.definitions[0].line for row in rows] == [1, 2]


def _comparisons(source: str) -> list[tuple[int, ComparisonKind, str]]:
    return [(c.line, c.kind, c.value) for c in PythonAdapter().string_comparisons(source, "m.py")]


def test_collects_equality_and_inequality_in_either_order() -> None:
    source = 'a == "x1"\n"y1" != b\nc == d\ne == 3\n'
    assert _comparisons(source) == [
        (1, ComparisonKind.EQUALS, "x1"),
        (2, ComparisonKind.EQUALS, "y1"),
    ]


def test_collects_membership_in_inline_and_named_sets() -> None:
    source = (
        'NAMES = frozenset({"n1", "n2", "n3"})\n'
        'if a in ("i1", "i2"):\n'
        "    pass\n"
        "if b not in NAMES:\n"
        "    pass\n"
        "if c in UNKNOWN:\n"
        "    pass\n"
    )
    assert sorted(_comparisons(source)) == [
        (2, ComparisonKind.MEMBERSHIP, "i1"),
        (2, ComparisonKind.MEMBERSHIP, "i2"),
        (4, ComparisonKind.MEMBERSHIP, "n1"),
        (4, ComparisonKind.MEMBERSHIP, "n2"),
        (4, ComparisonKind.MEMBERSHIP, "n3"),
    ]


def test_collects_prefix_suffix_and_regex_matches() -> None:
    source = (
        'a.startswith("p1")\n'
        'a.endswith(("s1", "s2"))\n'
        "import re\n"
        're.match(r"^q+$", a)\n'
        'a.startswith("")\n'
        "a.startswith(prefix)\n"
    )
    assert _comparisons(source) == [
        (1, ComparisonKind.PREFIX, "p1"),
        (2, ComparisonKind.SUFFIX, "s1"),
        (2, ComparisonKind.SUFFIX, "s2"),
        (4, ComparisonKind.REGEX, "^q+$"),
    ]


def test_unparsable_source_has_no_comparisons() -> None:
    assert _comparisons("def (:\n") == []


def _row_comparisons(extra: dict[str, str]) -> list[tuple[str, int, ComparisonKind]]:
    files = {**_repository(), **extra}
    row = build_value_set_rows(files)[0]
    return [(c.path, c.line, c.kind) for c in row.comparisons]


def test_row_binds_equality_membership_and_regex_comparisons_by_value() -> None:
    found = _row_comparisons(
        {
            "pkg/c.py": (
                'a == "delta_x"\n'
                'b in ("alpha_x", "beta_x")\n'
                'import re\nre.search("gamma_x", c)\n'
                'd == "unrelated"\n'
            )
        }
    )
    assert ("pkg/c.py", 1, ComparisonKind.EQUALS) in found
    assert ("pkg/c.py", 2, ComparisonKind.MEMBERSHIP) in found
    assert ("pkg/c.py", 4, ComparisonKind.REGEX) in found
    assert not any(line == 5 for _, line, _ in found)
    # 同じ行の `in` は要素ごとに返るが、行と形で一つにまとまる
    assert sum(1 for path, line, _ in found if (path, line) == ("pkg/c.py", 2)) == 1


def test_row_binds_affix_comparisons_when_any_value_has_the_affix() -> None:
    found = _row_comparisons(
        {"pkg/c.py": 'a.startswith("alpha")\na.endswith("_x")\na.startswith("zzz")\n'}
    )
    assert ("pkg/c.py", 1, ComparisonKind.PREFIX) in found
    assert ("pkg/c.py", 2, ComparisonKind.SUFFIX) in found
    assert not any(line == 3 for _, line, _ in found)


def _chain(values: list[str]) -> str:
    head, *rest = values
    lines = [f'if mode == "{head}":\n    pass']
    lines += [f'elif mode == "{value}":\n    pass' for value in rest]
    return "\n".join(lines) + "\n"


def test_value_set_compared_only_in_an_if_chain_is_a_row() -> None:
    files = {"pkg/a.py": _chain(["fast_m", "safe_m", "dry_m"]), "pkg/b.py": "x = 1\n"}
    rows = build_value_set_rows(files)
    assert len(rows) == 1
    assert rows[0].values == ("dry_m", "fast_m", "safe_m")
    assert rows[0].subject == "mode"
    assert rows[0].definitions == ()
    assert [c.line for c in rows[0].comparisons] == [1, 3, 5]


def test_two_distinct_compared_values_are_not_a_row() -> None:
    assert build_value_set_rows({"pkg/a.py": _chain(["fast_m", "safe_m", "fast_m"])}) == ()


def test_merged_row_keeps_the_kind_of_each_definition() -> None:
    files = _repository()
    files["pkg/c.py"] = "use(('alpha_x', 'beta_x', 'gamma_x', 'extra_x'))\n"
    rows = build_value_set_rows(files)
    assert [(d.path, d.name, d.kind) for d in rows[0].definitions] == [
        ("pkg/a.py", "ASPECTS", DefinitionKind.NAMED),
        ("pkg/c.py", None, DefinitionKind.LITERAL),
    ]
