from pathlib import Path

import pytest
from pr_support import git

from jigane.design.mentions import Mention, MentionKind
from jigane.design.parse import parse_design
from jigane.design.recommend import (
    _REPLACE_CALLS,
    _WRITE_CALLS,
    _WRITE_INFIX,
    _WRITE_PREFIX,
    AccessorCallerReason,
    Ambiguous,
    Candidate,
    CandidateKind,
    Found,
    Location,
    LocationKind,
    Match,
    NamedReason,
    NotFound,
    ReaderReason,
    Reason,
    Recommendation,
    Trace,
    recommend,
)

FILES = {
    "shop/cart.py": """\
from shop.pricing import Pricing


class Cart:
    def checkout(self) -> int:
        return Pricing().total(self)
""",
    "shop/pricing.py": """\
class Pricing:
    def total(self, cart: object) -> int:
        return 1
""",
    "shop/app.py": """\
from shop.cart import Cart


def main() -> int:
    return Cart().checkout()
""",
    "shop/unrelated.py": """\
class Unrelated:
    def noop(self) -> None:
        return None
""",
    "shop/store.py": 'REVIEWS = "reviews.json"\n',
    "shop/cli/commands/run.py": """\
def register(subparsers):
    run = subparsers.add_parser("run")
    run.add_argument("--limit")
""",
    "tests/test_cart.py": "from shop.cart import Cart\n\n\ndef test() -> None:\n    Cart()\n",
}

DOCUMENT = """\
# 題

## 1. 序論

`Cart` を直す。`shop/store.py` と `reviews.json` と `Ghost` が出る。

## 2. 設計

```mermaid
classDiagram
    class Cart
```

`jigane run --limit 3` を使う。`missing/none.py` は無い。
"""


def _work(tmp_path: Path) -> Path:
    for relative, text in FILES.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def _recommend(tmp_path: Path, limit: int = 8) -> Recommendation:
    return recommend(_work(tmp_path), parse_design(DOCUMENT), limit)


def test_direct_hits_rank_above_one_step_and_sections_count(tmp_path: Path) -> None:
    result = _recommend(tmp_path)
    by_path = {c.path: c for c in result.candidates}
    cart = by_path["shop/cart.py"]
    assert cart.kind is CandidateKind.DIRECT
    # Cart は 1 節の本文と 2 節のクラス図で名指された
    assert cart.section_count == 2
    assert result.candidates[0].path == "shop/cart.py"


def _traces(reasons: tuple[Reason, ...], name: str) -> set[Trace]:
    return {r.trace for r in reasons if r.mention.name == name}


def test_one_step_callers_and_callees(tmp_path: Path) -> None:
    by_path = {c.path: c for c in _recommend(tmp_path).candidates}
    assert by_path["shop/pricing.py"].kind is CandidateKind.ONE_STEP
    assert _traces(by_path["shop/pricing.py"].reasons, "Cart") == {Trace.CALLEE}
    assert by_path["shop/app.py"].kind is CandidateKind.ONE_STEP
    assert _traces(by_path["shop/app.py"].reasons, "Cart") == {Trace.CALLER}
    assert "shop/unrelated.py" not in by_path
    assert "tests/test_cart.py" not in by_path


def test_reasons_are_structured_values(tmp_path: Path) -> None:
    by_path = {c.path: c for c in _recommend(tmp_path).candidates}
    cart = by_path["shop/cart.py"]
    assert [(r.mention.name, r.mention.via.value, r.trace) for r in cart.reasons] == [
        ("Cart", "code_span", Trace.NAMED),
        ("Cart", "class_diagram", Trace.NAMED),
    ]
    assert [r.mention.section.title for r in cart.reasons if r.mention.section] == [
        "1. 序論",
        "2. 設計",
    ]
    run = by_path["shop/cli/commands/run.py"]
    assert {r.mention.name for r in run.reasons} == {"run", "--limit"}
    store = by_path["shop/store.py"]
    assert {r.mention.name for r in store.reasons} == {"shop/store.py", "reviews.json"}


def test_limit_cuts_and_counts_omitted(tmp_path: Path) -> None:
    full = _recommend(tmp_path, limit=100)
    cut = _recommend(tmp_path, limit=2)
    assert [c.path for c in cut.candidates] == [c.path for c in full.candidates[:2]]
    assert cut.omitted == len(full.candidates) - 2
    assert full.omitted == 0


def _kinds(match: Match) -> list[str]:
    assert isinstance(match, Found)
    return [loc.kind for loc in match.locations]


def test_match_table_shows_unresolved_mentions(tmp_path: Path) -> None:
    matches = {m.mention.name: m for m in _recommend(tmp_path).matches}
    assert _kinds(matches["Cart"]) == ["class"]
    assert _kinds(matches["shop/store.py"]) == ["file"]
    assert _kinds(matches["run"]) == ["subcommand"]
    assert _kinds(matches["--limit"]) == ["option"]
    assert _kinds(matches["reviews.json"]) == ["stored_file"]
    assert isinstance(matches["Ghost"], NotFound)
    assert isinstance(matches["missing/none.py"], NotFound)


def test_function_and_variable_declarations_show_their_kind(tmp_path: Path) -> None:
    root = _work(tmp_path)
    (root / "shop/util.py").write_text(
        "MAX_LIMIT = 3\n\n\ndef bounded() -> int:\n    return 1\n", encoding="utf-8"
    )
    document = parse_design("# 題\n\n## 1. 序論\n\n`bounded()` と `MAX_LIMIT`。\n")
    matches = {m.mention.name: m for m in recommend(root, document).matches}
    assert _kinds(matches["bounded"]) == ["function"]
    assert _kinds(matches["MAX_LIMIT"]) == ["variable"]


def test_same_input_gives_same_result(tmp_path: Path) -> None:
    document = parse_design(DOCUMENT)
    root = _work(tmp_path)
    assert recommend(root, document) == recommend(root, document)


def test_review_store_design_recommends_review(tmp_path: Path) -> None:
    del tmp_path
    root = Path(__file__).resolve().parents[1]
    text = (root / "tests/fixtures/design/review-store.md").read_text(encoding="utf-8")
    document = parse_design(text)
    result = recommend(root, document)
    top = [c.path for c in result.candidates[:3]]
    assert top[0] == "jigane/reviews/lifecycle.py"
    review = next(m for m in result.matches if m.mention.name == "Review")
    assert isinstance(review, Found)


def test_tests_are_excluded_from_path_and_subcommand_hits(tmp_path: Path) -> None:
    root = _work(tmp_path)
    (root / "tests/cli").mkdir(parents=True)
    (root / "tests/cli/more.py").write_text(
        'def register(s):\n    s.add_parser("run")\n', encoding="utf-8"
    )
    document = parse_design("# 題\n\n## 1. 序論\n\n`tests/test_cart.py` と `jigane run`。\n")
    matches = {m.mention.name: m for m in recommend(root, document).matches}
    assert isinstance(matches["tests/test_cart.py"], NotFound)
    run = matches["run"]
    assert isinstance(run, Found)
    assert {loc.path for loc in run.locations} == {"shop/cli/commands/run.py"}


def test_sources_under_docs_are_not_indexed(tmp_path: Path) -> None:
    root = _work(tmp_path)
    (root / "docs").mkdir()
    (root / "docs/scratch.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    result = recommend(root, parse_design("# 題\n\n## 1. 序論\n\n`Cart`。\n"))
    assert "docs/scratch.py" not in {c.path for c in result.candidates}


def test_more_sections_rank_first_then_direct_above_one_step(tmp_path: Path) -> None:
    root = _work(tmp_path)
    text = (
        "# 題\n\n## 1. a\n\n`Cart`\n\n## 2. b\n\n`Cart`\n\n## 3. c\n\n`Cart` `shop/unrelated.py`\n"
    )
    result = recommend(root, parse_design(text))
    order = [(c.path, c.kind, c.section_count) for c in result.candidates]
    assert order[0] == ("shop/cart.py", CandidateKind.DIRECT, 3)
    # 3 節の起点から辿った 2 件は、1 節だけが名指す直接の unrelated より上に来る
    assert order.index(("shop/app.py", CandidateKind.ONE_STEP, 3)) < order.index(
        ("shop/unrelated.py", CandidateKind.DIRECT, 1)
    )


def test_limit_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limit"):
        recommend(_work(tmp_path), parse_design(DOCUMENT), 0)


def test_dot_slash_bare_file_is_found(tmp_path: Path) -> None:
    root = _work(tmp_path)
    (root / "notes.md").write_text("メモ\n", encoding="utf-8")
    document = parse_design("# 題\n\n## 1. 序論\n\n`./notes.md` を読む。\n")
    (match,) = recommend(root, document).matches
    assert isinstance(match, Found)
    assert [(loc.path, loc.kind) for loc in match.locations] == [("notes.md", LocationKind.FILE)]


def test_location_line_follows_kind() -> None:
    Location("a.py", 3, "x", LocationKind.CLASS)
    Location("a.py", None, "x", LocationKind.FILE)
    with pytest.raises(ValueError, match="種類と合いません"):
        Location("a.py", None, "x", LocationKind.CLASS)
    with pytest.raises(ValueError, match="種類と合いません"):
        Location("a.py", 1, "x", LocationKind.DIRECTORY)


def test_untracked_nested_copies_stay_out_of_a_git_work_tree(tmp_path: Path) -> None:
    root = _work(tmp_path)
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    copies = [".claude/worktrees/x/shop/cart.py", "ignored/shop/cart.py"]
    for relative in copies:
        (root / relative).parent.mkdir(parents=True)
        (root / relative).write_text(FILES["shop/cart.py"], encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".gitignore", *FILES)
    result = recommend(root, parse_design(DOCUMENT))
    paths = {c.path for c in result.candidates}
    paths |= {loc.path for m in result.matches if isinstance(m, Found) for loc in m.locations}
    assert "shop/cart.py" in paths
    assert not any(p.startswith((".claude/", "ignored/")) for p in paths)


def test_outside_git_hidden_directories_are_skipped(tmp_path: Path) -> None:
    root = _work(tmp_path)
    nested = root / ".claude/worktrees/x/shop/cart.py"
    nested.parent.mkdir(parents=True)
    nested.write_text(FILES["shop/cart.py"], encoding="utf-8")
    result = recommend(root, parse_design(DOCUMENT))
    assert not any(c.path.startswith(".claude/") for c in result.candidates)
    assert "shop/cart.py" in {c.path for c in result.candidates}


METHOD_FILES = {
    "lib/models.py": """\
class Review:
    def to_dict(self) -> dict:
        return {}


class Other:
    def to_dict(self) -> dict:
        return {}

    def only_here(self) -> int:
        return 1
""",
    "lib/helpers.py": """\
def to_dict() -> dict:
    return {}


def unique_helper() -> int:
    return 1


def twin() -> int:
    return 1
""",
    "lib/more.py": """\
def twin() -> int:
    return 2
""",
}


def _method_matches(tmp_path: Path, document: str) -> dict[tuple[str, int], Match]:
    for relative, text in METHOD_FILES.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    result = recommend(tmp_path, parse_design(document))
    return {(m.mention.name, m.mention.line): m for m in result.matches}


def _paths(match: Match) -> list[str]:
    assert isinstance(match, (Found, Ambiguous))
    return [loc.path for loc in match.locations]


def test_qualified_method_is_limited_to_its_type(tmp_path: Path) -> None:
    matches = _method_matches(
        tmp_path, "# 題\n\n## 1. 節\n\n`Review.to_dict` と `Nothing.to_dict`。\n"
    )
    assert _paths(matches["Review.to_dict", 5]) == ["lib/models.py"]
    assert isinstance(matches["Nothing.to_dict", 5], NotFound)


def test_bare_method_is_narrowed_by_type_in_same_section(tmp_path: Path) -> None:
    matches = _method_matches(tmp_path, "# 題\n\n## 1. 節\n\n`Review` の `to_dict`。\n")
    found = matches["to_dict", 5]
    assert isinstance(found, Found)
    assert [(loc.path, loc.kind) for loc in found.locations] == [
        ("lib/models.py", LocationKind.METHOD)
    ]


def test_bare_name_with_unrelated_type_in_section_does_not_fall_to_module_function(
    tmp_path: Path,
) -> None:
    matches = _method_matches(tmp_path, "# 題\n\n## 1. 節\n\n`Other` の `unique_helper`。\n")
    assert isinstance(matches["unique_helper", 5], NotFound)


def test_bare_name_without_type_resolves_to_unique_module_function(tmp_path: Path) -> None:
    matches = _method_matches(tmp_path, "# 題\n\n## 1. 節\n\n`unique_helper()` を呼ぶ。\n")
    assert _paths(matches["unique_helper", 5]) == ["lib/helpers.py"]


def test_ambiguous_names_are_shown_and_not_recommended(tmp_path: Path) -> None:
    for relative, text in METHOD_FILES.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    document = parse_design(
        "# 題\n\n## 1. 節\n\n`twin()` を呼ぶ。\n\n## 2. 節\n\n`Review` と `Other` の `to_dict`。\n"
    )
    result = recommend(tmp_path, document)
    twin = next(m for m in result.matches if m.mention.name == "twin")
    assert isinstance(twin, Ambiguous)
    assert sorted(_paths(twin)) == ["lib/helpers.py", "lib/more.py"]
    to_dict = next(m for m in result.matches if m.mention.name == "to_dict")
    assert isinstance(to_dict, Ambiguous)
    paths = {c.path for c in result.candidates}
    assert "lib/more.py" not in paths
    assert "lib/helpers.py" not in paths


def test_bare_method_without_type_in_section_is_narrowed_by_types_of_whole_document(
    tmp_path: Path,
) -> None:
    matches = _method_matches(
        tmp_path, "# 題\n\n## 1. 節\n\n`to_dict` を呼ぶ。\n\n## 2. 節\n\n`Review` を使う。\n"
    )
    found = matches["to_dict", 5]
    assert isinstance(found, Found)
    assert [(loc.path, loc.kind) for loc in found.locations] == [
        ("lib/models.py", LocationKind.METHOD)
    ]


def test_bare_method_is_ambiguous_when_several_document_types_have_it(tmp_path: Path) -> None:
    matches = _method_matches(
        tmp_path,
        "# 題\n\n## 1. 節\n\n`to_dict` を呼ぶ。\n\n## 2. 節\n\n`Review` と `Other` を使う。\n",
    )
    assert isinstance(matches["to_dict", 5], Ambiguous)


def test_bare_name_no_document_type_has_falls_to_module_function(tmp_path: Path) -> None:
    matches = _method_matches(
        tmp_path, "# 題\n\n## 1. 節\n\n`unique_helper` を呼ぶ。\n\n## 2. 節\n\n`Review` を使う。\n"
    )
    assert _paths(matches["unique_helper", 5]) == ["lib/helpers.py"]


def test_untracked_files_are_matched_and_marked_but_ignored_and_nested_trees_are_not(
    tmp_path: Path,
) -> None:
    root = _work(tmp_path)
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".gitignore", *FILES)
    (root / "shop/fresh.py").write_text("class Fresh:\n    pass\n", encoding="utf-8")
    ignored = root / "ignored/shop/cart.py"
    ignored.parent.mkdir(parents=True)
    ignored.write_text(FILES["shop/cart.py"], encoding="utf-8")
    nested = root / "copy/shop/cart.py"
    nested.parent.mkdir(parents=True)
    nested.write_text(FILES["shop/cart.py"], encoding="utf-8")
    git(root / "copy", "init", "-q")
    document = parse_design("# 題\n\n## 1. 序論\n\n`Fresh` と `shop/cart.py`。\n")
    result = recommend(root, document)
    assert {c.path for c in result.candidates if c.untracked} == {"shop/fresh.py"}
    assert {
        loc.path
        for m in result.matches
        if isinstance(m, Found)
        for loc in m.locations
        if loc.untracked
    } == {"shop/fresh.py"}
    assert "shop/fresh.py" in {c.path for c in result.candidates}
    found = {m.mention.name: m for m in result.matches}
    assert isinstance(found["Fresh"], Found)
    assert [loc.path for loc in found["Fresh"].locations] == ["shop/fresh.py"]
    # 追跡している cart.py だけが引き当たる。ignore した写しと入れ子の作業木の写しは入らない
    assert isinstance(found["shop/cart.py"], Found)
    assert [loc.path for loc in found["shop/cart.py"].locations] == ["shop/cart.py"]


STORE_FILES = {
    "lib/paths.py": 'NOTES_FILE = "notes.json"\n',
    "lib/reader.py": """\
from lib import paths


def load(root):
    return (root / paths.NOTES_FILE).read_text()
""",
    "lib/writer.py": """\
from lib.paths import NOTES_FILE


def save(root, text):
    (root / NOTES_FILE).write_text(text)
""",
    "lib/importer.py": "from lib.paths import NOTES_FILE\n\nUNUSED = NOTES_FILE\n",
    "lib/other.py": "def other():\n    return 1\n",
}


def test_stored_file_name_traces_to_the_functions_using_its_constant(tmp_path: Path) -> None:
    for relative, text in STORE_FILES.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    result = recommend(tmp_path, parse_design("# 題\n\n## 1. 序論\n\n`notes.json` を保存する。\n"))
    by_path = {c.path: c for c in result.candidates}
    assert by_path["lib/paths.py"].kind is CandidateKind.DIRECT
    for accessor, trace in (("lib/reader.py", Trace.READER), ("lib/writer.py", Trace.WRITER)):
        assert by_path[accessor].kind is CandidateKind.ONE_STEP
        assert [r.trace for r in by_path[accessor].reasons] == [trace]
    # 定数を関数の外でしか参照しないファイルと、無関係なファイルは入らない
    assert "lib/importer.py" not in by_path
    assert "lib/other.py" not in by_path


def test_untracked_files_under_hidden_directories_are_left_out(tmp_path: Path) -> None:
    root = _work(tmp_path)
    git(root, "init", "-q")
    git(root, "add", *FILES)
    hidden = root / ".claude/worktrees/w/shop/ghost.py"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("class Ghost:\n    pass\n", encoding="utf-8")
    (root / "shop/fresh.py").write_text("class Fresh:\n    pass\n", encoding="utf-8")
    result = recommend(root, parse_design("# 題\n\n## 1. 序論\n\n`Ghost` と `Fresh`。\n"))
    paths = {c.path for c in result.candidates}
    assert "shop/fresh.py" in paths
    assert not any(".claude" in p for p in paths)
    found = {m.mention.name: m for m in result.matches}
    assert isinstance(found["Ghost"], NotFound)


def test_stored_file_constant_is_found_on_any_line_and_only_through_imports(
    tmp_path: Path,
) -> None:
    files = {
        # 保存物の名前を持つ最初の行は、定数の宣言ではない
        "lib/paths.py": '# "notes.json" を置く\nNOTES_FILE = "notes.json"\n',
        "lib/real.py": (
            "from lib.paths import NOTES_FILE\n\n\ndef load():\n    return NOTES_FILE\n"
        ),
        "lib/aliased.py": "from lib import paths as p\n\n\ndef load():\n    return p.NOTES_FILE\n",
        "lib/unrelated.py": "NOTES_FILE = 1\n\n\ndef load():\n    return NOTES_FILE\n",
        "lib/attr.py": "def load(config):\n    return config.NOTES_FILE\n",
    }
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    result = recommend(tmp_path, parse_design("# 題\n\n## 1. 序論\n\n`notes.json` を保存する。\n"))
    paths = {c.path for c in result.candidates}
    assert {"lib/paths.py", "lib/real.py", "lib/aliased.py"} <= paths
    assert "lib/unrelated.py" not in paths
    assert "lib/attr.py" not in paths


def test_callers_of_the_accessor_function_are_a_second_step_ranked_below_the_first(
    tmp_path: Path,
) -> None:
    files = {
        "lib/paths.py": 'NOTES_FILE = "notes.json"\n',
        "lib/reader.py": (
            "from lib import paths\n\n\n"
            "@staticmethod\ndef load(root):\n    return (root / paths.NOTES_FILE).read_text()\n"
        ),
        "lib/app.py": "from lib.reader import load\n\n\ndef main():\n    return load('.')\n",
        "lib/zeta.py": (
            "from lib.paths import NOTES_FILE\n\n\ndef zeta():\n    return NOTES_FILE\n"
        ),
    }
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    result = recommend(tmp_path, parse_design("# 題\n\n## 1. 序論\n\n`notes.json` を保存する。\n"))
    order = [c.path for c in result.candidates]
    by_path = {c.path: c for c in result.candidates}
    assert by_path["lib/app.py"].kind is CandidateKind.TWO_STEP
    assert [
        (r.trace, r.function)
        for r in by_path["lib/app.py"].reasons
        if isinstance(r, AccessorCallerReason)
    ] == [(Trace.ACCESSOR_CALLER, "load")]
    assert len(by_path["lib/app.py"].reasons) == 1
    # 同じ節の数なら、二段目は直接と一段目のどちらよりも下にある（パスの順では app が先頭に来る）
    assert order == ["lib/paths.py", "lib/reader.py", "lib/zeta.py", "lib/app.py"]


def test_functions_with_a_write_call_are_writers_and_the_rest_readers(tmp_path: Path) -> None:
    bodies = {
        "reads": "return (root / NOTES_FILE).read_text()",
        "replaces": "(root / NOTES_FILE).with_suffix('.tmp').replace(root / NOTES_FILE)",
        "opens": "return open(root / NOTES_FILE, 'a')",
        "opens_to_read": "return open(root / NOTES_FILE, 'r')",
        "dumps": "json.dump({}, (root / NOTES_FILE).open('w'))",
        "atomic": "write_text_atomic(root / NOTES_FILE, text)",
        "str_replace": "return str(NOTES_FILE).replace('a', 'b')",
        "moves": "shutil.move('x', root / NOTES_FILE)",
        "yaml_dumps": "yaml.dump({}, (root / NOTES_FILE).open('w'))",
        "bare_dumps": "dump({}, root / NOTES_FILE)",
        "updates": "return open(root / NOTES_FILE, 'r+')",
    }
    files = {"lib/paths.py": 'NOTES_FILE = "notes.json"\n'}
    for name, body in bodies.items():
        header = "import json\nfrom lib.paths import NOTES_FILE\n\n\ndef f(root, text=''):\n"
        files[f"lib/{name}.py"] = f"{header}    {body}\n"
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    result = recommend(
        tmp_path, parse_design("# 題\n\n## 1. 序論\n\n`notes.json` を保存する。\n"), limit=50
    )
    traces = {c.path: {r.trace for r in c.reasons} for c in result.candidates}
    writers = {
        "replaces",
        "opens",
        "dumps",
        "atomic",
        "moves",
        "yaml_dumps",
        "bare_dumps",
        "updates",
    }
    for name in bodies:
        expected = Trace.WRITER if name in writers else Trace.READER
        assert traces[f"lib/{name}.py"] == {expected}, name


def test_prose_stored_file_names_are_taken_only_when_a_constant_defines_them(
    tmp_path: Path,
) -> None:
    target = tmp_path / "lib/paths.py"
    target.parent.mkdir(parents=True)
    target.write_text('REVIEW_FILE = "review.json"\n', encoding="utf-8")
    document = parse_design(
        "# 題\n\n## 1. 序論\n\nレビューは review.json に保存し、foo.json は使わない。\n"
    )
    result = recommend(tmp_path, document)
    assert {m.mention.name for m in result.matches} == {"review.json"}
    assert "lib/paths.py" in {c.path for c in result.candidates}


def test_prose_and_table_paths_are_matched_only_when_they_exist(tmp_path: Path) -> None:
    root = _work(tmp_path)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    document = parse_design(
        "# 題\n\n## 1. 序論\n\n"
        "地の文の shop/pricing.py と（pyproject.toml）と ghost/none.py を読む。\n\n"
        "| 項目 | 場所 |\n| --- | --- |\n| 表 | shop/app.py |\n\n"
        "`missing/none.py` は無い。\n"
    )
    matches = {m.mention.name: m for m in recommend(root, document).matches}
    assert isinstance(matches["shop/pricing.py"], Found)
    assert isinstance(matches["pyproject.toml"], Found)
    assert isinstance(matches["shop/app.py"], Found)
    assert "ghost/none.py" not in matches
    assert isinstance(matches["missing/none.py"], NotFound)


def test_readme_lists_the_same_write_rules_as_the_implementation() -> None:
    readme = Path(__file__).parent.parent / "jigane/design/README.md"
    text = readme.read_text(encoding="utf-8")
    # 書く側と判定する呼び出しの一覧。README の列挙はこれと同じものを同じ順で書く
    write_rules = (
        f"`{_WRITE_PREFIX}` で始まる名前",
        f"`{_WRITE_INFIX}` を含む名前",
        *(f"`{name}`" for name in sorted(_WRITE_CALLS)),
        *(f"`{name}`" for name in sorted(_REPLACE_CALLS)),
        "引数一つの `replace`",
        "書き込みの mode（w、a、x、更新の +）の `open`",
    )
    positions = [text.index(rule) for rule in write_rules]
    assert positions == sorted(positions)


def test_candidate_kind_follows_the_smallest_step_of_its_reasons() -> None:
    mention = Mention("T", MentionKind.TYPE, None, 1)
    two = AccessorCallerReason(mention, "load")
    one = ReaderReason(mention, "load")
    assert Candidate("a.py", (two,)).kind is CandidateKind.TWO_STEP
    assert Candidate("a.py", (two, one)).kind is CandidateKind.ONE_STEP
    assert Candidate("a.py", (NamedReason(mention), two)).kind is CandidateKind.DIRECT


def test_stored_file_trace_skips_sources_ast_cannot_parse(tmp_path: Path) -> None:
    files = {**STORE_FILES, "lib/deep.py": "x = " + "+".join(["1"] * 100000) + "\n"}
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    result = recommend(tmp_path, parse_design("# 題\n\n## 1. 序論\n\n`notes.json` を保存する。\n"))
    assert "lib/paths.py" in {c.path for c in result.candidates}
