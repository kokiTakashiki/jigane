"""LLM の出力を検証して型へ読むモデル。claude に渡すスキーマも同じモデルから作る。

役ごとのモデルはここへ足す。モデルは未知の項目を拒み、型の変換をしない。
"""

from types import GenericAlias
from typing import Annotated, Any

from pydantic import MISSING, BaseModel, Field, StringConstraints, create_model

from jigane.llm_runs.finding_model import PLACES_DESCRIPTION, JudgeVerdict, Location, ToolClaim
from jigane.llm_runs.output_common import STRICT_OUTPUT, NonNegativeSchemaInt


class BundleGroups(BaseModel):
    # 開示済みの限界の束ねの出力。同じ限界を指す対象の位置の組を並べる。
    # docstring にすると description としてスキーマへ載るので、コメントで書く

    model_config = STRICT_OUTPUT

    groups: list[list[NonNegativeSchemaInt]]


class ThemeGroup(BaseModel):
    # 束ねの出力の一つのテーマ。題と、そのテーマに入れる指摘の id を持つ

    model_config = STRICT_OUTPUT

    title: Annotated[str, StringConstraints(min_length=1)]
    finding_ids: list[str]


class ThemesOutput(BaseModel):
    # 指摘の選別の出力。指摘をテーマへ束ねた組を並べる

    model_config = STRICT_OUTPUT

    themes: list[ThemeGroup]


class RoleItem(BaseModel):
    # 検証の役の出力の一件。index は役に渡した候補の並びの位置

    model_config = STRICT_OUTPUT

    index: NonNegativeSchemaInt


class RebuttalItem(RoleItem):
    challenge: str
    checked: list[Location]


class AlternativeItem(RoleItem):
    alternative: str


class DefenseItem(RoleItem):
    defense: str


DisclosedAt = Annotated[
    str,
    StringConstraints(min_length=1),
    Field(
        description=(
            "verdict が rejected で、棄却の理由が docstring、README、設計書、決定に"
            "開示済みの既知の限界で、その開示が害や条件まで書いているときだけ書く。"
            f"{PLACES_DESCRIPTION}"
        )
    ),
]


class JudgeAlternativeItem(RoleItem):
    verdict: JudgeVerdict
    reason: str
    disclosed_at: DisclosedAt | MISSING = MISSING


class JudgeRebuttalItem(RoleItem):
    recheck: list[Location]
    verdict: JudgeVerdict
    reason: str
    disclosed_at: DisclosedAt | MISSING = MISSING
    verified: bool
    tool_claim: ToolClaim


def role_output_model(item: type[RoleItem]) -> type[BaseModel]:
    """役の出力。items に役の一件の型を並べる。"""
    fields: dict[str, Any] = {"items": (GenericAlias(list, (item,)), ...)}
    return create_model(f"{item.__name__}Output", __config__=STRICT_OUTPUT, **fields)
