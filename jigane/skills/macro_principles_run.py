"""方針の群の起動。図の形は図の起動に加えて原則ごとに一本ずつ、設計書の形は一本を走らせる。"""

from dataclasses import replace

from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude
from jigane.llm_runs.defaults import resolve_run_defaults
from jigane.skills import macro, policy_principles
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import DiagramPolicyEstimate, PolicyEstimate


def principle_launches(assembly: AssemblyInput) -> tuple[macro.ExtraLaunch, ...]:
    """図の形の、原則ごとの起動。設計書の形は原則の起動を持たない。"""
    if macro.policy_shape(assembly.review) is not PolicyShape.DIAGRAM:
        return ()
    return tuple(
        macro.ExtraLaunch(item.principle.number, item.prompt, index)
        for index, item in enumerate(policy_principles.principle_prompts(assembly), start=1)
    )


def run_policy(assembly: AssemblyInput, settings: claude.RunSettings) -> macro.MacroRun:
    """方針の群を走らせる唯一の入口。"""
    return macro.run_macro(assembly, settings, principle_launches(assembly))


def estimate_policy(assembly: AssemblyInput) -> PolicyEstimate:
    """方針の群の見積もり。図の形は図の起動と原則ごとの起動の和、設計書の形は一本。"""
    estimate = macro.estimate_macro(assembly)
    if not isinstance(estimate, DiagramPolicyEstimate):
        # 設計書の形は原則の起動を持たず、見積もりは一本のまま
        return estimate
    launches = principle_launches(assembly)
    prompts = [launch.prompt.encode("utf-8") for launch in launches]
    budget = resolve_run_defaults(macro.GROUP, PolicyShape.DIAGRAM, None).budget
    tokens = [
        budget.for_principle(launch.viewpoint).prompt_tokens(len(prompt))
        for launch, prompt in zip(launches, prompts, strict=True)
    ]
    return replace(
        estimate,
        principle_input_tokens=tuple(
            (launch.viewpoint, count) for launch, count in zip(launches, tokens, strict=True)
        ),
        discovery_input_tokens=estimate.discovery_input_tokens + sum(tokens),
        materials_bytes=estimate.materials_bytes + sum(len(prompt) for prompt in prompts),
        planned_invocations=1 + len(prompts),
    )
