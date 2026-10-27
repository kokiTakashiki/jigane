"""shared_rules の表と、雛形の差し込みの印、プロンプトの版が食い違わないことを縛る。"""

from importlib import resources

import pytest

from jigane.skills import adversarial, macro_shared, shared_rules
from jigane.skills.shared_rules import SHARED_RULES, SharedRule

RULE_IDS = [rule.placeholder for rule in SHARED_RULES]


def _template_names() -> list[str]:
    prompts = resources.files("jigane.skills").joinpath("prompts")
    return sorted(
        path.name.removesuffix(".md") for path in prompts.iterdir() if path.name.endswith(".md")
    )


def _version_of(template: str) -> str:
    # 方針の群は雛形の版を macro_shared が部品ごと作る。macro_standards は図の形から差し込まれる
    if template == macro_shared.PROMPT_PARTS["standards"]:
        return macro_shared.stamp_template("macro_diagram", "").version
    return adversarial.prompt_version(template)


@pytest.mark.parametrize("rule", SHARED_RULES, ids=RULE_IDS)
def test_marker_appears_in_exactly_the_templates_of_the_table(rule: SharedRule) -> None:
    marked = {
        name
        for name in _template_names()
        if f"${rule.placeholder}" in shared_rules.prompt_text(name)
    }
    assert marked == set(rule.templates)


@pytest.mark.parametrize("rule", SHARED_RULES, ids=RULE_IDS)
def test_rule_body_has_no_marker_left_after_insertion(rule: SharedRule) -> None:
    for template in sorted(rule.templates):
        inserted = shared_rules.insert_into(template, shared_rules.prompt_text(template))
        assert f"${rule.placeholder}" not in inserted
        assert shared_rules.rule_text(rule) in inserted


@pytest.mark.parametrize("rule", SHARED_RULES, ids=RULE_IDS)
def test_version_follows_the_rule_body(rule: SharedRule, monkeypatch: pytest.MonkeyPatch) -> None:
    original = shared_rules.prompt_text
    changed = {template: _version_of(template) for template in sorted(rule.templates)}
    monkeypatch.setattr(
        shared_rules,
        "prompt_text",
        lambda name: original(name) + "追記" if name == rule.file else original(name),
    )
    for template, before in changed.items():
        assert _version_of(template) != before, template
