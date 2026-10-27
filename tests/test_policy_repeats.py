import time
from dataclasses import replace
from pathlib import Path

import pytest
from review_fakes import with_map, with_range_source

from jigane.languages.csharp import CSharpAdapter
from jigane.languages.python import PythonAdapter
from jigane.reviews.lifecycle import FeatureOrigin, Review, create_feature_review
from jigane.skills import policy_repeats, policy_tree

REMOVED = "value = compute_total_amount(x, rate)"
ADDED = "return compute_new_total_value(x, 10)"
BLOCK = ["total = compute_new_total_value(x, 10)", "log(total)", "return total"]


def repeats_text(review: Review) -> str:
    return policy_repeats.repeats_text(review, policy_tree.trees_of(review).after)


def block(indent: str = "    ") -> str:
    return "".join(f"{indent}{line}\n" for line in BLOCK)


@pytest.fixture
def seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    """識別子と宣言を借りるためだけの機能レビュー。"""
    root = tmp_path / "seed"
    root.mkdir()
    (root / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return create_feature_review(root, ["."], "seed")


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def diff_review(
    seed: Review, tmp_path: Path, base: dict[str, str], head: dict[str, str]
) -> Review:
    base_root, head_root = tmp_path / "base", tmp_path / "head"
    write(base_root, base)
    write(head_root, head)
    paths = sorted(set(base) | set(head))
    segments = [
        {"old_path": p if p in base else None, "new_path": p if p in head else None}
        for p in paths
        if base.get(p) != head.get(p)
    ]
    return with_range_source(
        replace(
            with_map(seed, {**seed.map_kind.to_dict(), "segments": segments}),
            repository_root=str(head_root),
            paths=sorted(head),
        ),
        base_root,
    )


def feature_review(seed: Review, tmp_path: Path, files: dict[str, str]) -> Review:
    root = tmp_path / "feature"
    write(root, files)
    return replace(seed, origin=FeatureOrigin(), repository_root=str(root), paths=sorted(files))


def make_pair(seed: Review, tmp_path: Path, extra: dict[str, str]) -> Review:
    base = {"a.py": f"def f(x, rate):\n    {REMOVED}\n    return value\n", **extra}
    head = {"a.py": f"def f(x, rate):\n    {ADDED}\n", **extra}
    return diff_review(seed, tmp_path, base, head)


def test_removed_line_still_present_elsewhere(seed: Review, tmp_path: Path) -> None:
    review = make_pair(
        seed, tmp_path, {"b.py": f"def g():\n    {REMOVED}\n\ndef h():\n    {REMOVED}\n"}
    )
    text = repeats_text(review)
    section = text.split("#### 同じ並びが他にもある塊")[0]
    assert "#### 直した形が残る箇所" in section
    assert f"| `{REMOVED}` | a.py:2 | 2 | b.py:2, b.py:5 |" in section


def test_removed_line_gone_everywhere_says_so(seed: Review, tmp_path: Path) -> None:
    text = repeats_text(make_pair(seed, tmp_path, {}))
    assert policy_repeats.NO_REMAINS_TEXT in text
    assert policy_repeats.NO_REPEATS_TEXT in text


def test_added_block_repeated_elsewhere(seed: Review, tmp_path: Path) -> None:
    base = {"a.py": "def f():\n    pass\n", "d.py": f"def g(x):\n{block()}"}
    head = {"a.py": f"def f():\n{block()}", "d.py": f"def g(x):\n{block()}"}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    section = text.split("#### 同じ並びが他にもある塊")[1]
    assert "| 変更した側 | 行数 | 同じ並びの場所の数 | 同じ並びの場所の例 | 代表の行 |" in section
    assert f"| a.py:2-4 | 3 | 1 | d.py:2-4 | `{BLOCK[0]}` |" in section
    assert policy_repeats.NO_REMAINS_TEXT in text


def test_two_line_match_is_not_a_block(seed: Review, tmp_path: Path) -> None:
    other = "".join(f"    {line}\n" for line in BLOCK[:2])
    base = {"a.py": "def f():\n    pass\n", "d.py": f"def g(x):\n{other}"}
    head = {"a.py": f"def f():\n{other}", "d.py": f"def g(x):\n{other}"}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    assert policy_repeats.NO_REPEATS_TEXT in text


def test_block_of_only_short_lines_is_ignored(seed: Review, tmp_path: Path) -> None:
    short = "    a = 1\n    b = 2\n    c = 3\n"
    base = {"a.py": "def f():\n    pass\n", "d.py": f"def g(x):\n{short}"}
    head = {"a.py": f"def f():\n{short}", "d.py": f"def g(x):\n{short}"}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    assert policy_repeats.NO_REPEATS_TEXT in text


def test_match_stops_at_a_line_that_differs(seed: Review, tmp_path: Path) -> None:
    changed = block() + "    return other_total_value_that_differs\n"
    base = {"a.py": "def f():\n    pass\n", "d.py": f"def g(x):\n{block()}    return x\n"}
    head = {"a.py": f"def f():\n{changed}", "d.py": base["d.py"]}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    assert "| a.py:2-4 | 3 | 1 | d.py:2-4 |" in text


def test_overlapping_matches_are_one_block(seed: Review, tmp_path: Path) -> None:
    lines = [f"    value_{i} = compute_something_long_{i}(argument)" for i in range(6)]
    base = {
        "a.py": "def f():\n    pass\n",
        "d.py": "def g():\n" + "\n".join(lines[:4]) + "\n",
        "e.py": "def h():\n" + "\n".join(lines[2:]) + "\n",
    }
    head = {**base, "a.py": "def f():\n" + "\n".join(lines) + "\n"}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    rows = [r for r in text.splitlines() if r.startswith("| a.py")]
    assert len(rows) == 1
    assert rows[0].startswith("| a.py:2-7 | 6 | 2 | d.py:2-5, e.py:2-5 |")


def test_same_block_added_twice_is_one_row(seed: Review, tmp_path: Path) -> None:
    base = {"a.py": "def f():\n    pass\n", "b.py": "def g():\n    pass\n"}
    head = {"a.py": f"def f():\n{block()}", "b.py": f"def g():\n{block()}"}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    rows = [r for r in text.splitlines() if r.startswith(("| a.py", "| b.py"))]
    assert len(rows) == 1


def test_test_files_are_excluded_on_both_sides(seed: Review, tmp_path: Path) -> None:
    base = {
        "a.py": f"def f():\n    {REMOVED}\n",
        "tests/test_a.py": f"def test_a():\n    {REMOVED}\n",
        "tests/test_b.py": "def test_b():\n    pass\n",
        "d.py": f"def g():\n{block()}",
    }
    head = {
        "a.py": f"def f():\n{block()}",
        "tests/test_a.py": f"def test_a():\n    {REMOVED}\n",
        "tests/test_b.py": f"def test_b():\n{block()}",
        "d.py": f"def g():\n{block()}",
    }
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    assert "tests/" not in text
    assert policy_repeats.NO_REMAINS_TEXT in text
    assert "d.py:2-4" in text


def test_idiom_lines_with_many_other_places_are_dropped(seed: Review, tmp_path: Path) -> None:
    many = f"def g():\n{(REMOVED + chr(10)).replace(REMOVED, '    ' + REMOVED) * 11}"
    text = repeats_text(make_pair(seed, tmp_path, {"b.py": many}))
    section = text.split("#### 同じ並びが他にもある塊")[0]
    assert f"`{REMOVED}`" not in section
    assert "慣用句として外した（1 件）" in section
    assert policy_repeats.NO_REMAINS_TEXT in section


def test_moved_line_is_not_a_remaining_one(seed: Review, tmp_path: Path) -> None:
    base = {"a.py": f"def f():\n    {REMOVED}\n    pass\n"}
    head = {"a.py": f"def f():\n    pass\n    {REMOVED}\n"}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    assert policy_repeats.NO_REMAINS_TEXT in text


def test_short_import_comment_and_symbol_lines_are_ignored(seed: Review, tmp_path: Path) -> None:
    ignored = [
        "x = 1",
        "from package.module_with_long_name import something_else",
        "import some_really_long_module_name_here_now",
        "# this is a long comment line that repeats elsewhere",
        "-" * 40,
    ]
    shared = "\n".join(f"    {line}" for line in ignored)
    review = make_pair(seed, tmp_path, {"b.py": f"def g():\n{shared}\n"})
    base = review.diff_origin.base_tree  # type: ignore[union-attr]
    (Path(base) / "a.py").write_text(f"def f():\n{shared}\n", encoding="utf-8")
    (Path(review.repository_root) / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    text = repeats_text(review)
    for line in ignored:
        assert line not in text
    assert policy_repeats.NO_REMAINS_TEXT in text


def test_comment_inside_block_comment_is_ignored(seed: Review, tmp_path: Path) -> None:
    docstring = '"""\n    prose that repeats the removed implementation line\n    """'
    review = make_pair(seed, tmp_path, {"b.py": f"def g():\n    {docstring}\n"})
    (Path(review.diff_origin.base_tree) / "a.py").write_text(  # type: ignore[union-attr]
        f"def f():\n    {docstring}\n", encoding="utf-8"
    )
    text = repeats_text(review)
    # docstring は文字列であってコメントではないため、数える対象に残る
    assert "prose that repeats" in text


def test_row_cap_and_remainder(seed: Review, tmp_path: Path) -> None:
    lines = [f"    value_{i:02d} = compute_something_long_{i:02d}(argument)" for i in range(22)]
    base = {"a.py": "def f():\n" + "\n".join(lines) + "\n"}
    # 行 i は b.py に i % 10 + 1 回ある。多い順に並ぶので、10 回の行が最上位に来る
    other = "".join(f"{line}\n" * (i % 10 + 1) for i, line in enumerate(lines))
    base["b.py"] = other
    head = {"a.py": "def f():\n    pass\n", "b.py": other}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    section = text.split("#### 同じ並びが他にもある塊")[0]
    assert section.count("| `value_") == policy_repeats.LINE_CAP
    assert "ほか 2 件" in section
    first_row = next(row for row in section.splitlines() if row.startswith("| `value_"))
    assert first_row.startswith("| `value_09")
    assert "| 10 |" in first_row


def test_same_line_removed_several_times_is_one_row(seed: Review, tmp_path: Path) -> None:
    base = {"a.py": f"def f():\n    {REMOVED}\n    {REMOVED}\n", "b.py": f"    {REMOVED}\n"}
    head = {"a.py": "def f():\n    pass\n", "b.py": f"    {REMOVED}\n"}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    assert text.count(f"`{REMOVED}`") == 1
    assert f"| `{REMOVED}` | a.py:2, a.py:3 | 1 | b.py:1 |" in text


def test_symlinked_file_is_not_counted(seed: Review, tmp_path: Path) -> None:
    review = make_pair(seed, tmp_path, {"b.py": f"def g():\n    {REMOVED}\n"})
    head = Path(review.repository_root)
    outside = tmp_path / "outside.py"
    outside.write_text(f"{REMOVED}\n", encoding="utf-8")
    (head / "link.py").symlink_to(outside)
    (head / "linked_b.py").symlink_to(head / "b.py")
    text = repeats_text(review)
    assert f"| `{REMOVED}` | a.py:2 | 1 | b.py:2 |" in text
    assert "link.py" not in text


def test_cell_escapes_pipe_and_backtick_and_cuts_body(seed: Review, tmp_path: Path) -> None:
    line = "result = first_value | `second_value` | " + "x" * 200
    review = make_pair(seed, tmp_path, {"b.py": f"{line}\n"})
    (Path(review.diff_origin.base_tree) / "a.py").write_text(  # type: ignore[union-attr]
        f"{line}\n", encoding="utf-8"
    )
    (Path(review.repository_root) / "a.py").write_text("pass\n", encoding="utf-8")
    text = repeats_text(review)
    row = next(r for r in text.splitlines() if r.startswith("| `result"))
    assert "\\|" in row
    assert "'second_value'" in row
    assert row.split("` |")[0].endswith("…")
    assert len(row.split("` |")[0]) < policy_repeats.BODY_CAP + 30


def test_feature_review_has_no_remaining_section(seed: Review, tmp_path: Path) -> None:
    review = feature_review(
        seed,
        tmp_path,
        {"a.py": f"def f():\n{block()}", "b.py": f"def g():\n{block()}"},
    )
    text = repeats_text(review)
    assert "直した形が残る箇所" not in text
    assert "#### 同じ並びが他にもある塊" in text
    assert f"| a.py:2-4 | 3 | 1 | b.py:2-4 | `{BLOCK[0]}` |" in text
    assert not [row for row in text.splitlines() if row.startswith("| b.py")]


def test_files_without_a_language_adapter_are_not_searched(seed: Review, tmp_path: Path) -> None:
    review = make_pair(seed, tmp_path, {"notes.md": f"{REMOVED}\n"})
    assert policy_repeats.NO_REMAINS_TEXT in repeats_text(review)


@pytest.mark.parametrize("line", ["    value = compute_new_total_value(x, 10)", "    x = 1"])
def test_file_of_thousands_of_identical_lines_finishes_quickly(
    seed: Review, tmp_path: Path, line: str
) -> None:
    body = f"def f():\n{(line + chr(10)) * 3500}"
    review = feature_review(seed, tmp_path, {"a.py": body, "b.py": body})
    # 実時間の上限。処理の回数を数える差し込み口が無いので時間で測る。正常な線形の走りは
    # 約 0.2 秒（実測）で、上限の 10 秒は約 50 倍の余裕になる。二乗の計算量に戻ると、
    # 3500 行 × 3500 行 × 2 ファイルで 2 千万回超の比較になり、1 回 1 マイクロ秒としても
    # 20 秒を超える。負荷で数倍遅れても正常側は落ちず、バグ側は必ず超える。
    started = time.monotonic()
    text = repeats_text(review)
    # jigane: allow-flaky FLK-WALL-CLOCK -- 期待 0.2 秒に約 50 倍の余裕
    assert time.monotonic() - started < 10
    # 長い行は慣用句として種にせず、短い行は一行の一致の対象に満たないので、どちらも塊にならない
    assert policy_repeats.NO_REPEATS_TEXT in text


CSHARP_STEPS = [
    "var total = ComputeNewTotalValue(request, 10);",
    "await WriteAuditEntryAsync(total, token);",
    "return Results.Ok(total);",
]


def csharp(steps: list[str], between: list[str], name: str = "A") -> str:
    """手順の間に between の行を挟んだ C# のメソッド。"""
    gap = "".join(f"        {line}\n" for line in between)
    body = "".join(f"        {step}\n{gap}" for step in steps)
    return f"class Handler{name}\n{{\n    void Run{name}()\n    {{\n{body}    }}\n}}\n"


@pytest.mark.parametrize(
    "between", [["{", "}"], ["", "// explain the next step"], ["{", "", "// note", "}"]]
)
def test_unmatchable_lines_do_not_break_a_block(
    seed: Review, tmp_path: Path, between: list[str]
) -> None:
    files = {
        "A.cs": csharp(CSHARP_STEPS, between),
        "B.cs": csharp(CSHARP_STEPS, ["{", "}"], "B"),
    }
    text = repeats_text(feature_review(seed, tmp_path, files))
    rows = [r for r in text.splitlines() if r.startswith("| A.cs")]
    assert len(rows) == 1
    # 行数は一致に使える行の数で、範囲は元の行番号
    assert rows[0].startswith("| A.cs:5-")
    assert " | 3 | 1 | B.cs:5-" in rows[0]


def test_changed_side_breaks_at_an_unchanged_matchable_line(seed: Review, tmp_path: Path) -> None:
    middle = "var untouched = ComputeUntouchedValue(request);"
    steps = [*CSHARP_STEPS[:2], middle, CSHARP_STEPS[2]]
    base = {"A.cs": csharp([middle], ["{", "}"]), "B.cs": csharp(steps, ["{", "}"], "B")}
    head = {**base, "A.cs": csharp(steps, ["{", "}"])}
    # 足したのは middle の前の 2 行と後ろの 1 行で、変更していない middle で並びが切れる
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    assert policy_repeats.NO_REPEATS_TEXT in text


def test_representative_line_is_the_first_eligible_line(seed: Review, tmp_path: Path) -> None:
    steps = ["return", *CSHARP_STEPS]
    files = {"A.cs": csharp(steps, ["{", "}"]), "B.cs": csharp(steps, ["{", "}"], "B")}
    text = repeats_text(feature_review(seed, tmp_path, files))
    row = next(r for r in text.splitlines() if r.startswith("| A.cs"))
    assert row.endswith(f"| `{CSHARP_STEPS[0]}` |")


def test_matchable_lines_judge_comments_and_imports_by_syntax_tree_with_an_adapter() -> None:
    code = (
        "using System.Collections.Generic;\n"
        "// a comment line with enough text in it\n"
        "class A { void F() { using (var reader = Open()) { Run(reader); } } }\n"
    )
    found = policy_repeats._matchable_lines(CSharpAdapter(), code)
    assert [number for number, _ in found] == [3]


def test_matchable_lines_do_not_apply_the_appearance_fallback_with_an_adapter() -> None:
    # 文字列の中の行が "//" や "import " で始まっていても、構文木ではコメントでも import でもない
    code = 'x = """\n// not a comment, a string line\nimport this is text\n"""\n'
    found = policy_repeats._matchable_lines(PythonAdapter(), code)
    assert [number for number, _ in found] == [1, 2, 3]


def test_matchable_lines_fall_back_to_the_appearance_without_an_adapter() -> None:
    code = (
        "# note\n// note\n/* note\n* note\nimport foo\nusing Foo\nfrom a import b\n"
        "plain_line = 1\n}\n"
    )
    found = policy_repeats._matchable_lines(None, code)
    assert found == [(8, "plain_line = 1")]


LONG_BLOCK = [f"step_number_{n} = compute_long_value_{n}(request)" for n in range(6)]


def two_copy_review(seed: Review, tmp_path: Path) -> Review:
    short = f"def f():\n{block()}"
    long = "def g():\n" + "".join(f"    {line}\n" for line in LONG_BLOCK)
    files = {"a.py": short, "b.py": short, "c.py": long, "d.py": long}
    return feature_review(seed, tmp_path, files)


def test_line_limit_attaches_lines_to_blocks_up_to_the_limit(seed: Review, tmp_path: Path) -> None:
    review = two_copy_review(seed, tmp_path)
    text = policy_repeats.repeats_text(review, policy_tree.trees_of(review).after, line_limit=5)
    short = next(r for r in text.splitlines() if r.startswith("| a.py"))
    long = next(r for r in text.splitlines() if r.startswith("| c.py"))
    assert f"`def f(): ⏎ {BLOCK[0]} ⏎ {BLOCK[1]} ⏎ {BLOCK[2]}`" in short
    assert LONG_BLOCK[0] not in long
    assert long.endswith("|  |")
    assert "5 行以下の塊だけ" in text
    assert "代表の行" not in text


def test_zero_line_limit_attaches_no_lines(seed: Review, tmp_path: Path) -> None:
    review = two_copy_review(seed, tmp_path)
    text = policy_repeats.repeats_text(review, policy_tree.trees_of(review).after, line_limit=0)
    assert BLOCK[1] not in text
    assert "行の本文" not in text


def test_unlimited_keeps_the_representative_line_for_every_block(
    seed: Review, tmp_path: Path
) -> None:
    review = two_copy_review(seed, tmp_path)
    text = repeats_text(review)
    assert "代表の行" in text
    assert f"`{LONG_BLOCK[0]}` |" in text


def test_a_block_repeated_in_the_same_file_without_overlap_is_found(
    seed: Review, tmp_path: Path
) -> None:
    base = {"a.py": f"def f():\n    pass\n\ndef g(x):\n{block()}"}
    head = {"a.py": f"def f():\n{block()}\ndef g(x):\n{block()}"}
    text = repeats_text(diff_review(seed, tmp_path, base, head))
    section = text.split("#### 同じ並びが他にもある塊")[1]
    assert f"| a.py:2-4 | 3 | 1 | a.py:7-9 | `{BLOCK[0]}` |" in section
