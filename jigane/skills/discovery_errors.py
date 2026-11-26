"""発見役の失敗の型。入力の見積もりで見送った起動の stop を運ぶ InputBudgetError を含む。

pipeline より下、input_budget（BudgetStop）より上に置く。
"""

from collections.abc import Sequence

from jigane.groups.definitions import GroupId
from jigane.llm_runs.claude import InvocationRecord
from jigane.llm_runs.invocation import FailureKind, InvocationError
from jigane.skills.input_budget import BudgetStop

MIN_EMPTY_REVIEW_OUTPUT_TOKENS = 100


def require_review_output(record: InvocationRecord, findings: Sequence[object]) -> None:
    """主たるレビュー起動で、検証済みの配列を後処理する前に確かめる。"""
    tokens = record.reported_output_tokens
    if not findings and tokens is not None and tokens < MIN_EMPTY_REVIEW_OUTPUT_TOKENS:
        raise InvocationError(
            f"警告: 出力が {tokens} トークン（{MIN_EMPTY_REVIEW_OUTPUT_TOKENS} 未満）で"
            "指摘を得られず、レビューが欠けています。"
            "もう一度実行するか、別のエージェントで確かめてください",
            record,
            FailureKind.SHORT_EMPTY_OUTPUT,
        )


class DiscoveryError(Exception):
    """発見役が失敗した。group は失敗した群の内部名。"""

    def __init__(self, group: GroupId, message: str) -> None:
        super().__init__(message)
        self.group = group


class InputBudgetError(DiscoveryError):
    """入力だけで 1 回の起動の上限の費用に達する見込みのため、起動せずに止めた。"""

    def __init__(self, group: GroupId, message: str, stop: BudgetStop) -> None:
        super().__init__(group, message)
        self.stop = stop


def all_launches_over_budget(
    group: GroupId, errors: Sequence[InputBudgetError]
) -> InputBudgetError:
    """すべての起動が入力の見積もりで見送られた回の止め方。記録に残す実行が無い（シャードと同じ）。

    起動の数は errors の数と等しい（一本でも走ったなら、この止め方は使わない）。
    """
    if len(errors) == 1:
        return errors[0]
    return InputBudgetError(
        group,
        f"全 {len(errors)} 起動が失敗しました"
        f"（{len(errors)} 個は起動しませんでした）。" + str(errors[0]),
        errors[0].stop,
    )
