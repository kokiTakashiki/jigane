from dataclasses import replace
from pathlib import Path

import pytest
from test_policy_field_copies import _range_review, _tree
from test_policy_principles import P1_CASE

from jigane.figures.models import Figure
from jigane.groups.viewpoints import design_principles
from jigane.reviews.lifecycle import Review
from jigane.skills import policy_figures
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import REGISTRY, PolicyFigure, build_figure
from jigane.skills.policy_principles import figure_keys_of


def _review(tmp_path: Path) -> Review:
    return _range_review(tmp_path, _tree(P1_CASE / "before"), _tree(P1_CASE / "after"))


def _direct(figure: PolicyFigure, assembly: AssemblyInput) -> Figure | None:
    """共有を通さず、図の builder を直接呼んだ結果。"""
    if figure.build_of is not None:
        return figure.build_of(assembly)
    return figure.build(assembly.review)


def test_build_figure_builds_once_per_assembly(tmp_path: Path) -> None:
    review = _review(tmp_path)
    calls: list[str] = []
    figure = next(f for f in REGISTRY if f.build_of is None)
    real = figure.build

    def counting(target: Review) -> Figure | None:
        calls.append(figure.key)
        return real(target)

    counted = replace(figure, build=counting)
    assembly = AssemblyInput.load(review)
    first = build_figure(counted, assembly)
    second = build_figure(counted, assembly)
    assert calls == [figure.key]
    assert first is second
    build_figure(counted, AssemblyInput.load(review))
    assert calls == [figure.key, figure.key]


def test_policy_sections_are_built_once_per_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path)
    real = policy_figures.policy_sections
    calls: list[str] = []

    def counting(target: Review) -> policy_figures.PolicySections:
        calls.append("built")
        return real(target)

    monkeypatch.setattr(policy_figures, "policy_sections", counting)
    assembly = AssemblyInput.load(review)
    first = policy_figures.shared_policy_sections(assembly)
    assert policy_figures.shared_policy_sections(assembly) is first
    assert calls == ["built"]
    policy_figures.shared_policy_sections(AssemblyInput.load(review))
    assert calls == ["built", "built"]


def test_shared_figure_texts_equal_the_direct_builds(tmp_path: Path) -> None:
    review = _review(tmp_path)
    shared = AssemblyInput.load(review)
    sections = policy_figures.shared_policy_sections(shared)
    for figure in REGISTRY:
        built = build_figure(figure, shared)
        direct = _direct(figure, AssemblyInput.load(review))
        assert (built.text() if built else None) == (direct.text() if direct else None)
    for keys in [None, *(figure_keys_of(p) for p in design_principles())]:
        reference = policy_figures.prompt_figure_texts(
            AssemblyInput.load(review), policy_figures.policy_sections(review).figures, keys
        )
        assert policy_figures.prompt_figure_texts(shared, sections.figures, keys) == reference
