"""1 回の起動の上限と effort の既定を決める唯一の場所。run と estimate が引く。"""

from dataclasses import dataclass

from jigane.groups.definitions import GROUP_BY_ID, Effort, GroupId, RunShape
from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs.claude import RunBudget


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
    return RunDefaults(
        effort if effort is not None else default_shape.effort,
        RunBudget(
            model if model is not None else GROUP_BY_ID[group].model,
            max_budget_usd if max_budget_usd is not None else default_shape.max_budget_usd,
            default_shape.reserve_margin,
        ),
    )
