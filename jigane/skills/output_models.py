"""LLM の出力を検証して型へ読むモデル。claude に渡すスキーマも同じモデルから作る。

役ごとのモデルはここへ足す。モデルは未知の項目を拒み、型の変換をしない。
"""

from types import GenericAlias
from typing import Annotated, Any

from pydantic import MISSING, BaseModel, Field, JsonValue, StringConstraints, create_model

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


def json_schema_of(model: type[BaseModel]) -> dict[str, JsonValue]:
    """claude の `--json-schema` へ渡すスキーマ。title は除き、$ref は展開する。"""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    return _object_without_titles(_object_with_references(schema, definitions))


def _object_with_references(
    node: dict[str, JsonValue], definitions: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    # 入れ子のモデルは $defs への $ref になる。旧のスキーマは入れ子を直に書いていたので展開する
    reference = node.get("$ref")
    if isinstance(reference, str):
        target = definitions[reference.removeprefix("#/$defs/")]
        if isinstance(target, dict):
            # 定義の description はクラスの docstring 由来で、コードの読み手に向けた文。
            # claude に渡すのは、欄に Field(description=...) で明示した文だけ（$ref の隣に載る）
            expanded = _object_with_references(
                {key: value for key, value in target.items() if key != "description"}, definitions
            )
            beside = {key: value for key, value in node.items() if key != "$ref"}
            return {**expanded, **_object_with_references(beside, definitions)}
    return {key: _with_references(value, definitions) for key, value in node.items()}


def _with_references(node: JsonValue, definitions: dict[str, JsonValue]) -> JsonValue:
    if isinstance(node, dict):
        return _object_with_references(node, definitions)
    if isinstance(node, list):
        return [_with_references(item, definitions) for item in node]
    # 葉の値（型名や数値）はそのまま返す
    return node


def _object_without_titles(node: dict[str, JsonValue]) -> dict[str, JsonValue]:
    # 文字列の title だけが説明用。値が辞書の title は、title という名の項目の定義
    return {
        key: _without_titles(value)
        for key, value in node.items()
        if not (key == "title" and isinstance(value, str))
    }


def _without_titles(node: JsonValue) -> JsonValue:
    if isinstance(node, dict):
        return _object_without_titles(node)
    if isinstance(node, list):
        return [_without_titles(item) for item in node]
    # 葉の値（型名や数値）はそのまま返す
    return node
