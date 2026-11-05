from collections.abc import Collection, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from review_fakes import make_review
from test_policy_classes import feature_review, whole
from tree_sitter import Node

from jigane.figures.models import FigureKind, TableFigure
from jigane.languages import registry
from jigane.languages.base import Declaration, PublicName, PublicNameKind, PublicNameReader
from jigane.languages.java import JavaAdapter
from jigane.languages.python_public_names import added_public_names
from jigane.reviews.lifecycle import Review
from jigane.skills import macro, policy_callers
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import (
    REGISTRY,
    FigureSlot,
    diagrams_of,
    section_texts,
    unsupported_figures,
)
from jigane.storage.store import ReviewKind

FIXTURES = Path(__file__).parent / "fixtures" / "policy_callers"
# 例題の id から、図の同じ一行に載る語の組。名前と、呼ぶ側の欄の語（なし）
PRESENCE = {
    "p2-a04": [["Table.render.max_width", "なし"], ["Table.render.align", "なし"]],
    "p2-a07": [["truncate_slug", "なし"]],
    "p2-a08": [["fetch_user.strict", "なし"], ["fetch_user.normalize", "なし"]],
    "p2-a09": [["Level.TRACE", "なし"], ["Level.FATAL", "なし"]],
}


def _names(before: dict[str, str], after: dict[str, str]) -> dict[str, PublicName]:
    found = added_public_names(before, after, set(after))
    return {name.name: name for name in found}


def _callers(before: str, after: str) -> dict[str, tuple[str, ...]]:
    names = _names({"m.py": before}, {"m.py": after})
    return {name: found.callers for name, found in names.items()}


def test_added_function_lists_distinct_calling_functions() -> None:
    after = (
        "def helper(x):\n    return x\n\n\n"
        "def a():\n    helper(1)\n    helper(2)\n\n\n"
        "def b():\n    helper(3)\n\n\n"
        "helper(4)\n"
    )
    names = _names({"m.py": ""}, {"m.py": after})
    assert names["helper"].kind is PublicNameKind.FUNCTION
    assert names["helper"].callers == ("m.py の a", "m.py の b", "m.py の モジュール")


def test_added_argument_counts_only_calls_that_pass_it() -> None:
    before = "def f(a):\n    pass\n\n\ndef g():\n    f(1)\n"
    after = (
        "def f(a, b=None, *, c=None):\n    pass\n\n\n"
        "def g():\n    f(1)\n\n\n"
        "def h():\n    f(1, 2)\n\n\n"
        "def i():\n    f(1, c=3)\n\n\n"
        "def j(args):\n    f(*args)\n"
    )
    callers = _callers(before, after)
    assert callers["f.b"] == ("m.py の h",)
    assert callers["f.c"] == ("m.py の i",)
    assert "f" not in callers


def test_added_argument_of_a_method_skips_self() -> None:
    before = "class T:\n    def render(self, sep):\n        pass\n"
    after = (
        "class T:\n    def render(self, sep, width=None):\n        pass\n\n\n"
        "def use(t):\n    t.render(',', 5)\n"
    )
    assert _callers(before, after)["T.render.width"] == ("m.py の use",)


def test_added_init_argument_counts_calls_of_the_class() -> None:
    before = "class T:\n    def __init__(self, rows):\n        pass\n"
    after = (
        "class T:\n    def __init__(self, rows, title=None):\n        pass\n\n\n"
        "def a():\n    T([], 'x')\n\n\n"
        "def b():\n    T([])\n"
    )
    assert _callers(before, after)["T.__init__.title"] == ("m.py の a",)


def test_enum_value_counts_type_dot_value_references() -> None:
    before = "import enum\n\n\nclass Level(enum.Enum):\n    INFO = 1\n"
    after = (
        "import enum\n\n\nclass Level(enum.Enum):\n    INFO = 1\n"
        "    TRACE = 0\n    FATAL = 4\n\n\n"
        "def f():\n    return Level.TRACE\n"
    )
    names = _names({"m.py": before}, {"m.py": after})
    assert names["Level.TRACE"].kind is PublicNameKind.ENUM_VALUE
    assert names["Level.TRACE"].callers == ("m.py の f",)
    assert names["Level.FATAL"].callers == ()


def test_class_counts_calls_and_subclasses() -> None:
    after = (
        "class Base:\n    pass\n\n\n"
        "class Child(Base):\n    pass\n\n\n"
        "def make():\n    return Base()\n"
    )
    names = _names({"m.py": ""}, {"m.py": after})
    assert names["Base"].kind is PublicNameKind.CLASS
    assert names["Base"].callers == ("m.py の make", "m.py の Child")


def test_class_counts_annotations_and_references_by_name() -> None:
    after = (
        "class Draft:\n    pass\n\n\n"
        "class Maker:\n    SHARED = 1\n\n\n"
        "class Checked:\n    pass\n\n\n"
        "class Unused:\n    pass\n\n\n"
        "def annotate(draft: Draft) -> None:\n    pass\n\n\n"
        "def read():\n    return Maker.SHARED\n\n\n"
        "def check(value):\n    return isinstance(value, Checked)\n"
    )
    names = _names({"m.py": ""}, {"m.py": after})
    assert names["Draft"].callers == ("m.py の annotate",)
    assert names["Maker"].callers == ("m.py の read",)
    assert names["Checked"].callers == ("m.py の check",)
    assert names["Unused"].callers == ()


def test_class_counts_a_reference_through_a_module() -> None:
    after = {"m.py": "class Draft:\n    pass\n", "n.py": "def f(value: m.Draft):\n    pass\n"}
    assert _names({"m.py": ""}, after)["Draft"].callers == ("n.py の f",)


def test_class_named_only_inside_itself_has_no_caller() -> None:
    after = "class Node:\n    def copy(self) -> Node:\n        return Node()\n"
    assert _names({"m.py": ""}, {"m.py": after})["Node"].callers == ()


def test_class_counts_uses_of_another_definition_with_the_same_name() -> None:
    after = {
        "m.py": "class Badge:\n    pass\n",
        "n.py": "class Badge:\n    pass\n\n\ndef tile(badge: Badge):\n    pass\n",
    }
    names = _names({"m.py": "", "n.py": ""}, after)
    assert names["Badge"].callers == ("n.py の tile",)


def test_string_annotation_and_nested_class_are_not_counted() -> None:
    after = (
        "class Outer:\n    class Nested:\n        pass\n\n\n"
        "class Draft:\n    pass\n\n\n"
        "def f(draft: 'Draft'):\n    return Outer.Nested\n"
    )
    names = _names({"m.py": ""}, {"m.py": after})
    assert "Outer.Nested" not in names
    assert names["Draft"].callers == ()


def test_intro_does_not_claim_that_zero_callers_means_unused() -> None:
    assert "使う箇所が無い" not in policy_callers.INTRO
    assert "名前だけを照合" in policy_callers.INTRO
    assert "`.a`" in policy_callers.INTRO
    assert "入れ子のクラス" in policy_callers.INTRO


def test_recursion_is_not_a_caller() -> None:
    after = "def walk(n):\n    if n:\n        walk(n - 1)\n"
    assert _names({"m.py": ""}, {"m.py": after})["walk"].callers == ()


def test_recursive_method_is_not_a_caller() -> None:
    after = "class T:\n    def walk(self, n):\n        return self.walk(n - 1)\n"
    assert _names({"m.py": ""}, {"m.py": after})["T.walk"].callers == ()


def test_names_starting_with_underscore_are_left_out() -> None:
    before = "def f(a):\n    pass\n"
    after = (
        "def _private():\n    pass\n\n\n"
        "def f(a, _hidden=None):\n    pass\n\n\n"
        "class _K:\n    def run(self):\n        pass\n\n\n"
        "class K:\n    def _inner(self):\n        pass\n"
    )
    assert set(_names({"m.py": before}, {"m.py": after})) == {"K"}


def test_unchanged_names_are_not_listed() -> None:
    source = "def f(a):\n    pass\n"
    assert _names({"m.py": source}, {"m.py": source}) == {}


def test_names_come_from_changed_files_and_callers_from_the_whole_tree() -> None:
    found = added_public_names(
        {"a.py": ""},
        {
            "a.py": "def helper():\n    pass\n",
            "b.py": "from a import helper\n\n\ndef go():\n    helper()\n",
        },
        {"a.py"},
    )
    assert [(n.name, n.callers) for n in found] == [("helper", ("b.py の go",))]


def test_file_that_does_not_parse_is_skipped() -> None:
    assert _names({"m.py": ""}, {"m.py": "def broken(:\n"}) == {}


def _case(case: str, work: Path) -> Review:
    """例題の木を、`.py.txt` を `.py` に直して作業場所へ写し、レビューにする。"""
    for source in (FIXTURES / case).rglob("*.txt"):
        target = work / source.relative_to(FIXTURES / case).with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return _review(work)


def _review(directory: Path) -> Review:
    before, after = directory / "before", directory / "after"
    paths = sorted(
        p.relative_to(after).as_posix()
        for p in after.rglob("*.py")
        if not (before / p.relative_to(after)).exists()
        or (before / p.relative_to(after)).read_text() != p.read_text()
    )
    segments: list[dict[str, Any]] = []
    for path in paths:
        segment = whole(path, len((after / path).read_text().splitlines()))
        if not (before / path).exists():
            segment["old_path"] = None
            segment["old_span"] = None
            segment["old_line_count"] = 0
        segments.append(segment)
    return make_review(
        after,
        paths,
        base_tree=before,
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )


@pytest.mark.parametrize("case", sorted(PRESENCE))
def test_example_cases_put_each_name_and_its_callers_on_one_line(
    case: str, tmp_path: Path
) -> None:
    review = _case(case, tmp_path)
    sections = section_texts(review, FigureSlot.AFTER_REGIONS)
    section = next(s for s in sections if s.startswith(f"### {policy_callers.SECTION_CALLERS}"))
    for group in PRESENCE[case]:
        assert any(all(word in line for word in group) for line in section.splitlines()), group


def test_table_of_the_enum_example_has_the_columns_and_the_intro(tmp_path: Path) -> None:
    figure = policy_callers.caller_table(_case("p2-a09", tmp_path))
    assert isinstance(figure, TableFigure)
    assert figure.kind == FigureKind.TABLE
    assert figure.header == ("名前", "種類", "定義の場所", "呼ぶ側の数", "呼ぶ側")
    rows = {row.cells[0]: row.cells for row in figure.rows}
    assert rows["`Level.TRACE`"] == ("`Level.TRACE`", "列挙の値", "logs.py:5", "0", "なし")
    assert figure.text().startswith(policy_callers.INTRO)


def test_no_figure_without_added_public_names(tmp_path: Path) -> None:
    (tmp_path / "before").mkdir()
    (tmp_path / "after").mkdir()
    (tmp_path / "before" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "after" / "m.py").write_text("x = 2\n", encoding="utf-8")
    assert policy_callers.caller_table(_review(tmp_path)) is None


def test_file_review_has_no_figure(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "m.py", "def f():\n    pass\n")
    assert policy_callers.caller_table(review) is None


def _mixed_review(tmp_path: Path, extra: str, extra_text: str, python_text: str) -> Review:
    (tmp_path / "before").mkdir()
    (tmp_path / "after").mkdir()
    (tmp_path / "before" / "m.py").write_text("", encoding="utf-8")
    (tmp_path / "after" / "m.py").write_text(python_text, encoding="utf-8")
    (tmp_path / "before" / extra).write_text("", encoding="utf-8")
    (tmp_path / "after" / extra).write_text(extra_text, encoding="utf-8")
    segments = [whole("m.py", 2), whole(extra, 2)]
    return make_review(
        tmp_path / "after",
        ["m.py", extra],
        base_tree=tmp_path / "before",
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )


def test_change_without_a_reader_has_no_section_and_names_the_language_as_unsupported(
    tmp_path: Path,
) -> None:
    review = _mixed_review(tmp_path, "A.java", "class A { void run() {} }\n", "")
    (tmp_path / "after" / "m.py").unlink()
    assert policy_callers.caller_table(review) is None
    assert any(e.key == "callers" for e in unsupported_figures(review))


def test_mixed_languages_list_python_rows_and_name_the_language_without_a_reader(
    tmp_path: Path,
) -> None:
    review = _mixed_review(
        tmp_path, "A.java", "class A { void run() {} }\n", "def helper():\n    pass\n"
    )
    figure = policy_callers.caller_table(review)
    assert isinstance(figure, TableFigure)
    assert [row.cells[0] for row in figure.rows] == ["`helper`"]
    section = next(
        s
        for s in section_texts(review, FigureSlot.AFTER_REGIONS)
        if policy_callers.SECTION_CALLERS in s
    )
    assert "対応外の言語: Java" in section
    assert any(e.key == "callers" for e in unsupported_figures(review))


class _FakeReaderAdapter:
    name = "fake-reader"
    grammar = "java"
    suffixes = frozenset({".fkr"})
    comment_node_types = JavaAdapter.comment_node_types
    import_node_types = JavaAdapter.import_node_types
    test_directory_names = JavaAdapter.test_directory_names
    test_file_patterns = JavaAdapter.test_file_patterns
    build_file_patterns = JavaAdapter.build_file_patterns

    def declarations(self, root_node: Node, source: bytes) -> list[Declaration]:
        return JavaAdapter().declarations(root_node, source)

    def added_public_names(
        self,
        before: Mapping[str, str],
        after: Mapping[str, str],
        changed: Collection[str],
        /,
    ) -> list[PublicName]:
        return [
            PublicName(f"fake_{path}", PublicNameKind.FUNCTION, path, 1, ())
            for path in changed
            if after[path] != before.get(path)
        ]


def test_each_reader_adds_its_own_rows_to_a_mixed_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java = next(p for p in registry.builtin_plugins() if p.adapter.name == "java")
    fake = replace(java, adapter=_FakeReaderAdapter(), label="fake-reader")
    monkeypatch.setattr(registry, "_plugins", (*registry.builtin_plugins(), fake))
    review = _mixed_review(tmp_path, "a.fkr", "x\n", "def helper():\n    pass\n")
    figure = policy_callers.caller_table(review)
    assert isinstance(figure, TableFigure)
    assert [row.cells[0] for row in figure.rows] == ["`helper`", "`fake_a.fkr`"]


def test_registered_after_regions_and_reaches_prompt_and_diagrams(tmp_path: Path) -> None:
    entry = next(e for e in REGISTRY if e.key == "callers")
    assert entry.slot is FigureSlot.AFTER_REGIONS
    assert entry.interface is PublicNameReader
    review = _case("p2-a07", tmp_path)
    placed = next(p for p in diagrams_of(review).figures if p.key == "callers")
    assert placed.to_json()["kind"] == "table"
    text = macro.diagram_materials_text(AssemblyInput.load(review), macro.policy_sections(review))
    assert "truncate_slug" in text


@pytest.mark.parametrize("terms", [600, 20_000])
def test_added_public_names_skip_a_file_with_a_deep_expression(terms: int) -> None:
    deep = "def deep():\n    return " + "+".join(["a"] * terms) + "\n"
    after = {"deep.py": deep, "ok.py": "def helper(x):\n    return x\n"}
    found = added_public_names({}, after, set(after))
    assert [name.name for name in found] == ["helper"]
