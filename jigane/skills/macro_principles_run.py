"""方針の群の起動。図の形は図の起動に加えて原則ごとに一本ずつ、設計書の形は一本を走らせる。"""

from dataclasses import replace

from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude
from jigane.llm_runs.defaults import resolve_run_defaults
from jigane.skills import macro, macro_compact, policy_launch, policy_principles
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import DiagramPolicyEstimate, PolicyEstimate

COMPACT_ESTIMATE_NOTE = (
    "図の起動 1 回と、問える形がある原則ごとの起動を見積もります。"
    "宣言、変更節点と根拠位置、役に関係する図表、前の回の指摘を使い、道具は渡しません。"
    "出力と推論の分は含みません。"
)


def principle_launches(
    assembly: AssemblyInput, *, compact: bool = False
) -> tuple[policy_launch.ExtraLaunch, ...]:
    """図の形の、原則ごとの起動。assembly の中で一度だけ組み立て、見積もりと実行で共有する。"""

    def build() -> tuple[policy_launch.ExtraLaunch, ...]:
        # 設計書の形は原則の起動を持たない
        if macro.policy_shape(assembly.review) is not PolicyShape.DIAGRAM:
            return ()
        if compact:
            return tuple(
                policy_launch.ExtraLaunch(
                    principle.number, macro_compact.build_prompt(assembly, principle)
                )
                for principle in policy_principles.applicable_principles(
                    assembly, include_policy_only=True
                )
            )
        return tuple(
            policy_launch.ExtraLaunch(item.principle.number, item.prompt)
            for item in policy_principles.principle_prompts(assembly)
        )

    return assembly.remember(("principle_launches", compact), build)


def main_prompt(assembly: AssemblyInput, *, compact: bool) -> str:
    if compact and macro.policy_shape(assembly.review) is PolicyShape.DIAGRAM:
        return assembly.remember("compact_diagram", lambda: macro_compact.build_prompt(assembly))
    return macro.remembered_prompt(assembly)


def run_policy(assembly: AssemblyInput, settings: claude.RunSettings) -> macro.MacroRun:
    """方針の群を走らせる唯一の入口。"""
    budget = settings.budget
    plan = macro.remembered_plan(assembly)
    if plan.missing:
        return macro.run_macro(assembly, settings, plan=plan)
    compact = budget.compact_materials
    prompt = main_prompt(assembly, compact=compact)
    extras = principle_launches(assembly, compact=compact)
    if compact and plan.shape is PolicyShape.DIAGRAM:
        plan = replace(
            plan, stamp=macro_compact.stamp, stamp_principle=macro_compact.stamp_principle
        )
    return macro.run_macro(
        assembly, settings, extras, plan=replace(plan, build_prompt=lambda: prompt)
    )


def estimate_policy(
    assembly: AssemblyInput, *, budget: claude.RunBudget | None = None
) -> PolicyEstimate:
    """方針の群の見積もり。図の形は図の起動と原則ごとの起動の和、設計書の形は一本。"""
    compact = budget is not None and budget.compact_materials
    prompt = (
        main_prompt(assembly, compact=True)
        if compact and not macro.remembered_plan(assembly).missing
        else None
    )
    estimate = macro.estimate_macro(assembly, prompt)
    if not isinstance(estimate, DiagramPolicyEstimate):
        # 設計書の形は原則の起動を持たず、見積もりは一本のまま
        return estimate
    launches = principle_launches(assembly, compact=compact)
    prompts = [launch.prompt.encode("utf-8") for launch in launches]
    basis = resolve_run_defaults(macro.GROUP, PolicyShape.DIAGRAM, None).budget
    tokens = [
        basis.for_principle(launch.viewpoint).prompt_tokens(len(prompt))
        for launch, prompt in zip(launches, prompts, strict=True)
    ]
    return replace(
        estimate,
        note=COMPACT_ESTIMATE_NOTE if compact else estimate.note,
        principle_input_tokens=tuple(
            (launch.viewpoint, count) for launch, count in zip(launches, tokens, strict=True)
        ),
        discovery_input_tokens=estimate.discovery_input_tokens + sum(tokens),
        materials_bytes=estimate.materials_bytes + sum(len(prompt) for prompt in prompts),
        planned_invocations=1 + len(prompts),
    )
