from pathlib import Path

import pytest
from test_policy_callers import _case as callers_case
from test_policy_field_copies import _range_review, _tree

from jigane.groups.viewpoints import (
    KISS,
    SEPARATION_OF_CONCERNS,
    SINGLE_SOURCE,
    YAGNI,
    design_principles,
)
from jigane.policy_materials.changed_files import PrebuiltSection
from jigane.skills import adversarial, policy_figures
from jigane.skills.assembly import AssemblyInput
from jigane.skills.macro import policy_sections
from jigane.skills.policy_figures import PROMPT_OMITTED, REGISTRY
from jigane.skills.policy_principles import (
    FIGURE_KEYS_BY_PRINCIPLE,
    TREE_KEY,
    figure_keys_of,
    principle_prompt,
    principle_prompts,
    principle_text,
)

P1_CASE = Path(__file__).parent / "fixtures" / "policy_material" / "principle_prompts" / "p1-a06"
TITLES = {figure.key: figure.title for figure in REGISTRY}


def _headings(prompt: str) -> list[str]:
    return [line[4:] for line in prompt.splitlines() if line.startswith("### ")]


def test_assigned_figure_keys() -> None:
    assert FIGURE_KEYS_BY_PRINCIPLE == {
        SINGLE_SOURCE: ("value_sets", "cochange", "classes", "field_copies"),
        YAGNI: ("sequence", "classes", "readers", "callers"),
        KISS: ("value_sets", "classes", "discriminators"),
        SEPARATION_OF_CONCERNS: ("sequence", "classes", "readers"),
    }


def test_every_principle_has_an_entry() -> None:
    assert set(FIGURE_KEYS_BY_PRINCIPLE) == set(design_principles())


def test_figure_keys_exist_in_registry_and_are_not_omitted() -> None:
    known = {figure.key for figure in REGISTRY} - PROMPT_OMITTED
    for keys in FIGURE_KEYS_BY_PRINCIPLE.values():
        assert set(keys) <= known


def test_tree_figure_is_given_to_every_principle() -> None:
    for principle in design_principles():
        assert TREE_KEY in figure_keys_of(principle)


def test_unknown_figure_key_is_rejected(tmp_path: Path) -> None:
    review = _range_review(tmp_path, _tree(P1_CASE / "before"), _tree(P1_CASE / "after"))
    sections = policy_sections(review)
    with pytest.raises(ValueError, match="no_such_figure"):
        policy_figures.prompt_figure_texts(review, sections.figures, ("tree", "no_such_figure"))


@pytest.mark.usefixtures("all_principle_shapes")
def test_each_prompt_holds_only_its_principle_and_its_figures(tmp_path: Path) -> None:
    review = _range_review(tmp_path, _tree(P1_CASE / "before"), _tree(P1_CASE / "after"))
    prompts = principle_prompts(AssemblyInput.load(review))
    assert [item.principle.number for item in prompts] == ["P1", "P2", "P3", "P4"]
    for item in prompts:
        lines = item.prompt.splitlines()
        for principle in design_principles():
            assert (principle_text(principle) in lines) == (principle == item.principle)
        # 割り当てに無い図の見出しは資料に入らない
        unassigned = {TITLES[key] for key in TITLES if key not in item.figure_keys}
        unassigned.discard(TITLES[TREE_KEY])
        assert not unassigned & set(_headings(item.prompt)), item.principle.number
        assert PrebuiltSection.TREE.value in _headings(item.prompt)


def test_unassigned_section_is_absent_but_assigned_one_is_present(tmp_path: Path) -> None:
    review = _range_review(tmp_path, _tree(P1_CASE / "before"), _tree(P1_CASE / "after"))
    by_number = {
        item.principle.number: item.prompt
        for item in principle_prompts(AssemblyInput.load(review))
    }
    assert TITLES["classes"] in _headings(by_number["P1"])
    assert TITLES["sequence"] not in _headings(by_number["P1"])
    assert TITLES["stores"] not in _headings(by_number["P2"])


def test_prompt_wording_asks_for_every_violation_or_nit_and_never_for_discards() -> None:
    template = adversarial.template_text("macro_principles")
    assert "一件も省かずに、違反か nit かを判断して" in template
    assert "捨て" not in template
    assert "discarded" not in template
    assert "指摘は少なく" not in template


def test_p1_example_identifiers_share_one_line_of_the_materials(tmp_path: Path) -> None:
    review = _range_review(tmp_path, _tree(P1_CASE / "before"), _tree(P1_CASE / "after"))
    lines = principle_prompt(AssemblyInput.load(review), SINGLE_SOURCE).splitlines()
    for group in (["WorkerConfig"], ["ClientConfig"], ["ServerConfig"]):
        assert any(all(word in line for word in group) for line in lines), group


def test_p2_example_identifiers_share_one_line_of_the_materials(tmp_path: Path) -> None:
    review = callers_case("p2-a04", tmp_path)
    lines = principle_prompt(AssemblyInput.load(review), YAGNI).splitlines()
    for group in (["Table.render.max_width", "なし"], ["Table.render.align", "なし"]):
        assert any(all(word in line for word in group) for line in lines), group
