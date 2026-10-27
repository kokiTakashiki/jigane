"""方針のレビューの実行の記録（runs.json）に書く形と役の値。

macro が書き、pipeline の過去の実測と usage の表が読む。
依存するのは同じ groups の群の表（definitions）だけ。
"""

from enum import StrEnum
from typing import assert_never

from jigane.groups.definitions import DESIGN_SHAPE, DIAGRAM_SHAPE, RunShape


class PolicyRole(StrEnum):
    """runs.json の起動の role に書く方針の役。新しい実行は DIAGRAM か DESIGN を書く。

    それ以外は廃止した形の役で、過去の実行の記録を読むためだけに残す。
    """

    DIAGRAM = "diagram"
    DESIGN = "design"
    SINGLE = "single"
    ROUTE = "discovery:route"
    VERIFY = "discovery:verify"
    PURPOSE = "discovery:purpose"
    CONCLUDE = "conclude"

    @property
    def label(self) -> str:
        """使用量の表に出す役の名前。廃止した形の役は、それと分かる名前にする。"""
        return _ROLE_LABELS[self]


_ROLE_LABELS = {
    PolicyRole.DIAGRAM: "図",
    PolicyRole.DESIGN: "設計書",
    PolicyRole.SINGLE: "一回の形（廃止）",
    PolicyRole.ROUTE: "四回の形（廃止）:道筋",
    PolicyRole.VERIFY: "四回の形（廃止）:検証",
    PolicyRole.PURPOSE: "四回の形（廃止）:目的",
    PolicyRole.CONCLUDE: "四回の形（廃止）:結論",
}


class PolicyShape(StrEnum):
    """runs.json の shape に書く方針の形。新しい実行は DIAGRAM か DESIGN を書く。

    SINGLE と FULL は廃止した形で、過去の実行の記録を読むためだけに残す。
    """

    DIAGRAM = "diagram"
    DESIGN = "design"
    SINGLE = "single"
    FULL = "full"

    @property
    def roles(self) -> tuple[PolicyRole, ...]:
        """形の起動が記録する役。廃止した FULL は役が四つに分かれる。"""
        match self:
            case PolicyShape.DIAGRAM:
                return (PolicyRole.DIAGRAM,)
            case PolicyShape.DESIGN:
                return (PolicyRole.DESIGN,)
            case PolicyShape.SINGLE:
                return (PolicyRole.SINGLE,)
            case PolicyShape.FULL:
                return (
                    PolicyRole.ROUTE,
                    PolicyRole.VERIFY,
                    PolicyRole.PURPOSE,
                    PolicyRole.CONCLUDE,
                )
            case _:
                assert_never(self)

    @property
    def run_shape(self) -> RunShape:
        """この形の起動の既定（effort、1 回の上限、往復）。形ごとの既定はここだけが持つ。

        廃止した形は新しい起動をしないので、いまの図の形と同じ値を返す。
        """
        match self:
            case PolicyShape.DESIGN:
                return DESIGN_SHAPE
            case PolicyShape.DIAGRAM | PolicyShape.SINGLE | PolicyShape.FULL:
                return DIAGRAM_SHAPE
            case _:
                assert_never(self)

    @property
    def usage_row_labels(self) -> tuple[str, ...]:
        """使用量の表（usage.csv）で、この形の起動の行が持つ役の名前。

        SINGLE は、役の名前を決める前に docs/reviews/usage.csv へ書いた「単一」の行も持つ。
        """
        labels = tuple(role.label for role in self.roles)
        match self:
            case PolicyShape.SINGLE:
                return (*labels, "単一")
            case PolicyShape.DIAGRAM | PolicyShape.DESIGN | PolicyShape.FULL:
                return labels
            case _:
                assert_never(self)

    @property
    def is_estimate_basis(self) -> bool:
        """見積もりの過去実額に、この形の行を数えるか。

        評価の事例は図の形なので、図の行だけを数える。廃止した一回の形は、道具を使う別の
        起動で、その実額は今の図だけの形と合わない。設計書の形と廃止した四回の形も、
        1 回の実行ぶんの行に揃わないか事例の形と違うので数えない。
        """
        match self:
            case PolicyShape.DIAGRAM:
                return True
            case PolicyShape.DESIGN | PolicyShape.SINGLE | PolicyShape.FULL:
                return False
            case _:
                assert_never(self)


def role_of(shape: PolicyShape) -> PolicyRole:
    """形の起動が記録する役。役が一つに決まらない形は ValueError。"""
    roles = shape.roles
    if len(roles) != 1:
        raise ValueError("廃止した四回の形は、一つの役に決まりません")
    return roles[0]


def estimate_basis_row_labels() -> tuple[str, ...]:
    """見積もりの過去実額に数える使用量の行の役の名前。形の定義から引く。"""
    return tuple(
        label
        for shape in PolicyShape
        if shape.is_estimate_basis
        for label in shape.usage_row_labels
    )
