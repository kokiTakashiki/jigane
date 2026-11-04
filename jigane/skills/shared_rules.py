"""複数の雛形が共有する規則の部品と、それを差し込む雛形の集合を定める表。

差し込みと版の算出は、発見役の三つの群も反証役と判定役も、この表から引く。
adversarial が import するので、adversarial を import しない。
"""

from dataclasses import dataclass
from importlib import resources

from jigane.groups.definitions import MACRO, MICRO, SECURITY, GroupId
from jigane.groups.viewpoints import of_group


def prompt_text(name: str) -> str:
    return (
        resources.files("jigane.skills").joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")
    )


@dataclass(frozen=True)
class SharedRule:
    # 雛形の差し込みの名前。雛形には `$<placeholder>` と書く
    placeholder: str
    # 本文のファイル名（prompts/<file>.md の <file>）
    file: str
    # 差し込む雛形の名前の集合
    templates: frozenset[str]


SHARED_RULES = (
    # 範囲外を理由に指摘を出さない、とはしない規則。方針の群は macro_standards 経由で受ける
    SharedRule("scope_rule", "scope_rule", frozenset({"micro", "security", "macro_standards"})),
    # 反証役と判定役が、範囲外を理由に指摘を崩さない規則
    SharedRule(
        "verifier_scope_rule", "verifier_scope_rule", frozenset({"rebuttal", "judge_rebuttal"})
    ),
)


# 観点の一覧を `$viewpoints` に差し込む雛形と、一覧の群
VIEWPOINT_LIST_GROUPS = {"micro": MICRO, "security": SECURITY, "macro_diagram": MACRO}


def viewpoint_list_text(group: GroupId) -> str:
    """群の観点の一覧。問いの文が空の観点は、雛形に載せない。"""
    lines = []
    for viewpoint in of_group(group):
        if not viewpoint.question:
            continue
        if viewpoint.name:
            lines.append(f"- {viewpoint.number} {viewpoint.name}: {viewpoint.question}")
        else:
            lines.append(f"- {viewpoint.number} {viewpoint.question}")
    return "\n".join(lines)


def rules_for(template: str) -> tuple[SharedRule, ...]:
    """雛形に差し込む規則。順は表の順で、版の算出の順でもある。"""
    return tuple(rule for rule in SHARED_RULES if template in rule.templates)


def rule_text(rule: SharedRule) -> str:
    return prompt_text(rule.file).strip()


def values_for(template: str) -> dict[str, str]:
    """雛形の置換に渡す、差し込みの名前と本文の対応。"""
    values = {rule.placeholder: rule_text(rule) for rule in rules_for(template)}
    if template in VIEWPOINT_LIST_GROUPS:
        values["viewpoints"] = viewpoint_list_text(VIEWPOINT_LIST_GROUPS[template])
    return values


def insert_into(template: str, text: str) -> str:
    """text の中の `$<差し込みの名前>` を、template に差し込む規則の本文に置き換える。"""
    for rule in rules_for(template):
        text = text.replace(f"${rule.placeholder}", rule_text(rule))
    return text
