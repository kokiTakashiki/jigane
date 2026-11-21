import re
from pathlib import Path

import pytest
from review_fakes import make_review
from test_policy_callers import _case as callers_case
from test_policy_classes import _interface_region, whole
from test_policy_field_copies import _range_review, _tree

from jigane.groups.viewpoints import (
    INFORMATION_HIDING,
    KISS,
    SEPARATION_OF_CONCERNS,
    SINGLE_SOURCE,
    YAGNI,
    design_principles,
)
from jigane.policy_materials.changed_files import PrebuiltSection
from jigane.skills import adversarial, macro_compact, macro_principles_run, policy_figures
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import PROMPT_OMITTED, REGISTRY
from jigane.skills.policy_principles import (
    FIGURE_KEYS_BY_PRINCIPLE,
    TREE_KEY,
    figure_keys_of,
    principle_prompt,
    principle_prompts,
    principle_text,
)
from jigane.storage.store import ReviewKind

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
        INFORMATION_HIDING: ("stores", "readers", "classes"),
    }


def test_every_principle_has_an_entry() -> None:
    assert set(FIGURE_KEYS_BY_PRINCIPLE) == set(design_principles(include_policy_only=True))


def test_figure_keys_exist_in_registry_and_are_not_omitted() -> None:
    known = {figure.key for figure in REGISTRY} - PROMPT_OMITTED
    for keys in FIGURE_KEYS_BY_PRINCIPLE.values():
        assert set(keys) <= known


def test_tree_figure_is_given_to_every_principle() -> None:
    for principle in design_principles(include_policy_only=True):
        assert TREE_KEY in figure_keys_of(principle)


def test_unknown_figure_key_is_rejected(tmp_path: Path) -> None:
    review = _range_review(tmp_path, _tree(P1_CASE / "before"), _tree(P1_CASE / "after"))
    sections = policy_figures.policy_sections(review)
    with pytest.raises(ValueError, match="no_such_figure"):
        policy_figures.prompt_figure_texts(
            AssemblyInput.load(review), sections.figures, ("tree", "no_such_figure")
        )


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


@pytest.mark.parametrize("compact", [False, True])
def test_p1_example_identifiers_share_one_line_of_the_materials(
    tmp_path: Path, *, compact: bool
) -> None:
    review = _range_review(tmp_path, _tree(P1_CASE / "before"), _tree(P1_CASE / "after"))
    launches = macro_principles_run.principle_launches(AssemblyInput.load(review), compact=compact)
    [p1] = [launch.prompt for launch in launches if launch.viewpoint == SINGLE_SOURCE.number]
    lines = p1.splitlines()
    assert any("WorkerConfig" in line and "ClientConfig" in line for line in lines)
    assert any("WorkerConfig" in line and "ServerConfig" in line for line in lines)


def test_p2_example_identifiers_share_one_line_of_the_materials(tmp_path: Path) -> None:
    review = callers_case("p2-a04", tmp_path)
    lines = principle_prompt(AssemblyInput.load(review), YAGNI).splitlines()
    for group in (["Table.render.max_width", "なし"], ["Table.render.align", "なし"]):
        assert any(all(word in line for word in group) for line in lines), group


def test_compact_p3_launch_pairs_all_changes_in_a_public_signature(tmp_path: Path) -> None:
    old = (
        "def publish(channel: str, limit: int = 10, retries: int = 1, retired: bool = False, "
        "encoding: str = 'utf8'):"
    )
    new = (
        "def publish(channel: str, limit: int = 20, retries: int = 1, encoding: str = 'utf8', "
        "label: str = '', quiet: bool = False, safe: bool = True, tracing: bool = False):"
    )
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "m.py").write_text(old + "\n    return channel\n", encoding="utf-8")
    (head / "m.py").write_text(new + "\n    return channel.upper()\n", encoding="utf-8")
    span = {"start_line": 1, "end_line": 2}
    review = make_review(
        head,
        ["m.py"],
        base_tree=base,
        judgment_map={
            "segments": [whole("m.py", 2)],
            "regions": [_interface_region(1, "publish", span, span)],
            "line_counts": {},
        },
        kind=ReviewKind.RANGE,
    )
    launches = macro_principles_run.principle_launches(AssemblyInput.load(review), compact=True)
    [p3] = [launch for launch in launches if launch.viewpoint == KISS.number]
    assert adversarial.template_text(macro_compact.P3_GUIDE).strip() in p3.prompt
    [paired] = [
        line.split(" ~ ", 1)[1] for line in p3.prompt.splitlines() if " ~ def publish(" in line
    ]
    marks = re.compile(r"⟦([^¦]*)¦([^⟧]*)⟧")
    assert len(marks.findall(paired)) >= 3
    assert marks.sub(r"\1", paired) == old
    assert marks.sub(r"\2", paired) == new
    assert "return channel" not in p3.prompt
