import json
from pathlib import Path
from typing import Any

from review_fakes import make_review
from test_policy_classes import feature_review, whole

from jigane.figures.models import FigureKind, MermaidFigure
from jigane.policy_materials import discriminators, er
from jigane.preconditions.required import required
from jigane.reviews.lifecycle import Review
from jigane.skills import macro, policy_figures
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import DiagramsResult, diagrams_of
from jigane.storage.store import ReviewKind

SOURCE = """\
from dataclasses import dataclass
from enum import Enum


class Kind(Enum):
    PR = 1
    RANGE = 2
    NONE = 3


@dataclass
class DiffSource:
    base: str
    range_text: str | None = None
    pr_number: int | None = None


def decide(source: DiffSource) -> Kind:
    if source.pr_number is None and source.base:
        print("secret-body-statement")
        return Kind.RANGE
    elif source.pr_number is not None:
        return Kind.PR
    return Kind.NONE


def show(source: DiffSource) -> str:
    return source.range_text or ""


def count(source: DiffSource) -> int:
    return len(source.range_text or "")


def other(source: DiffSource) -> str:
    return source.base
"""


def test_er_marks_optional_fields(tmp_path: Path) -> None:
    text = required(er.er_diagram(feature_review(tmp_path, "m.py", SOURCE))).text()
    assert "erDiagram" in text
    assert 'str_None range_text "optional"' in text
    assert "str base" in text


def test_discriminator_has_negation_branch_and_value_only(tmp_path: Path) -> None:
    text = required(
        discriminators.discriminator_diagram(feature_review(tmp_path, "m.py", SOURCE))
    ).text()
    assert "source.pr_number is None and source.base" in text
    assert "source.pr_number is not None" in text
    assert "return Kind.RANGE" in text
    assert "return Kind.NONE" in text
    assert "secret-body-statement" not in text


def test_readers_list_two_functions(tmp_path: Path) -> None:
    text = required(discriminators.reader_diagram(feature_review(tmp_path, "m.py", SOURCE))).text()
    assert "DiffSource.range_text (読む側 2)" in text
    assert "show" in text
    assert "count" in text
    assert "other" not in text


def test_prompt_includes_three_sections(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    review = make_review(
        tmp_path,
        ["m.py"],
        judgment_map={
            "segments": [whole("m.py", len(SOURCE.splitlines()))],
            "regions": [],
            "line_counts": {},
        },
        kind=ReviewKind.FEATURE,
    )
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert "### ER 図" in prompt
    assert "### 判別の流れ図" in prompt
    assert "### 読む側の対応" in prompt
    assert prompt.index("### クラス図") < prompt.index("### ER 図")
    assert prompt.index("### ER 図") < prompt.index("### 判別の流れ図")
    assert prompt.index("### 判別の流れ図") < prompt.index("### 読む側の対応")
    assert "secret-body-statement" not in prompt


TWO_TYPES = """\
from dataclasses import dataclass


@dataclass
class Alpha:
    name: str | None = None


@dataclass
class Beta:
    name: str | None = None


def read_alpha(a: Alpha) -> str:
    return a.name or ""


def read_beta(b: Beta) -> str:
    return b.name or ""


def read_unknown(x) -> str:
    return x.name
"""


def test_readers_count_only_the_receiver_type(tmp_path: Path) -> None:
    text = required(
        discriminators.reader_diagram(feature_review(tmp_path, "m.py", TWO_TYPES))
    ).text()
    assert "Alpha.name (読む側 1、受け手の型を確かめられない参照 1 件)" in text
    assert "Beta.name (読む側 1、受け手の型を確かめられない参照 1 件)" in text
    alpha = text.split("Alpha.name")[1].split("Beta.name")[0]
    assert "read_alpha" in alpha
    assert "read_beta" not in alpha
    assert "read_unknown" not in text


def _figure_of(result: DiagramsResult, key: str) -> MermaidFigure:
    figure = next(placed.figure for placed in result.figures if placed.key == key)
    assert isinstance(figure, MermaidFigure)
    return figure


def test_diagrams_output_carries_the_policy_figures_in_registry_order(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "m.py", SOURCE)
    result = diagrams_of(review)
    keys = [placed.key for placed in result.figures]
    # 登録簿の順に並び、描くものが無い図（この資料では signatures）は入らない
    assert keys == ["tree", "sequence", "classes", "er", "discriminators", "readers"]
    er = _figure_of(result, "er")
    assert er.kind == FigureKind.MERMAID
    assert 'str_None range_text "optional"' in er.panels[0].mermaid
    discriminators = _figure_of(result, "discriminators")
    assert "source.pr_number is not None" in discriminators.panels[0].mermaid
    assert discriminators.panels[0].title.startswith("m.decide m.py:")
    assert "range_text" in _figure_of(result, "readers").panels[0].mermaid
    # 資料の本文と同じ関数の出力で、写しではない
    sections = macro.policy_sections(review)
    material = macro.diagram_materials_text(AssemblyInput.load(review), sections)
    for placed in result.figures:
        figure = placed.figure
        if not isinstance(figure, MermaidFigure):
            continue
        for panel in figure.panels:
            assert panel.mermaid in material


def test_diagrams_output_leaves_empty_figures_out(tmp_path: Path) -> None:
    keys = [p.key for p in diagrams_of(feature_review(tmp_path, "m.py", "import os\n")).figures]
    # ツリー図とシーケンス図は注記だけでも出る。方針の図は描くものが無ければ入らない
    assert keys == ["tree", "sequence"]


def test_diagrams_json_names_each_figure_by_key_title_and_kind(tmp_path: Path) -> None:
    result = diagrams_of(feature_review(tmp_path, "m.py", SOURCE))
    figures = result.to_json()["figures"]
    # 並行処理の図は Python のソースでは描くものが無い
    expected = [e.key for e in policy_figures.REGISTRY if e.key != "concurrency"][:6]
    assert [f["key"] for f in figures] == expected
    assert [f["kind"] for f in figures] == ["tree", "sequence", *["mermaid"] * 4]
    assert all(f["title"] for f in figures)
    assert figures[0]["after"]["mermaid"]
    assert all(f["panels"][0]["mermaid"] for f in figures if f["kind"] == "mermaid")
    json.dumps(figures)


JAVA_SOURCE = """\
enum Kind { PR, RANGE, NONE }

class Rules {
    Kind decide(Object o, int x) {
        if (x > 1) {
            return Kind.RANGE;
        } else if (o instanceof String s) {
            return Kind.PR;
        }
        return switch (x) { case 1 -> Kind.NONE; default -> Kind.PR; };
    }
}
"""


def test_java_discriminator_is_drawn(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "Rules.java", JAVA_SOURCE)
    text = required(discriminators.discriminator_diagram(review)).text()
    assert "x ~ 1" in text
    assert "o instanceof String s" in text
    assert "switch x" in text
    assert "return Kind.RANGE" in text
    assert "case 1" in text


RANGED_SOURCE = """\
from enum import Enum


class Kind(Enum):
    A = 1
    B = 2


class Holder:
    limit = 1

    def untouched(self, x: int) -> Kind:
        if x > self.limit:
            return Kind.A
        return Kind.B

    def changed(self, x: int) -> Kind:
        if x > 2:
            return Kind.A
        return Kind.B

    tail = 2
"""


def ranged_review(root: Path, segments: list[dict[str, Any]]) -> Review:
    (root / "m.py").write_text(RANGED_SOURCE, encoding="utf-8")
    return make_review(
        root,
        ["m.py"],
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )


def named_segment(name: str, first: int, last: int) -> dict[str, Any]:
    return {
        **whole("m.py", last),
        "new_span": {"start_line": first, "end_line": last},
        "old_span": {"start_line": first, "end_line": last},
        "qualified_name": name,
        "declaration_kind": "class" if name == "Holder" else "method",
    }


def test_function_between_scattered_changes_of_its_class_is_not_drawn(tmp_path: Path) -> None:
    # Holder の変更行は 10 行目と 22 行目で、範囲は両端の間の二つのメソッドを覆う。
    # 変更行を含む関数は、changed の 18 行目だけ
    review = ranged_review(
        tmp_path,
        [named_segment("Holder", 10, 22), named_segment("Holder.changed", 18, 18)],
    )
    text = required(discriminators.discriminator_diagram(review)).text()
    assert "%% m.Holder.changed m.py:17" in text
    assert "m.Holder.untouched" not in text


def test_function_changed_below_its_first_line_is_drawn(tmp_path: Path) -> None:
    review = ranged_review(tmp_path, [named_segment("Holder.untouched", 13, 13)])
    text = required(discriminators.discriminator_diagram(review)).text()
    assert "%% m.Holder.untouched m.py:12" in text
    assert "m.Holder.changed" not in text


def test_er_keeps_the_sequence_marks_of_attribute_types() -> None:
    attribute_type = er._attribute_type
    assert attribute_type("Object[]") == "Object_array"
    assert attribute_type("char[]") == "char_array"
    assert attribute_type("int[][]") == "int_array_array"
    assert attribute_type("int[,]") == "int_array"
    assert attribute_type("[Int]") == "array_Int"
    assert attribute_type("[[Int]]") == "array_array_Int"
    assert attribute_type("[String: [Int]]") == "dict_String_array_Int"
    assert attribute_type("[[String: Int]]") == "array_dict_String_Int"
    assert attribute_type("[Int]?") == "array_Int"
    assert attribute_type("[Int?]") == "array_Int"
    assert attribute_type("[String: Int]?") == "dict_String_Int"
    assert attribute_type("[String: Int]") == "dict_String_Int"
    assert attribute_type("list[int]") == "list_int"
    assert attribute_type("str | None") == "str_None"
    assert attribute_type(None) == "unknown"


def _er_text(tmp_path: Path, name: str, source: str) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return required(er.er_diagram(feature_review(tmp_path, name, source))).text()


def test_er_keeps_array_marks_for_each_language(tmp_path: Path) -> None:
    java = "class Box {\n    Object[] stack;\n    String[] names;\n    char[] buffer;\n}\n"
    text = _er_text(tmp_path / "java", "Box.java", java)
    assert "Object_array stack" in text
    assert "String_array names" in text
    assert "char_array buffer" in text
    csharp = "class Box {\n    public int[] Counts;\n    public string[] Names { get; set; }\n}\n"
    text = _er_text(tmp_path / "csharp", "Box.cs", csharp)
    assert "int_array Counts" in text
    assert "string_array Names" in text
    swift = "struct Box {\n    var counts: [Int]\n    var index: [String: Int]\n}\n"
    text = _er_text(tmp_path / "swift", "Box.swift", swift)
    assert "array_Int counts" in text
    assert "dict_String_Int index" in text
    python = "class Box:\n    counts: list[int]\n    names: list[str]\n"
    text = _er_text(tmp_path / "python", "box.py", python)
    assert "list_int counts" in text
    assert "list_str names" in text
