"""1 回の起動の上限と effort の既定を決める唯一の場所。run と estimate が引く。"""

from dataclasses import dataclass, replace

from jigane.groups.definitions import (
    GROUP_BY_ID,
    PRINCIPLE_RUNS,
    Effort,
    GroupId,
    RunShape,
)
from jigane.groups.policy_runs import PolicyRole, PolicyShape, role_of
from jigane.llm_runs import codex
from jigane.llm_runs.claude import RunBudget, RunSettings
from jigane.llm_runs.pricing import InputCost


@dataclass(frozen=True)
class RunDefaults:
    effort: Effort | None
    budget: RunBudget


def resolve_run_defaults(
    group: GroupId,
    shape: PolicyShape | None,
    effort: Effort | None,
    model: str | None = None,
    max_budget_usd: float | None = None,
) -> RunDefaults:
    """1 回の起動の上限と effort の既定を決める。設定ファイルは読まない。

    上限は、`--max-budget-usd-per-call` の指定、形の既定、群の既定の順で決める。
    effort は、`--effort` の指定、形の既定、群の既定の順で決める。
    shape は方針だけが持ち、他の群では None。model が None なら群の既定のモデル。
    """
    group_shape: RunShape = GROUP_BY_ID[group].shape
    default_shape = shape.run_shape if shape is not None else group_shape

    def one(default_model: str, limit: float, output: int | None) -> RunBudget:
        basis = RunBudget(
            default_model, limit, default_shape.reserve_margin, output_allowance_tokens=output
        )
        return replace(
            basis,
            model=default_model if model is None else model,
            max_budget_usd=limit if max_budget_usd is None else max_budget_usd,
            reference=basis if model is not None and max_budget_usd is None else None,
            explicit_limit=max_budget_usd is not None,
        )

    budget = one(
        GROUP_BY_ID[group].model,
        default_shape.max_budget_usd,
        default_shape.output_allowance_tokens,
    )
    if shape is PolicyShape.DIAGRAM:
        budget = replace(
            budget,
            principles={
                number: one(run.model, run.max_budget_usd, run.output_allowance_tokens)
                for number, run in PRINCIPLE_RUNS.items()
            },
        )
    return RunDefaults(effort if effort is not None else default_shape.effort, budget)


def preflight_budget(
    settings: RunSettings,
    *,
    shape: PolicyShape | None = None,
    principle: str | None = None,
) -> RunBudget:
    """共通の起動前検査に、上流が役に割り当てる単価を渡す。"""
    budget = settings.budget
    if settings.has_spending_limit:
        return budget
    role = None if shape is None else role_of(shape)
    if principle is not None:
        role = PolicyRole.PRINCIPLE
    model = codex.model_for(role, principle)

    def price(input_tokens: int, output_tokens: int | None) -> InputCost:
        if output_tokens is None:
            rate = model.input_price
            nanos = input_tokens * rate
        else:
            rate = max(model.input_price, model.cache_write_price)
            nanos = model.estimate_nanos(input_tokens, output_tokens)
        return InputCost(
            nanos / codex.NANOS_PER_USD,
            rate * 1_000_000 / codex.NANOS_PER_USD,
            assumed_price=False,
        )

    reference = budget.reference
    if shape is not None:
        reference = resolve_run_defaults(GroupId.MACRO, shape, None).budget
    if reference is not None and principle is not None:
        reference = reference.for_principle(principle)
    basis = reference or budget
    return replace(
        basis,
        model=model.name,
        max_budget_usd=budget.max_budget_usd if budget.explicit_limit else basis.max_budget_usd,
        reserve_margin=False,
        compact_materials=False,
        reference=reference,
        explicit_limit=budget.explicit_limit,
        price_launch=price,
    )
