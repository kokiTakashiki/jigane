"""指摘（finding）のモデル。旧の JSON スキーマと同じ入力を受け付ける。"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from string import ascii_letters
from typing import Annotated, Any, Literal, Self

from pydantic import (
    MISSING,
    AfterValidator,
    BaseModel,
    Field,
    JsonValue,
    StringConstraints,
    create_model,
    model_validator,
)

from jigane.findings.discard import DiscardKind
from jigane.findings.records import ToolBasis, Triage, Verdict
from jigane.groups.definitions import GroupId
from jigane.groups.viewpoints import of_group
from jigane.llm_runs.claude_schema import json_schema_of
from jigane.llm_runs.output_common import STRICT_OUTPUT, SchemaInt

NonEmpty = Annotated[str, StringConstraints(min_length=1)]

# JSON スキーマの pattern は部分一致で、`$` は末尾の改行の前にも合う。
# pydantic の pattern は Rust の正規表現で `$` の意味が違うため、同じ re で確かめる
_FINDING_ID = re.compile(r"^F[0-9]+$")
_ASCII_LETTERS = frozenset(ascii_letters)


def _finding_id(value: str) -> str:
    if _FINDING_ID.search(value) is None:
        raise ValueError("指摘の id は F と数字")
    return value


def _relative_path(value: str) -> str:
    # 旧のスキーマの pattern と同じ三条件を、一つずつ確かめる
    if value.startswith("/"):
        raise ValueError("path は / で始められない")
    if value.startswith("\\"):
        raise ValueError("path は \\ で始められない")
    drive, colon = value[:1], value[1:2]
    if colon == ":" and drive in _ASCII_LETTERS:
        raise ValueError("path はドライブ文字で始められない")
    return value


# 検証はコード（_finding_id）が行う。pattern は claude に渡すスキーマにだけ載る
FindingId = Annotated[
    str, AfterValidator(_finding_id), Field(json_schema_extra={"pattern": "^F[0-9]+$"})
]
# 整数値の浮動小数（1.0）は受け、1.5 や真偽値は strict が拒む
Line = Annotated[int, Field(ge=1), SchemaInt]


class Location(BaseModel):
    model_config = STRICT_OUTPUT

    # 検証はコード（_relative_path）が行う。pattern は claude に渡すスキーマにだけ載る
    path: Annotated[
        NonEmpty,
        AfterValidator(_relative_path),
        Field(json_schema_extra={"pattern": r"^(?![/\\])(?![A-Za-z]:)"}),
    ]
    start_line: Line
    end_line: Line


# 列挙の欄は Field(strict=False) にする。strict のままでは列挙のインスタンスしか受けない


# 判定の語彙の欄。judge の役の出力モデルも同じ型を使う
JudgeVerdict = Annotated[Verdict, Field(strict=False)]


class ToolClaim(BaseModel):
    model_config = STRICT_OUTPUT

    relies: bool
    basis: Annotated[ToolBasis, Field(strict=False)]
    version: str


class Rebuttal(BaseModel):
    model_config = STRICT_OUTPUT

    kind: Literal["rebuttal"]
    challenge: str
    recheck: list[Location]
    verdict: JudgeVerdict
    reason: str
    verified: bool | MISSING = MISSING
    tool_claim: ToolClaim | MISSING = MISSING


class Alternative(BaseModel):
    model_config = STRICT_OUTPUT

    kind: Literal["alternative"]
    alternative: str
    defense: str
    verdict: JudgeVerdict
    reason: str


class NitReason(BaseModel):
    model_config = STRICT_OUTPUT

    kind: Annotated[DiscardKind, Field(strict=False)]
    text: NonEmpty
    concern: NonEmpty | None
    places: list[NonEmpty]


class OutOfScope(BaseModel):
    model_config = STRICT_OUTPUT

    paths: list[NonEmpty]
    reason: NonEmpty


Adjudication = Annotated[Rebuttal | Alternative, Field(discriminator="kind")]


class Finding(BaseModel):
    model_config = STRICT_OUTPUT

    id: FindingId
    severity: Literal["critical", "warning", "nit"]
    group: Annotated[GroupId, Field(strict=False)]
    viewpoint: NonEmpty
    claim: NonEmpty
    body: str
    evidence: list[Location]
    declaration_ref: Location | None
    judgment_region: str | None
    references: list[NonEmpty]
    adjudication: Adjudication | None
    theme: str | None
    triage: Annotated[Triage, Field(strict=False)] | None
    # 以下は無いことを許し、null は許さない。MISSING は「欄が無い」を表す
    nit_reason: NitReason | MISSING = MISSING
    deepens: FindingId | MISSING = MISSING
    from_concern: FindingId | MISSING = MISSING
    out_of_scope: OutOfScope | MISSING = MISSING
    triage_reason: NonEmpty | MISSING = MISSING

    @model_validator(mode="after")
    def _check_conditions(self) -> Self:
        if (
            self.triage is not None
            and self.triage.traits.needs_out_of_scope
            and self.out_of_scope is MISSING
        ):
            raise ValueError("triage が out_of_scope なら out_of_scope が要る")
        if self.viewpoint == "discarded":
            if self.severity != "nit" or self.evidence:
                raise ValueError("discarded の重さは nit で、根拠は空")
        elif not self.evidence:
            raise ValueError("discarded でなければ根拠は 1 件以上")
        return self


# 範囲の印と開示の場所を、役の出力に足す欄の名前。skills の scope と disclosed が引く
KEY_IN_SCOPE = "in_scope"
KEY_SCOPE_REASON = "scope_reason"
KEY_DISCLOSED_AT = "disclosed_at"
KEY_DEEPENS = "deepens"

# 開示の場所を書く欄の説明の共通部分。発見役、判定役、方針の群の結論の役の欄が引く
PLACES_DESCRIPTION = "開示の場所（ファイルと節）を書く。複数の場所は読点で区切って並べる。"
DISCLOSED_AT_DESCRIPTION = f"kind が disclosed_limitation のときだけ書く。{PLACES_DESCRIPTION}"

# 発見役の欄の説明。結論の役の欄（DISCLOSED_AT_DESCRIPTION）とは書く条件が違う
DISCOVERY_DISCLOSED_AT_DESCRIPTION = (
    f"開示済みの既知の限界を挙げるときだけ書く。{PLACES_DESCRIPTION}そのとき severity は nit"
)

# 保存する指摘のうち、発見役が決めず後の段が付ける欄
ASSIGNED_LATER = ("id", "group", "adjudication", "theme", "triage")

_SOURCE_ID = re.compile(r"^S[0-9]+$")


def _source_id(value: str) -> str:
    if _SOURCE_ID.search(value) is None:
        raise ValueError("元の指摘の id は S と数字")
    return value


SourceId = Annotated[
    str, AfterValidator(_source_id), Field(json_schema_extra={"pattern": "^S[0-9]+$"})
]


class LenientLocation(BaseModel):
    """入口で受ける位置。path の形は問わない（絶対パスは後で相対に直すため）。"""

    model_config = STRICT_OUTPUT

    path: NonEmpty
    start_line: Line
    end_line: Line


class MergedGroup(BaseModel):
    model_config = STRICT_OUTPUT

    finding_index: Annotated[int, Field(ge=0), SchemaInt]
    sources: Annotated[list[SourceId], Field(min_length=2)]


class DiscardedSourceCore(BaseModel):
    """disclosed_at を読まない後段へ渡す、捨てた候補の形。"""

    model_config = STRICT_OUTPUT

    source: SourceId
    reason: NonEmpty
    # 項目を欠く出力も受け、欠けた分は読み戻す側が文から補う
    kind: Annotated[DiscardKind, Field(strict=False)] | MISSING = MISSING
    concern: NonEmpty | MISSING = MISSING


class DiscardedSource(DiscardedSourceCore):
    # findings は llm_runs を読めないため、同じ項目の組を discard.DiscardedCandidate が別に持つ
    disclosed_at: Annotated[NonEmpty, Field(description=DISCLOSED_AT_DESCRIPTION)] | MISSING = (
        MISSING
    )


def _viewpoint_check(viewpoints: tuple[str, ...]) -> AfterValidator:
    allowed = frozenset(viewpoints)

    def check(value: str) -> str:
        if value in allowed:
            return value
        raise ValueError("観点の識別子が許す値の外")

    return AfterValidator(check)


@dataclass(frozen=True)
class RoleSpec:
    """役ごとの違い。同じ値には同じモデルを返すため、不変にする。"""

    # 渡すと、観点はその群の表の全行に限る
    group: GroupId | None = None
    require_references: bool = False
    require_scope_fields: bool = False
    allow_disclosed_at: bool = False
    # findings に加え merged と discarded を必須で持つ
    conclusion: bool = False
    # 渡すと、その欄だけを持つ。渡さないと、ASSIGNED_LATER を除いた全部を持つ
    model_written: tuple[str, ...] | None = None
    # 渡すと、観点はこの一つに限る（原則ごとの起動が、その原則の番号だけを許すため）
    only_viewpoint: str | None = None
    # 真なら、後段が読まない merged と discarded の disclosed_at を持たず、deepens の null を受ける
    omit_unread: bool = False

    def __post_init__(self) -> None:
        # _apply_spec は効く欄が無い指定を黙って飛ばす。組み立てた後では気づけないため、ここで拒む
        if self.model_written is None:
            return
        if self.group is not None and "viewpoint" not in self.model_written:
            raise ValueError("group は model_written に viewpoint が要る")
        if self.require_references and "references" not in self.model_written:
            raise ValueError("require_references は model_written に references が要る")

    def schema(self) -> dict[str, Any]:
        """claude に渡すスキーマ。"""
        return json_schema_of(role_findings_model(self))

    def lenient_model(self) -> type[BaseModel]:
        """入口の検証に使うモデル。schema と同じ形で、location.path の形の決まりを外してある。"""
        return role_findings_model(self, lenient=True)

    def viewpoint_numbers(self) -> tuple[str, ...] | None:
        if self.only_viewpoint is not None:
            return (self.only_viewpoint,)
        if self.group is None:
            return None
        return tuple(viewpoint.number for viewpoint in of_group(self.group))


def _viewpoint_schema(viewpoints: tuple[str, ...]) -> Callable[[dict[str, JsonValue]], None]:
    """claude に渡す観点のスキーマ。"""

    def extend(schema: dict[str, JsonValue]) -> None:
        schema.update({"enum": list(viewpoints)})

    return extend


def _shown_fields(model_written: tuple[str, ...] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, info in Finding.model_fields.items():
        hidden = name in ASSIGNED_LATER if model_written is None else name not in model_written
        if not hidden:
            fields[name] = (info.annotation, info)
    return fields


def _apply_spec(fields: dict[str, Any], spec: RoleSpec, location: type[BaseModel]) -> None:
    # 根拠は常に 1 件以上を要る。保存の形（Finding）だけが、捨てた候補から写す指摘の空の根拠を許す
    if "evidence" in fields:
        fields["evidence"] = (list[location], Field(min_length=1))  # type: ignore[valid-type]
    if "declaration_ref" in fields:
        fields["declaration_ref"] = (location | None, ...)
    viewpoints = spec.viewpoint_numbers()
    if viewpoints is not None and "viewpoint" in fields:
        fields["viewpoint"] = (
            Annotated[
                str,
                _viewpoint_check(viewpoints),
                Field(json_schema_extra=_viewpoint_schema(viewpoints)),
            ],
            ...,
        )
    if spec.require_references and "references" in fields:
        fields["references"] = (list[NonEmpty], Field(min_length=1))
    if spec.require_scope_fields:
        fields[KEY_IN_SCOPE] = (bool, ...)
        fields[KEY_SCOPE_REASON] = (str, ...)
    if spec.omit_unread and KEY_DEEPENS in fields:
        # null で再試行を使い切らないよう受ける。null を欄が無い形へ直すのは読む側（macro）
        fields[KEY_DEEPENS] = (FindingId | None | MISSING, MISSING)
    if spec.allow_disclosed_at:
        fields[KEY_DISCLOSED_AT] = (
            Annotated[NonEmpty, Field(description=DISCOVERY_DISCLOSED_AT_DESCRIPTION)] | MISSING,
            MISSING,
        )


@cache
def role_findings_model(spec: RoleSpec, *, lenient: bool = False) -> type[BaseModel]:
    """役の出力のモデル。Finding から、LLM が書く欄だけを持つ形を組み立てる。

    lenient は location.path の形の決まりを外す（入口の検証用）。
    """
    location: type[BaseModel] = LenientLocation if lenient else Location
    fields = _shown_fields(spec.model_written)
    _apply_spec(fields, spec, location)
    item = create_model("RoleFinding", __config__=STRICT_OUTPUT, **fields)
    top: dict[str, Any] = {"findings": (list[item], ...)}  # type: ignore[valid-type]
    if spec.conclusion:
        if not spec.omit_unread:
            top["merged"] = (list[MergedGroup], ...)
        discarded = DiscardedSourceCore if spec.omit_unread else DiscardedSource
        top["discarded"] = (list[discarded], ...)  # type: ignore[valid-type]
    return create_model("RoleOutput", __config__=STRICT_OUTPUT, **top)
