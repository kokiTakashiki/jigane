"""スキルの結果（findings.json）の読み書き。群ごとの古い印もここで持つ。"""

import copy
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, ClassVar, Literal, TypedDict, assert_never

from jigane.findings.discard import (
    CONCERNS_HEADING,
    CONCERNS_INTRO,
    DISCARDED_VIEWPOINT,
    KEY_FROM_CONCERN,
    PLACE_SEPARATOR,
    DiscardKind,
    DiscardState,
    DiscardSummary,
    DisclosedNitReason,
    ExtractedConcern,
    NitReason,
    NoConcern,
    PassedConcern,
    ShardConcerns,
    nit_reason_from_json,
    summary_of_counts,
)
from jigane.groups.definitions import GROUP_IDS, MACRO, MICRO, SECURITY, GroupId
from jigane.storage.review_error import ReviewError
from jigane.storage.store import review_lock, write_text_atomic
from jigane.storage.store_format import Upgrade, upgrade_mapping

# 読み込み口の警告はここへ流す。標準エラーへの出し方は呼ぶ側（CLI の出力層）が handler で決める
LOGGER = logging.getLogger(__name__)

FINDINGS_FILE = "findings.json"
FINDINGS_FORMAT = 3
# 群の結果を差し替えるたびに、差し替えられる側の指摘を足して残す。findings.json の形は変えない
HISTORY_FILE = "findings_history.json"
# skills が書く保存物。群の結果を消す口（reviews）が一覧に入れるため、読む側の層に置く
RUNS_FILE = "runs.json"
IN_PROGRESS_FILE = "run_in_progress.json"


def is_rejected(finding: "FindingBody") -> bool:
    """区分として棄却に置くか。

    裁定が rejected でも、判定役が開示の場所を添えて棄却した指摘（nit_reason の種類が
    開示済みの限界）は棄却に置かず、群の nit の規則に従う。
    """
    adjudication = finding.adjudication
    # 方針の群は検証を行わず adjudication を持たない。棄却されることもない
    if adjudication is None or adjudication.verdict is not Verdict.REJECTED:
        return False
    reason = finding.nit_reason
    return not isinstance(reason, DisclosedNitReason)


class Placement(StrEnum):
    """節の形（置き場所）。値は `show --json` の triages に載せる。"""

    IN_ORDER = "in_order"  # 通常の並び（判断の箇所ごと、またはテーマごと）
    COLLAPSED = "collapsed"  # 既定で畳み、件数だけを示す
    SCOPE_SECTION = "scope_section"  # 範囲外の節。範囲の判定の記録を添える
    DISMISSED_SECTION = "dismissed_section"  # 棄却の節


class DisplayCategory(StrEnum):
    """実装の群で指摘を見せる分類。宣言の順が見出しの並び。

    名前と並びだけを持つ鍵の型。名札、テーマで束ねるか、元の区分は display_info が引く。
    値は `show micro --json` の display_category と display_categories にそのまま載せる。
    """

    MUST_READ = "must_read"
    UNVERIFIED = "unverified"
    REMAINING = "remaining"
    NIT = "nit"
    DISCLOSED_LIMIT = "disclosed_limit"
    OUT_OF_SCOPE = "out_of_scope"
    REJECTED = "rejected"


@dataclass(frozen=True)
class NitReasonIs:
    """nit_reason がこの型の指摘だけを受ける。"""

    reason_type: type


@dataclass(frozen=True)
class Otherwise:
    """区分の指摘のうち、ほかの項目が受けなかったものを受ける。"""


DisplaySplit = NitReasonIs | Otherwise


@dataclass(frozen=True, kw_only=True)
class DisplayEntry:
    """区分が表示で取りうる分類の一つ。"""

    category: DisplayCategory
    split: DisplaySplit = Otherwise()
    # None は区分の名札をそのまま使う。折りたたみの二つだけは区分の名札と別の名札を持つ
    label: str | None = None


@dataclass(frozen=True)
class DisplayInfo:
    """表示の分類の性質。区分の表から作る。"""

    label: str
    themed: bool  # テーマのカードにまとめて見せる分類か
    source: "Triage"  # この分類を持つ区分


@dataclass(frozen=True, kw_only=True)
class TriageTraits:
    """区分ごとの性質。区分を足すときは traits_of に一件足す。"""

    label: str
    # 関門と最初に読むべきは意味が違う。いまは同じ区分を指すが、片方だけ変わりうるので別に持つ
    blocks_gate: bool  # 実装のレビューへ進む関門を塞ぐか
    read_first: bool  # 最初に読むべき区分か。使用量の指標が引く
    groups: tuple[GroupId, ...]  # この区分を付けられる群
    placement: Placement  # 指摘を見せるときの節の形。CLI はこの値で節の形を決める
    counted_as_finding: bool  # 指摘の件数に数えるか。棄却は件数から除き、見出しに別に示す
    # 実装の群の表示の分類。既定を一つ含む。二つ以上は nit_reason の型で分かれる（折りたたみだけ）
    displays: tuple[DisplayEntry, ...]
    # 見出しに件数を添えるか。必読の直後に出す節が、件数で見落としを防ぐ
    heading_with_count: bool = False
    # 範囲外の記録（out_of_scope）を添える区分か。受け付けの検査が区分の名前を比べずに引く
    needs_out_of_scope: bool = False

    def __post_init__(self) -> None:
        if sum(isinstance(entry.split, Otherwise) for entry in self.displays) != 1:
            raise ValueError("displays には Otherwise の項目がちょうど一つ要ります")


class Triage(StrEnum):
    """指摘の区分。宣言の順が並びで、性質は traits が一か所で持つ。

    値は findings.json に保存する文字列そのもの。性質は `triage.traits.label` のように引く。
    """

    MUST_READ = "must_read"
    UNVERIFIED = "unverified"
    REMAINING = "remaining"
    FOLDED = "folded"
    OUT_OF_SCOPE = "out_of_scope"
    REJECTED = "rejected"

    @property
    def traits(self) -> TriageTraits:
        return traits_of(self)


def traits_of(triage: Triage) -> TriageTraits:
    """区分の性質の唯一の定義。match の網羅で、区分を足して書き忘れると mypy が落とす。"""
    match triage:
        case Triage.MUST_READ:
            return TriageTraits(
                label="必読",
                blocks_gate=True,
                read_first=True,
                groups=GROUP_IDS,
                placement=Placement.IN_ORDER,
                counted_as_finding=True,
                displays=(DisplayEntry(category=DisplayCategory.MUST_READ),),
            )
        case Triage.UNVERIFIED:
            return TriageTraits(
                label="未検証",
                blocks_gate=False,
                read_first=False,
                groups=(MICRO, SECURITY),
                placement=Placement.IN_ORDER,
                counted_as_finding=True,
                displays=(DisplayEntry(category=DisplayCategory.UNVERIFIED),),
                heading_with_count=True,
            )
        case Triage.REMAINING:
            return TriageTraits(
                label="残り",
                blocks_gate=False,
                read_first=False,
                groups=GROUP_IDS,
                placement=Placement.IN_ORDER,
                counted_as_finding=True,
                displays=(DisplayEntry(category=DisplayCategory.REMAINING),),
            )
        case Triage.FOLDED:
            return TriageTraits(
                label="折りたたみ",
                blocks_gate=False,
                read_first=False,
                groups=(MICRO,),
                placement=Placement.COLLAPSED,
                counted_as_finding=True,
                displays=(
                    DisplayEntry(category=DisplayCategory.NIT, label="nit"),
                    DisplayEntry(
                        category=DisplayCategory.DISCLOSED_LIMIT,
                        label="開示済みの限界",
                        split=NitReasonIs(DisclosedNitReason),
                    ),
                ),
            )
        case Triage.OUT_OF_SCOPE:
            return TriageTraits(
                label="範囲外",
                blocks_gate=False,
                read_first=False,
                groups=GROUP_IDS,
                placement=Placement.SCOPE_SECTION,
                counted_as_finding=True,
                displays=(DisplayEntry(category=DisplayCategory.OUT_OF_SCOPE),),
                needs_out_of_scope=True,
            )
        case Triage.REJECTED:
            return TriageTraits(
                label="棄却",
                blocks_gate=False,
                read_first=False,
                groups=GROUP_IDS,
                placement=Placement.DISMISSED_SECTION,
                counted_as_finding=False,
                displays=(DisplayEntry(category=DisplayCategory.REJECTED),),
            )
        case _:
            assert_never(triage)


@dataclass(frozen=True, kw_only=True)
class FindingKindTraits:
    """指摘の種類ごとの性質。種類を足すときは kind_traits_of に一件足す。"""

    mark: str | None  # 必読と分けて見せる種類の札。通常の指摘は札を付けないので None
    heading: (
        str | None
    )  # 返信の文面で必読と分けて書く節の見出し。通常の指摘は節を分けないので None
    forced_triage: Triage | None  # 方針の群でこの種類に付ける区分。None なら重大度で決める


class FindingKind(StrEnum):
    """指摘の種類。通常、範囲外、深掘り。判定は kind_of だけが行い、性質は traits が一か所で持つ。

    宣言の順が返信の節の並び。値は show --json の指摘の kind 欄にそのまま出る。
    """

    REGULAR = "regular"
    SCOPE = "scope"
    DEEPENING = "deepening"

    @property
    def traits(self) -> FindingKindTraits:
        return kind_traits_of(self)


def kind_traits_of(kind: FindingKind) -> FindingKindTraits:
    """種類の性質の唯一の定義。match の網羅で、種類を足して書き忘れると mypy が落とす。"""
    match kind:
        case FindingKind.REGULAR:
            return FindingKindTraits(
                mark=None,
                heading=None,
                forced_triage=None,
            )
        case FindingKind.SCOPE:
            return FindingKindTraits(
                mark="範囲外",
                heading="範囲外の懸念",
                forced_triage=Triage.OUT_OF_SCOPE,
            )
        case FindingKind.DEEPENING:
            return FindingKindTraits(
                mark="深掘り",
                heading="前の回の指摘の続き",
                forced_triage=Triage.REMAINING,
            )
        case _:
            assert_never(kind)


def kind_of(*, scope_record: dict[str, Any] | None, continued_from: str | None) -> FindingKind:
    """指摘の種類を決める唯一の置き場。範囲外の記録が深掘りの印より先に効く。"""
    if scope_record is not None:
        return FindingKind.SCOPE
    if continued_from:
        return FindingKind.DEEPENING
    return FindingKind.REGULAR


# 保存済みの古い名前から今の区分への読み替え。実装の指摘を保留にしていた時期の名前は残りとして読む
_LEGACY_TRIAGES = {"deferred": Triage.REMAINING}


def triage_of(value: object) -> Triage | None:
    """JSON の文字列を区分へ変える。区分を持たない値と知らない名前は None。"""
    if not isinstance(value, str) or not value:
        return None
    if value in _LEGACY_TRIAGES:
        return _LEGACY_TRIAGES[value]
    try:
        return Triage(value)
    except ValueError:
        return None


def triage_sort_key(item: "TriagedFinding") -> int:
    """区分の宣言順の位置。"""
    return list(Triage).index(item.decision.triage)


def theme_triage(theme: "StoredTheme", triages: Mapping[str, Triage | None]) -> Triage | None:
    """テーマの区分の唯一の規則。所属する指摘の区分から導き、テーマには写しを持たせない。

    同じテーマの指摘は、位置による付与では同じ区分になる。検証の規則で一部だけ残りへ下がった
    ときは、読むべき側（宣言の順で先の区分）を代表にする。テーマが必読を隠さないため。
    指摘の区分が一つも読めないテーマは None。
    """
    readable = [
        triage
        for finding_id in theme.finding_ids
        if (triage := triages.get(finding_id)) is not None
    ]
    return min(readable, key=list(Triage).index, default=None)


def group_by_triage(
    items: "Sequence[TriagedFinding]",
) -> "list[tuple[Triage, list[TriagedFinding]]]":
    """区分の宣言順で、項目のある区分とその項目を返す。"""
    return [
        (triage, members)
        for triage in Triage
        if (members := [item for item in items if item.decision.triage is triage])
    ]


def count_by_triage(items: "Sequence[TriagedFinding]") -> dict[Triage, int]:
    """区分ごとの件数。項目の無い区分も 0 で持つ。"""
    counts = dict.fromkeys(Triage, 0)
    for item in items:
        counts[item.decision.triage] += 1
    return counts


def count_heading(title: str, items: "Sequence[TriagedFinding]") -> str:
    """群の見出しの件数。棄却など件数から除く区分は、本数に含めず括弧に別に示す。"""
    counted = sum(1 for item in items if item.decision.triage.traits.counted_as_finding)
    return f"{title}: {counted} 件（棄却 {len(items) - counted} 件）"


MACHINE_SCOPE_REASON = "根拠の位置が、レビューの範囲のファイルに一つも入りません"


@dataclass(frozen=True)
class ConcludedInScope:
    """結論の役が、範囲内と判断した。"""


@dataclass(frozen=True)
class ConcludedOutOfScope:
    """結論の役が、範囲外と判断した。reason は範囲外の理由。"""

    reason: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("範囲外の判断には理由が要ります")


@dataclass(frozen=True)
class NoConclusion:
    """結論の役が判断を返さなかった。"""


ScopeConclusion = ConcludedInScope | ConcludedOutOfScope | NoConclusion


def judge_scope(
    evidence_paths: list[str],
    scope: set[str],
    conclusion: ScopeConclusion,
) -> dict[str, Any] | None:
    """指摘が範囲外かの判定の唯一の置き場。範囲外なら保存する値を、範囲内なら None を返す。

    保存する値は paths（根拠のうち範囲の外のパス）と reason。CLI はこれを引くだけにする。
    """
    unique = sorted(set(evidence_paths))
    outside = [path for path in unique if path not in scope]
    match conclusion:
        case ConcludedInScope():
            return None
        case ConcludedOutOfScope(reason):
            return {"paths": outside, "reason": reason}
        case NoConclusion():
            # 機械の判断は、
            # 結論の役が判断を返さなかったとき（失敗して重複のまとめだけに戻ったときなど）
            # にだけ使う
            if outside and len(outside) == len(unique):
                return {"paths": outside, "reason": MACHINE_SCOPE_REASON}
            return None
        case _:
            assert_never(conclusion)


# 実装の群で必読にするテーマの数。テーマの並びの先頭からこの数だけを必読にし、残りは残りにする
MUST_READ_THEMES = 3


class TriageReason(StrEnum):
    """規則で必読から残りへ下げた理由。値は triage_reason に保存する文字列そのもの。"""

    WEAKENED_UNVERIFIED = "weakened_unverified"
    TOOL_GUESS_UNFOUNDED = "tool_guess_unfounded"


@dataclass(frozen=True)
class UnknownTriageReason:
    """保存物にあった、知らない理由の名前。書き戻しで元の文字列を失わないために生のまま持つ。

    規則の判定には使わない。TriageReason の値ではないので、型が区別する。
    """

    raw: str


def triage_reason_of(value: object) -> TriageReason | UnknownTriageReason | None:
    """JSON の文字列を下げた理由へ変える。知らない名前は生の文字列を保つ。他の値は None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return TriageReason(value)
    except ValueError:
        return UnknownTriageReason(value)


class ToolBasis(StrEnum):
    """判定役が示す、外の道具の挙動の推測の根拠。値は basis に保存する文字列そのもの。"""

    NONE = "none"
    DOCS = "docs"
    RUN = "run"

    @property
    def founded(self) -> bool:
        """文書か実行で確かめた根拠か。"""
        return self is not ToolBasis.NONE


class Verdict(StrEnum):
    """判定役の判定の値。保存する文字列そのもの。"""

    KEPT = "kept"
    WEAKENED = "weakened"
    REJECTED = "rejected"


class AdjudicationKind(StrEnum):
    """裁定の形。反証は確かめる作業、代案は優劣の判定。"""

    REBUTTAL = "rebuttal"
    ALTERNATIVE = "alternative"


@dataclass(frozen=True)
class ToolClaim:
    """判定役が示す、外の道具の挙動への依拠。"""

    relies: bool
    basis: ToolBasis
    version: str

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "ToolClaim":
        return cls(relies=raw["relies"], basis=ToolBasis(raw["basis"]), version=raw["version"])

    def to_json(self) -> dict[str, Any]:
        return {"relies": self.relies, "basis": self.basis.value, "version": self.version}

    @property
    def unfounded(self) -> bool:
        """道具に依拠しているのに、根拠か版が無い。"""
        return self.relies and not (self.basis.founded and bool(self.version.strip()))


@dataclass(frozen=True, kw_only=True)
class _AdjudicationBase:
    """種類ごとの裁定の共通部。全種類で意味の通る項目だけを置き、種類の型がその種類の欄を足す。

    型にしない鍵は extra に生で保ち、書き戻しで消さない。
    """

    kind: ClassVar[AdjudicationKind]

    verdict: Verdict
    reason: str
    extra: Mapping[str, Any] = field(default_factory=dict)

    def kind_fields(self) -> dict[str, Any]:
        raise NotImplementedError

    def trailing_fields(self) -> dict[str, Any]:
        """reason の後ろに書く、種類の型だけが持つ欄。"""
        return {}

    def to_json(self) -> dict[str, Any]:
        # 鍵の並びは判定役の出力と保存物の並び。書き戻しのたびに並びが変わらないようにする。
        # 種類の欄は kind の直後、verdict の前に入れる
        ordered: dict[str, Any] = {
            "kind": self.kind.value,
            **self.kind_fields(),
            "verdict": self.verdict.value,
            "reason": self.reason,
            **self.trailing_fields(),
        }
        return {**self.extra, **ordered}


class Verification(StrEnum):
    """反証が、主張を確かめたか。保存物では verified の真偽（欄が無ければ記録なし）で持つ。"""

    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    NO_RECORD = "no_record"  # 古い保存物など、判定役が欄を返さなかった


def read_verification(raw: object) -> Verification:
    """保存物の verified 欄（欠落は None で渡す）を読む。真偽以外は ValueError。"""
    match raw:
        case None:
            return Verification.NO_RECORD
        case True:
            return Verification.VERIFIED
        case False:
            return Verification.NOT_VERIFIED
        case _:
            raise ValueError(f"verified が真偽値ではない: {raw!r}")


@dataclass(frozen=True, kw_only=True)
class Rebuttal(_AdjudicationBase):
    """反証。確かめる作業で、再確認の位置を持つ。

    verified と tool_claim は降格の規則が読む欄で、反証だけが持つ。古い保存物には無い。
    """

    kind: ClassVar[AdjudicationKind] = AdjudicationKind.REBUTTAL

    challenge: str
    recheck: tuple[Mapping[str, Any], ...]
    verified: Verification = Verification.NO_RECORD
    tool_claim: ToolClaim | None = None

    def kind_fields(self) -> dict[str, Any]:
        return {"challenge": self.challenge, "recheck": list(self.recheck)}

    def trailing_fields(self) -> dict[str, Any]:
        trailing: dict[str, Any] = {}
        match self.verified:
            case Verification.VERIFIED:
                trailing["verified"] = True
            case Verification.NOT_VERIFIED:
                trailing["verified"] = False
            case Verification.NO_RECORD:
                pass  # 記録が無いときは欄を書かず、古い保存物と同じ形に往復させる
            case _:
                assert_never(self.verified)
        if self.tool_claim is not None:
            trailing["tool_claim"] = self.tool_claim.to_json()
        return trailing


@dataclass(frozen=True, kw_only=True)
class Alternative(_AdjudicationBase):
    """代案。優劣の判定で、確かめる作業ではない。"""

    kind: ClassVar[AdjudicationKind] = AdjudicationKind.ALTERNATIVE

    alternative: str
    defense: str

    def kind_fields(self) -> dict[str, Any]:
        return {"alternative": self.alternative, "defense": self.defense}


type Adjudication = Rebuttal | Alternative


@dataclass(frozen=True)
class UnreadableAdjudication:
    """裁定として読めなかったこと。欄そのものが無い場合（KeyError）は含まない。"""

    reason: str


def read_adjudication(raw: Mapping[str, Any]) -> Adjudication | UnreadableAdjudication:
    """保存物の裁定を読む。過去の版の記録も読むので、値が読めなくても落とさず理由を返す。

    モデルの出力は pipeline.adjudication_from_model_output が裁定のモデルで検証して作る。
    必須の欄が無いときは KeyError を送出する。値が読めないときは理由を返す。
    """
    data = dict(raw)
    try:
        kind = AdjudicationKind(data.pop("kind"))
        common: dict[str, Any] = {
            "verdict": Verdict(data.pop("verdict")),
            "reason": data.pop("reason"),
        }
        match kind:
            case AdjudicationKind.REBUTTAL:
                claim = data.pop("tool_claim", None)
                return Rebuttal(
                    **common,
                    challenge=data.pop("challenge"),
                    recheck=tuple(data.pop("recheck")),
                    verified=read_verification(data.pop("verified", None)),
                    tool_claim=None if claim is None else ToolClaim.from_json(claim),
                    extra=data,
                )
            case AdjudicationKind.ALTERNATIVE:
                # verified と tool_claim は降格の規則が使わないので型にせず、extra に残す
                return Alternative(
                    **common,
                    alternative=data.pop("alternative"),
                    defense=data.pop("defense"),
                    extra=data,
                )
            case _:
                assert_never(kind)
    except (ValueError, TypeError) as error:
        # 値の型が違う入力（recheck が null など）は TypeError で落ちる。読めない理由として返す
        return UnreadableAdjudication(str(error))


@dataclass(frozen=True, kw_only=True)
class _DecisionBase:
    """区分ごとの決定の共通部。区分ごとの変種だけが、その区分で持てる項目を足す。"""

    triage: ClassVar[Triage]
    # 各変種が持つ reason は triage_reason 欄へ写す値で、その区分に入れてよい理由の型だけを持つ。
    # 知らない理由は保存物の写しなので、どの区分とも組める


@dataclass(frozen=True, kw_only=True)
class MustReadDecision(_DecisionBase):
    triage: ClassVar[Triage] = Triage.MUST_READ
    reason: UnknownTriageReason | None = None


@dataclass(frozen=True, kw_only=True)
class UnverifiedDecision(_DecisionBase):
    """検証の分割が失敗して判定の付かなかった候補。必読に数えず、ゲートを止めない。"""

    triage: ClassVar[Triage] = Triage.UNVERIFIED
    reason: UnknownTriageReason | None = None


@dataclass(frozen=True, kw_only=True)
class FoldedDecision(_DecisionBase):
    triage: ClassVar[Triage] = Triage.FOLDED
    reason: UnknownTriageReason | None = None


@dataclass(frozen=True, kw_only=True)
class RejectedDecision(_DecisionBase):
    triage: ClassVar[Triage] = Triage.REJECTED
    reason: UnknownTriageReason | None = None


@dataclass(frozen=True, kw_only=True)
class OutOfScopeDecision(_DecisionBase):
    """範囲外。範囲の判定の記録を必ず持つ。"""

    triage: ClassVar[Triage] = Triage.OUT_OF_SCOPE
    out_of_scope: dict[str, Any]
    reason: UnknownTriageReason | None = None


@dataclass(frozen=True, kw_only=True)
class RemainingDecision(_DecisionBase):
    """残り。規則で必読から下げたときだけ、その理由を持つ。"""

    triage: ClassVar[Triage] = Triage.REMAINING
    # 規則が付ける理由（TriageReason）は残りの区分とだけ組になる
    reason: TriageReason | UnknownTriageReason | None = None


TriageDecision = (
    MustReadDecision
    | UnverifiedDecision
    | RemainingDecision
    | FoldedDecision
    | OutOfScopeDecision
    | RejectedDecision
)


def restored_decision(
    triage: Triage,
    record: dict[str, Any] | None,
    reason: TriageReason | UnknownTriageReason | None,
) -> TriageDecision:
    """保存物の区分、範囲外の記録、理由から決定を組む。区分と合わない記録と理由は落とす。

    規則が付ける理由は残りの区分とだけ組になるので、他の区分では捨てる。
    """
    unknown = reason if isinstance(reason, UnknownTriageReason) else None
    match triage:
        case Triage.MUST_READ:
            return MustReadDecision(reason=unknown)
        case Triage.REMAINING:
            return RemainingDecision(reason=reason)
        case Triage.UNVERIFIED:
            return UnverifiedDecision(reason=unknown)
        case Triage.FOLDED:
            return FoldedDecision(reason=unknown)
        case Triage.REJECTED:
            return RejectedDecision(reason=unknown)
        case Triage.OUT_OF_SCOPE:
            if record is None:
                raise ValueError("範囲外の区分は範囲の判定の記録が要ります")
            return OutOfScopeDecision(out_of_scope=record, reason=unknown)
        case _:
            assert_never(triage)


def decide_triage(
    group: GroupId,
    finding: "StoredFinding",
    *,
    theme_position: int | None = None,
) -> TriageDecision:
    """指摘の区分を決める置き場。引く口は triaged と fill_triage だけ。

    棄却は全群で最優先。範囲外の記録（指摘の out_of_scope。judge_scope の結果で、範囲内なら
    持たない）が次に効き、方針の群ではその次に深掘りの印（continued_from）が効く。
    深掘りと範囲外は必読にならない（種類の性質 forced_triage）。
    棄却と範囲外のどちらが勝つかはここだけが決める。
    実装の群の nit はテーマへ束ねて折りたたむ。実装のそれ以外は、指摘が属するテーマの並びの位置
    （theme_position）で必読か残りになる。方針と安全性の群は nit が残り、それ以外が必読。

    補いはテーマの位置は区分を持たない時期の保存物に無く再現できない。そのため補いは、
    実装の群の nit 以外について、位置による残りへの振り分けを行わず、必読を既定にする。
    下げる規則（_downgrade_unfounded）は補いでも実行時と同じに効くので、セキュリティと実装の
    指摘は、規則に当たれば残りに下がる。位置を再現できない分だけ、補いは関門を塞ぐ側に誤る。
    """
    if isinstance(finding.decision, UnverifiedDecision):
        # 検証の分割が失敗した候補は、保存のときに区分を付けて持ち、重さによらず未検証に置く。
        # 検証されていないので、棄却の裁定も範囲の判定も持たない
        return finding.decision
    body = finding.body
    if is_rejected(body):
        return RejectedDecision()
    forced = body.kind.traits.forced_triage
    if forced is not None and (group == MACRO or forced is Triage.OUT_OF_SCOPE):
        # 範囲外と深掘りは必読に数えない。区分は種類の性質が持つ。範囲外の種類は記録を必ず持つ。
        # 深掘りは方針の群だけの種類で、他の群は範囲外だけがこの経路に入る
        record = body.out_of_scope if isinstance(body, ScopeBody) else None
        return restored_decision(forced, record, None)
    is_nit = body.severity == "nit"
    if group == MICRO:
        return _micro_triage(finding, is_nit=is_nit, theme_position=theme_position)
    return _non_micro_triage(group, finding, is_nit=is_nit)


def _micro_triage(
    finding: "StoredFinding", *, is_nit: bool, theme_position: int | None
) -> TriageDecision:
    if is_nit:
        return FoldedDecision()
    if theme_position is not None and theme_position >= MUST_READ_THEMES:
        return RemainingDecision()
    return _downgrade_unfounded(finding) or MustReadDecision()


def _non_micro_triage(group: GroupId, finding: "StoredFinding", *, is_nit: bool) -> TriageDecision:
    if is_nit:
        return RemainingDecision()
    if group == SECURITY:
        return _downgrade_unfounded(finding) or MustReadDecision()
    return MustReadDecision()


def _downgrade_unfounded(finding: "StoredFinding") -> TriageDecision | None:
    """必読から残りへ下げる二つの規則。当たらなければ None。

    判定役の出力の明示の項目だけを読み、判定の文は読まない。項目を持たない古い保存物は
    どちらの規則にも当たらず、読み方は変わらない（検証を行わない群も同じ）。
    """
    adjudication = finding.body.adjudication
    if not isinstance(adjudication, Rebuttal):
        # 代案の判定は確かめる作業ではなく優劣の判定なので、どちらの項目も持たない
        return None
    weakened = adjudication.verdict is Verdict.WEAKENED
    if weakened and adjudication.verified is Verification.NOT_VERIFIED:
        return RemainingDecision(reason=TriageReason.WEAKENED_UNVERIFIED)
    if adjudication.tool_claim is not None and adjudication.tool_claim.unfounded:
        return RemainingDecision(reason=TriageReason.TOOL_GUESS_UNFOUNDED)
    return None


def triaged(
    group: GroupId,
    finding: "StoredFinding",
    *,
    theme_position: int | None = None,
) -> "TriagedFinding":
    """実行時の付与の唯一の入口。型の指摘を一つ受け、区分と理由の確定した指摘を返す。"""
    decision = decide_triage(group, finding, theme_position=theme_position)
    return TriagedFinding(finding.body, decision)


def fill_triage(group: GroupId, finding: "StoredFinding") -> "TriagedFinding":
    """保存された指摘を、区分の確定した指摘にする。区分を持たない古い指摘は、位置を使えない形で補う。"""
    if finding.decision is None:
        return triaged(group, finding)
    return TriagedFinding(finding.body, finding.decision)


# findings.json と findings_history.json の鍵。読み書きは各型の from_json と to_json だけが行う
_KEY_FORMAT = "format"
_KEY_LAST_NUMBER = "last_number"
_KEY_LAST_THEME_NUMBER = "last_theme_number"
_KEY_THEMES = "themes"
_KEY_STALE_GROUPS = "stale_groups"
_KEY_GROUPS = "groups"
_KEY_RAN_AT = "ran_at"
_KEY_FINDINGS = "findings"
_KEY_DISCARD = "discard"
_KEY_DISCOVERED = "discovered"
_KEY_DISCARDED = "discarded"


_KEY_ID = "id"
_KEY_SEVERITY = "severity"
_KEY_VIEWPOINT = "viewpoint"
_KEY_CLAIM = "claim"
_KEY_TRIAGE = "triage"
_KEY_THEME = "theme"
_KEY_ADJUDICATION = "adjudication"
_KEY_NIT_REASON = "nit_reason"
_KEY_TRIAGE_REASON = "triage_reason"
_KEY_DEEPENS = "deepens"
_KEY_OUT_OF_SCOPE = "out_of_scope"

_KEY_TITLE = "title"
_KEY_FINDING_IDS = "finding_ids"


@dataclass(frozen=True, kw_only=True)
class StoredTheme:
    """保存された一つのテーマ。実装の群の指摘のまとまりで、番号は B。

    triage は互換のために書き続ける写しで、読む側は load_findings_state が指摘から導いた値を見る。
    extra は型にしていない欄で、書き戻しで消さない。
    """

    id: str
    title: str
    finding_ids: tuple[str, ...]
    severity: str
    triage: Triage | None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict[str, Any], where: str = "") -> "StoredTheme":
        """保存された一件を読む。区分の旧い名前の読み替えはここで一度だけ行う。渡された辞書は書き換えない。

        今の版の形だけを読む。欠けた鍵の補いは read_findings_json が呼ぶ版ごとの移しが行う。
        """
        data = copy.deepcopy(raw)
        raw_triage = data.pop(_KEY_TRIAGE)
        triage = triage_of(raw_triage)
        if triage is None and raw_triage:
            LOGGER.warning(
                "%s/%s の区分 %r は知らない名前なので、区分なしとして読みます",
                where,
                data.get(_KEY_ID),
                raw_triage,
            )
        return cls(
            id=data.pop(_KEY_ID),
            title=data.pop(_KEY_TITLE),
            finding_ids=tuple(data.pop(_KEY_FINDING_IDS)),
            severity=data.pop(_KEY_SEVERITY),
            triage=triage,
            extra=data,
        )

    def to_json(self) -> dict[str, Any]:
        """現行の形だけを書く。旧形式の読み替えは from_json だけが行う。"""
        return {
            **self.extra,
            _KEY_ID: self.id,
            _KEY_TITLE: self.title,
            _KEY_FINDING_IDS: list(self.finding_ids),
            _KEY_SEVERITY: self.severity,
            _KEY_TRIAGE: self.triage,
        }


def _adjudication_from(raw: Mapping[str, Any] | None) -> Adjudication | None:
    if raw is None:
        return None
    read = read_adjudication(raw)
    if isinstance(read, UnreadableAdjudication):
        raise ValueError(read.reason)  # noqa: TRY004 型の検査ではなく、読めなかった記録の理由を ValueError で渡す
    return read


@dataclass(frozen=True, kw_only=True)
class FindingBody:
    """指摘の本体。区分と理由（triage、triage_reason）は持たない。確定後の指摘（TriagedFinding）が包む型。

    区分と理由の出所は決定（TriageDecision）だけで、本体の側から読む道は型で持たない。
    保存物を読み書きする StoredFinding は、この本体と保存の決定を組にした型である。

    論理が読む欄だけを型にし、残りは extra に生のまま保つ。種類は保存物に欄を持たず、
    out_of_scope と continued_from の有無から from_json が一度だけ決め、型として持つ。
    継続元（continued_from）は種類ごとに持ち方が違う。通常の指摘は持てず、範囲外は任意、
    深掘りは必須で、それぞれの型の欄で表す。読む側は continued_from_value() を使う。
    保存の鍵名 deepens は from_json と json_with の内側に閉じる。
    裁定（adjudication）は型（Adjudication）で持つ。
    入れ子の out_of_scope は、段 4b まで辞書のまま持つ。
    nit_reason は種類と直交する（捨てた候補から写した nit は通常の指摘でも持ちうる）ので
    共通に置く。
    extra は証拠の位置や参照など群ごとに違う欄で、書き戻しで消さない。
    """

    kind: ClassVar[FindingKind]

    id: str
    severity: str
    viewpoint: str
    claim: str
    theme: str | None
    adjudication: Adjudication | None
    nit_reason: NitReason | None
    # 実装の指摘がどの方針の指摘の懸念から来たか（方針の F 番号）。無ければ懸念から来ていない
    from_concern: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def kind_fields(self) -> dict[str, Any]:
        """種類の型だけが持つ欄。値が無い欄は書かない。"""
        raise NotImplementedError

    def continued_from_value(self) -> str | None:
        """継続元（深掘り元の指摘の番号）。既定は持たない。持てる型が上書きする。"""
        return None

    def json_with(self, decision: TriageDecision | None) -> dict[str, Any]:
        """現行の形だけを書く。旧形式の移しは境が行う。区分と理由は決定から取る。"""
        triage = None if decision is None else decision.triage
        triage_reason = None if decision is None else decision.reason
        raw: dict[str, Any] = {
            **self.extra,
            _KEY_ID: self.id,
            _KEY_SEVERITY: self.severity,
            _KEY_VIEWPOINT: self.viewpoint,
            _KEY_CLAIM: self.claim,
            _KEY_TRIAGE: triage,
            _KEY_THEME: self.theme,
            _KEY_ADJUDICATION: None if self.adjudication is None else self.adjudication.to_json(),
        }
        # 任意の欄は、無いことが「持たない」を表す。null で書くとスキーマ（deepens の型）に反する
        if self.nit_reason is not None:
            raw[_KEY_NIT_REASON] = self.nit_reason.to_json()
        if triage_reason is not None:
            raw[_KEY_TRIAGE_REASON] = (
                triage_reason.raw
                if isinstance(triage_reason, UnknownTriageReason)
                else triage_reason
            )
        continued_from = self.continued_from_value()
        if continued_from is not None:
            raw[_KEY_DEEPENS] = continued_from
        if self.from_concern is not None:
            raw[KEY_FROM_CONCERN] = self.from_concern
        raw.update(self.kind_fields())
        return raw


@dataclass(frozen=True, kw_only=True)
class RegularBody(FindingBody):
    kind: ClassVar[FindingKind] = FindingKind.REGULAR

    def kind_fields(self) -> dict[str, Any]:
        return {}


@dataclass(frozen=True, kw_only=True)
class ScopeBody(FindingBody):
    """範囲外の指摘の本体。範囲外の記録を必ず持つ。深掘りの印は、範囲外と重なったときだけ持つ。"""

    kind: ClassVar[FindingKind] = FindingKind.SCOPE
    out_of_scope: dict[str, Any]
    continued_from: str | None = None

    def kind_fields(self) -> dict[str, Any]:
        return {_KEY_OUT_OF_SCOPE: self.out_of_scope}

    def continued_from_value(self) -> str | None:
        return self.continued_from


@dataclass(frozen=True, kw_only=True)
class DeepeningBody(FindingBody):
    """前の回の指摘の続きの本体。深掘り元の指摘の番号を必ず持つ。"""

    kind: ClassVar[FindingKind] = FindingKind.DEEPENING
    continued_from: str

    def kind_fields(self) -> dict[str, Any]:
        return {}

    def continued_from_value(self) -> str | None:
        return self.continued_from


@dataclass(frozen=True)
class StoredFinding:
    """保存された一件の指摘。本体と、保存物が持つ決定を組にする。読む口は from_json。

    決定が None なのは、区分を持たない古い保存物である。確定後の読み手は、この値でなく
    TriagedFinding の決定を読む。
    """

    body: FindingBody
    decision: TriageDecision | None

    @classmethod
    def from_json(
        cls,
        raw: dict[str, Any],
        where: str = "",
    ) -> "StoredFinding":
        """保存物の一件を読む。裁定も raw の adjudication から読む。渡された辞書は書き換えない。

        今の版の形だけを読む。古い形は read_findings_json が呼ぶ版ごとの移しが先に直す。
        """
        data = copy.deepcopy(raw)
        adjudication = _adjudication_from(data.pop(_KEY_ADJUDICATION))
        return cls._read(data, adjudication, where)

    @classmethod
    def of_model_output(
        cls,
        raw_without_adjudication: dict[str, Any],
        adjudication: Adjudication | None,
        where: str = "",
    ) -> "StoredFinding":
        """役の出力から作った一件を、読み済みの裁定と合わせて読む。"""
        if _KEY_ADJUDICATION in raw_without_adjudication:
            raise ValueError("adjudication は raw でなく、読み済みの型で渡します")
        return cls._read(copy.deepcopy(raw_without_adjudication), adjudication, where)

    @classmethod
    def _read(
        cls,
        data: dict[str, Any],
        adjudication: Adjudication | None,
        where: str,
    ) -> "StoredFinding":
        """種類の決定はここで一度だけ行う。data は書き換える。"""
        raw_triage = data.pop(_KEY_TRIAGE)
        triage = triage_of(raw_triage)
        if triage is None and raw_triage:
            LOGGER.warning(
                "%s/%s の区分 %r は知らない名前なので、区分なしとして読みます",
                where,
                data.get(_KEY_ID),
                raw_triage,
            )
        raw_triage_reason = data.pop(_KEY_TRIAGE_REASON, None)
        triage_reason = triage_reason_of(raw_triage_reason)
        if triage_reason is None and raw_triage_reason:
            # 文字列でない値はスキーマに反する。保てる形が無いので理由なしで読む
            LOGGER.warning(
                "%s/%s の下げた理由 %r は文字列でないので、理由なしとして読みます",
                where,
                data.get(_KEY_ID),
                raw_triage_reason,
            )
        raw_nit_reason = data.pop(_KEY_NIT_REASON, None)
        nit_reason = None if raw_nit_reason is None else nit_reason_from_json(raw_nit_reason)
        theme = data.pop(_KEY_THEME)
        out_of_scope = data.pop(_KEY_OUT_OF_SCOPE, None)
        # 空文字の deepens はスキーマ（F の番号）に反するので、持たない扱いで落とす
        continued_from = data.pop(_KEY_DEEPENS, None) or None
        # 空文字の from_concern もスキーマ（F の番号）に反するので、持たない扱いで落とす
        from_concern = data.pop(KEY_FROM_CONCERN, None) or None
        kind = kind_of(scope_record=out_of_scope, continued_from=continued_from)
        common: dict[str, Any] = {
            "id": data.pop(_KEY_ID),
            "severity": data.pop(_KEY_SEVERITY),
            "viewpoint": data.pop(_KEY_VIEWPOINT),
            "claim": data.pop(_KEY_CLAIM),
            "theme": theme,
            "adjudication": adjudication,
            "nit_reason": nit_reason,
            "from_concern": from_concern,
            "extra": data,
        }
        body: FindingBody
        match kind:
            case FindingKind.SCOPE:
                # 範囲外が深掘りより先に効く。前の回の続きでもある指摘は deepens も残す
                body = ScopeBody(
                    **common, out_of_scope=out_of_scope, continued_from=continued_from
                )
            case FindingKind.DEEPENING:
                # kind_of が継続元の真偽で決めた種類なので来ないが、型の絞り込みに要る
                if continued_from is None:
                    raise ValueError("継続元のない深掘りです")
                body = DeepeningBody(**common, continued_from=continued_from)
            case FindingKind.REGULAR:
                body = RegularBody(**common)
            case _:
                assert_never(kind)
        # 範囲外の区分は記録と一緒にしか成り立たない。記録の無い保存物は保存の写しを信じず、
        # 区分なしとして読んで決め直す
        if triage is Triage.OUT_OF_SCOPE and not isinstance(body, ScopeBody):
            triage = None
        if triage is None:
            return cls(body, None)
        record = body.out_of_scope if isinstance(body, ScopeBody) else None
        return cls(body, restored_decision(triage, record, triage_reason))

    @property
    def triage(self) -> Triage | None:
        return None if self.decision is None else self.decision.triage

    def with_theme(self, theme: str | None) -> "StoredFinding":
        return StoredFinding(replace(self.body, theme=theme), self.decision)

    def to_json(self) -> dict[str, Any]:
        return self.body.json_with(self.decision)


@dataclass(frozen=True)
class TriagedFinding:
    """区分の確定した指摘。本体と、その区分と理由の決定を組にする。

    区分と理由の出所は決定だけで、本体はどちらの欄も持たない。
    """

    finding: FindingBody
    decision: TriageDecision

    def to_json(self) -> dict[str, Any]:
        return self.finding.json_with(self.decision)

    def stored(self) -> StoredFinding:
        return StoredFinding(self.finding, self.decision)


@cache
def _display_infos() -> Mapping[DisplayCategory, DisplayInfo]:
    """区分の表を走査して作る、分類から性質への索引。"""
    infos: dict[DisplayCategory, DisplayInfo] = {}
    for triage in Triage:
        traits = triage.traits
        for entry in traits.displays:
            infos[entry.category] = DisplayInfo(
                label=traits.label if entry.label is None else entry.label,
                themed=traits.placement is Placement.IN_ORDER,
                source=triage,
            )
    return infos


def display_info(category: DisplayCategory) -> DisplayInfo:
    """分類の名札、テーマで束ねるか、元の区分。"""
    return _display_infos()[category]


def display_category_of(finding: TriagedFinding) -> DisplayCategory:
    """表示の分類の唯一の導出。区分の displays から引く。条件に外れた指摘は Otherwise が受ける。"""
    displays = finding.decision.triage.traits.displays
    reason = finding.finding.nit_reason
    for entry in displays:
        match entry.split:
            case NitReasonIs(reason_type=reason_type):
                if isinstance(reason, reason_type):
                    return entry.category
            case Otherwise():
                # 後で受ける。条件の項目が全て外れたときに、次の行が拾う
                pass
            case _:
                assert_never(entry.split)
    return next(entry.category for entry in displays if isinstance(entry.split, Otherwise))


def group_by_display_category(
    items: Sequence[TriagedFinding],
) -> list[tuple[DisplayCategory, list[TriagedFinding]]]:
    """分類の宣言順で、項目のある分類とその項目を返す。"""
    return [
        (category, members)
        for category in DisplayCategory
        if (members := [item for item in items if display_category_of(item) is category])
    ]


def finding_dicts(findings: Iterable[StoredFinding]) -> list[dict[str, Any]]:
    """保存された指摘を辞書にする口。"""
    return [finding.to_json() for finding in findings]


def triaged_dicts(findings: Iterable[TriagedFinding]) -> list[dict[str, Any]]:
    """確定後の指摘を辞書にする口。"""
    return [finding.to_json() for finding in findings]


@dataclass(frozen=True)
class ThemeUpdate:
    """テーマの一覧と採番の最終値。この二つは一緒に変わるので一つの値で運ぶ。"""

    themes: tuple[StoredTheme, ...]
    last_theme_number: int


@dataclass(frozen=True)
class GroupResult:
    """一つの群の実行が保存する結果。theme_update はテーマを持つ群だけが持ち、他は None。"""

    findings: tuple[TriagedFinding, ...]
    theme_update: ThemeUpdate | None = None


@dataclass(frozen=True)
class StoredFindings:
    """findings.json の全体。指摘は StoredFinding、テーマの中身は辞書のまま持つ。

    区分は Triage に揃えて持つ。区分を補った読む側の状態は FilledFindings で、この型とは別。
    """

    last_number: int
    last_theme_number: int
    groups: dict[GroupId, tuple[StoredFinding, ...]]
    themes: tuple[StoredTheme, ...]
    stale_groups: tuple[GroupId, ...]
    # 現行の群に無い名前の群。型には載せず、書き戻しで消さないよう生の値のまま保つ
    unknown_groups: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "StoredFindings":
        return cls(0, 0, {}, (), ())

    @classmethod
    def from_json(cls, raw: dict[str, Any], where: str = "") -> "StoredFindings":
        """今の版の形の全体を読む。渡された辞書は書き換えない。

        版の判別と古い形の移しは read_findings_json が先に行う。
        """
        data = copy.deepcopy(raw)
        themes = tuple(StoredTheme.from_json(t, where) for t in data[_KEY_THEMES])
        groups: dict[GroupId, tuple[StoredFinding, ...]] = {}
        unknown_groups: dict[str, Any] = {}
        for name, findings in data[_KEY_GROUPS].items():
            try:
                group = GroupId(name)
            except ValueError:
                LOGGER.warning("%s の群 %r は現行に無い名前なので、読まずに保ちます", where, name)
                unknown_groups[name] = findings
                continue
            groups[group] = tuple(StoredFinding.from_json(f, where) for f in findings)
        return cls(
            last_number=data[_KEY_LAST_NUMBER],
            last_theme_number=data[_KEY_LAST_THEME_NUMBER],
            groups=groups,
            themes=themes,
            stale_groups=tuple(GroupId(name) for name in data[_KEY_STALE_GROUPS]),
            unknown_groups=unknown_groups,
        )

    def to_json(self) -> dict[str, Any]:
        """現行の形だけを書く。旧形式の移行は read_findings_json が行う。"""
        return {
            _KEY_FORMAT: FINDINGS_FORMAT,
            _KEY_LAST_NUMBER: self.last_number,
            _KEY_GROUPS: {
                **{name: finding_dicts(findings) for name, findings in self.groups.items()},
                **self.unknown_groups,
            },
            _KEY_STALE_GROUPS: list(self.stale_groups),
            _KEY_LAST_THEME_NUMBER: self.last_theme_number,
            _KEY_THEMES: [theme.to_json() for theme in self.themes],
        }

    def with_reserved(self, count: int) -> "StoredFindings":
        return replace(self, last_number=self.last_number + count)

    def with_result(self, group: GroupId, result: GroupResult) -> "StoredFindings":
        """この群の結果だけを差し替える。他の群には触れない。"""
        groups = {**self.groups, group: tuple(f.stored() for f in result.findings)}
        stale = tuple(name for name in self.stale_groups if name != group)
        update = result.theme_update
        if update is None:
            return replace(self, groups=groups, stale_groups=stale)
        # テーマは実装の群のものだけで、番号は実装の実行が進める
        return replace(
            self,
            groups=groups,
            stale_groups=stale,
            themes=update.themes,
            last_theme_number=max(self.last_theme_number, update.last_theme_number),
        )

    def without_group(self, group: GroupId) -> "StoredFindings":
        groups = {name: findings for name, findings in self.groups.items() if name != group}
        return replace(
            self, groups=groups, stale_groups=tuple(n for n in self.stale_groups if n != group)
        )

    def marked_stale(self) -> "StoredFindings":
        return replace(self, stale_groups=tuple(self.groups))


@dataclass(frozen=True)
class FilledFindings:
    """保存に無い区分を補った、読む側のための状態。保存の口へは渡せない。

    どの指摘も区分を持ち（TriagedFinding）、テーマの triage は指摘から導いた値。
    現行に無い群は持たない。
    """

    last_number: int
    last_theme_number: int
    groups: dict[GroupId, tuple[TriagedFinding, ...]]
    themes: tuple[StoredTheme, ...]
    stale_groups: tuple[GroupId, ...]


def read_stored_state(directory: Path) -> StoredFindings:
    """findings.json を型で読む。書き手が読み直して保存するときの入口。"""
    path = directory / FINDINGS_FILE
    if not path.is_file():
        return StoredFindings.empty()
    return read_findings_json(json.loads(path.read_text(encoding="utf-8")), directory.name)


def _stored_file_name(where: str, file: str) -> str:
    """版の判定の誤り文に添える保存物の名。where が空なら file だけを返す。"""
    return f"{where} の {file}" if where else file


def read_findings_json(raw: dict[str, Any], where: str = "") -> StoredFindings:
    """findings.json を読む境の唯一の口。版を見て今の版まで移してから型へ読む。

    移した結果は書き戻さない。書くのは常に今の版である。
    """
    return StoredFindings.from_json(
        upgrade_mapping(
            _stored_file_name(where, FINDINGS_FILE),
            raw,
            current=FINDINGS_FORMAT,
            upgrades=_UPGRADES,
        ),
        where,
    )


def load_findings_state(directory: Path) -> FilledFindings:
    """読む側の入口。区分を補った状態を返す。呼ぶたびにファイルから読み直す。"""
    stored = read_stored_state(directory)
    # 区分を持たない古い指摘は、ここで補う。読む側は補い済みの値だけを見る。現行に無い群は見せない
    groups = {
        group: tuple(fill_triage(group, finding) for finding in findings)
        for group, findings in stored.groups.items()
    }
    triages = {f.finding.id: f.decision.triage for findings in groups.values() for f in findings}
    # テーマの区分は保存した写しを読まず、指摘から導く。保存する値は互換のために書き続ける
    themes = tuple(replace(theme, triage=theme_triage(theme, triages)) for theme in stored.themes)
    return FilledFindings(
        last_number=stored.last_number,
        last_theme_number=stored.last_theme_number,
        groups=groups,
        themes=themes,
        stale_groups=stored.stale_groups,
    )


# 古い結果が捨てた候補の nit の主張の末尾に添えていた一文。いまは主張に含めない
_HANDOFF_NOTE = "方針では確かめられない。実装のレビューで確かめる。"


def _upgrade_finding_v1(finding: dict[str, Any]) -> dict[str, Any]:
    """版 1 の指摘一件を版 2 の形へ移す。辞書から辞書への純粋な関数で、渡された辞書は書き換えない。

    欠けた鍵は、版 2 の書き手が必ず書く鍵（null や空文字を含む）だけを足す。
    nit_reason、triage_reason、deepens、from_concern、out_of_scope は書き手も省くので足さない。
    """
    data = copy.deepcopy(finding)
    for key in (_KEY_SEVERITY, _KEY_VIEWPOINT, _KEY_CLAIM):
        data.setdefault(key, "")
    for key in (_KEY_THEME, _KEY_TRIAGE, _KEY_ADJUDICATION):
        data.setdefault(key, None)
    _migrate_legacy_nit(data)
    return data


def _upgrade_findings_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """版 1 の findings.json を版 2 の形へ移す。辞書から辞書への純粋な関数。

    区分をテーマだけが持つ時期の指摘は、テーマの区分を写す。
    last_number を持たない最も古い形は、持っている指摘の最大の番号から決める。再利用しないため。
    現行に無い名前の群は読まずに保つので、指摘の移しもかけない。
    """
    data = copy.deepcopy(raw)
    themes = []
    for theme in data.get(_KEY_THEMES, []):
        filled: dict[str, Any] = {
            _KEY_TITLE: "",
            _KEY_FINDING_IDS: [],
            _KEY_SEVERITY: "",
            _KEY_TRIAGE: None,
        }
        themes.append(filled | theme)
    theme_triages = {
        theme[_KEY_ID]: triage
        for theme in themes
        if (triage := triage_of(theme[_KEY_TRIAGE])) is not None
    }
    numbers = [0]
    groups: dict[str, Any] = {}
    for name, findings in data[_KEY_GROUPS].items():
        numbers += [
            int(m[1]) for f in findings if (m := re.fullmatch(r"F(\d+)", str(f.get(_KEY_ID))))
        ]
        if name not in GROUP_IDS:
            groups[name] = findings
            continue
        upgraded = [_upgrade_finding_v1(f) for f in findings]
        for finding in upgraded:
            if triage_of(finding[_KEY_TRIAGE]) is None and finding[_KEY_THEME] in theme_triages:
                finding[_KEY_TRIAGE] = theme_triages[finding[_KEY_THEME]].value
        groups[name] = upgraded
    return {
        _KEY_FORMAT: 2,
        _KEY_LAST_NUMBER: data.get(_KEY_LAST_NUMBER, max(numbers)),
        _KEY_GROUPS: groups,
        _KEY_STALE_GROUPS: data.get(_KEY_STALE_GROUPS, []),
        _KEY_LAST_THEME_NUMBER: data.get(_KEY_LAST_THEME_NUMBER, 0),
        _KEY_THEMES: themes,
    }


# 版 2 の開示済みの限界の懸念の文「{主張}（開示の場所: {場所の列}）」。場所は読点で並ぶ。
# 読み戻しはこの移しだけが行う
_V2_DISCLOSED_CONCERN = re.compile(r"(.*)（開示の場所: (.+)）", re.DOTALL)


def _upgrade_finding_v2(finding: dict[str, Any]) -> dict[str, Any]:
    """版 2 の指摘一件を版 3 の形へ移す。純粋な関数で、渡された辞書は書き換えない。

    版 3 は nit_reason に places を持つ。
    版 2 では開示済みの限界の場所が懸念の文の末尾にだけあった。
    形に合う開示済みの限界は、concern を主張だけにし、場所を places へ分ける。
    形に合わない開示済みの限界は、場所を作らず places を空にし、concern は文のまま残す。
    方針の群が捨てた候補から写した開示済みの限界も場所を持たず、同じ形（種類は残して places が空）
    で保存される。種類を other へ落とすと、その札と束ねの対象から外れて別の挙動になる。
    それ以外の種類は places を空にする。
    """
    data = copy.deepcopy(finding)
    reason = data.get(_KEY_NIT_REASON)
    # 版 2 の書き手は nit_reason を省く。無ければ移す中身がない
    if reason is None or "places" in reason:
        return data
    reason["places"] = []
    if reason["kind"] != DiscardKind.DISCLOSED_LIMITATION.value:
        return data
    matched = _V2_DISCLOSED_CONCERN.fullmatch(reason["concern"] or "")
    places = [] if matched is None else [p for p in matched.group(2).split(PLACE_SEPARATOR) if p]
    if matched is None or not places:
        return data
    reason["concern"] = matched.group(1)
    reason["places"] = places
    return data


def _upgrade_findings_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """版 2 の findings.json を版 3 の形へ移す。現行に無い群は移さない。"""
    data = copy.deepcopy(raw)
    for name, findings in data[_KEY_GROUPS].items():
        if name in GROUP_IDS:
            data[_KEY_GROUPS][name] = [_upgrade_finding_v2(f) for f in findings]
    data[_KEY_FORMAT] = 3
    return data


# 保存された版から一つ上の版へ移す関数。読む境がここを順にたどる
_UPGRADES: dict[int, Upgrade] = {1: _upgrade_findings_v1, 2: _upgrade_findings_v2}


def _upgrade_history_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """版 1 の findings_history.json を版 2 の形へ移す。辞書から辞書への純粋な関数。

    回の鍵（ran_at、findings、discard）は版をまたいで変わらない。変わるのは指摘一件の形だけで、
    その移しは findings.json と同じ _upgrade_finding_v1 を通す。
    現行に無い群の名前は、ここで弾かずに型へ読む段で壊れた履歴にする。
    """
    data = copy.deepcopy(raw)
    for rounds in data[_KEY_GROUPS].values():
        for entry in rounds:
            entry[_KEY_FINDINGS] = [_upgrade_finding_v1(f) for f in entry[_KEY_FINDINGS]]
    data[_KEY_FORMAT] = 2
    return data


def _upgrade_history_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """版 2 の findings_history.json を版 3 の形へ移す。指摘一件の移しは findings.json と共通。"""
    data = copy.deepcopy(raw)
    for rounds in data[_KEY_GROUPS].values():
        for entry in rounds:
            entry[_KEY_FINDINGS] = [_upgrade_finding_v2(f) for f in entry[_KEY_FINDINGS]]
    data[_KEY_FORMAT] = 3
    return data


# findings_history.json の版 → 一つ上の版へ移す関数。版は findings.json と同じ FINDINGS_FORMAT
_HISTORY_UPGRADES: dict[int, Upgrade] = {1: _upgrade_history_v1, 2: _upgrade_history_v2}


# 古い形は書かれた時点の規則で読む。現行の表を引くと、表の変更で古い保存物の読みが変わる
_LEGACY_NOT_NIT_KINDS = frozenset({DiscardKind.SETTLED_EARLIER, DiscardKind.SETTLED_EARLIER.value})


def _migrate_legacy_nit(finding: dict[str, Any]) -> None:
    """捨てた候補から写した nit の古い形を、現行の形に直す。

    nit でない指摘には触れない。
    現行の形は nit_reason 欄を持ち、claim が NitReason.claim と一致する。
    古い形は、nit_reason 欄の代わりに discard_kind 欄と concern 欄を持ち、
    claim の末尾に添え文が付く。直した結果は種類、元の文、懸念の文を nit_reason に持つ。
    元の文は古い保存物に残っていないので、添え文を外した claim で代える。
    settled_earlier の種類（古い欄でも nit_reason でも）は nit にしない種類なので、
    nit_reason を外して理由なしの捨てた候補として読む。
    discard_kind 欄を持たない最も古い結果と、知らない種類の名前を持つ結果は、種類を決められず、
    添え文を外すだけにする。知らない名前で読み込みを止めると、履歴全体が壊れた扱いになる。
    """
    if finding.get("viewpoint") != DISCARDED_VIEWPOINT:
        return
    claim = finding.get("claim", "")
    stripped = claim.removesuffix(_HANDOFF_NOTE) or claim
    legacy_kind = finding.pop("discard_kind", None)
    legacy_concern = finding.pop("concern", None)
    kind = _legacy_discard_kind(legacy_kind, finding.get("id"))
    stored = finding.get("nit_reason")
    if kind in _LEGACY_NOT_NIT_KINDS or (
        stored is not None and stored.get("kind") in _LEGACY_NOT_NIT_KINDS
    ):
        # 前の回で直った候補は nit にならない。理由なしの捨てた候補で残せば表示が崩れない
        finding.pop("nit_reason", None)
        kind = None
    if finding.get("nit_reason") is None and kind is not None:
        # 版 2 の形（places を持たない）で書く。版 3 への移しが続けて places を足す
        finding["nit_reason"] = {
            "kind": kind.value,
            "text": stripped,
            "concern": legacy_concern or None,
        }
    raw_reason = finding.get("nit_reason")
    if raw_reason is None:
        finding["claim"] = stripped
    else:
        finding["claim"] = raw_reason["concern"] or raw_reason["text"]


def _legacy_discard_kind(raw: str | None, finding_id: object) -> DiscardKind | None:
    if raw is None:
        return None
    try:
        return DiscardKind(raw)
    except ValueError:
        LOGGER.warning(
            "%s の捨てた理由の種類 %r は知らない名前なので、種類なしとして読みます",
            finding_id,
            raw,
        )
        return None


def save_findings_state(directory: Path, state: StoredFindings) -> None:
    write_text_atomic(
        directory / FINDINGS_FILE, json.dumps(state.to_json(), ensure_ascii=False, indent=2)
    )


def reserve_numbers(directory: Path, count: int) -> int:
    """F の番号を count 個取り、最初の番号を返す。

    実行の間に別の群の保存が last_number を進めていても重ならないよう、
    ロックの中で読み直して進める。
    """
    with review_lock(directory):
        state = read_stored_state(directory)
        save_findings_state(directory, state.with_reserved(count))
    return state.last_number + 1


def merge_group(
    directory: Path,
    group: GroupId,
    result: GroupResult,
    replaced_run: "ReplacedRun | None",
) -> None:
    """読み直した最新の状態に、この群の結果だけを差し替えて保存する。他の群には触れない。

    差し替えられる側の指摘は、同じロックの中で履歴（findings_history.json）へ足してから消す。
    replaced_run は差し替えられる結果を作った実行の、履歴の回に残す値で、runs.json を読む
    呼び出し側が作る。差し替えられる結果が無ければ None。
    """
    with review_lock(directory):
        state = read_stored_state(directory)
        if group in state.groups:
            _append_history(directory, group, state.groups[group], replaced_run)
        save_findings_state(directory, state.with_result(group, result))


@dataclass(frozen=True)
class ReplacedRun:
    """差し替えられる結果を作った実行の、履歴の回に残す値。

    runs.json の鍵は呼び出し側だけが知る。
    """

    ran_at: str | None
    discard: DiscardSummary


@dataclass(frozen=True)
class HistoryRound:
    """履歴の一回。差し替えられた結果の指摘と、その結果を作った実行の時刻と、捨てた割合。

    discard は、捨てた候補を記録する群（いまは方針だけ）の回が数を持つ。
    数を持たない回（古い履歴、捨てた候補を記録しない群）は UNRECORDED である。
    """

    ran_at: str | None
    findings: tuple[StoredFinding, ...]
    discard: DiscardSummary = field(
        default_factory=lambda: DiscardSummary(DiscardState.UNRECORDED)
    )

    @classmethod
    def of_findings(
        cls, ran_at: str | None, findings: list[dict[str, Any]], discard: DiscardSummary
    ) -> "HistoryRound":
        """指摘を型の回へ入れる唯一の口。今の版の形だけを受け取る。旧形式は読む境が先に直す。"""
        stored = tuple(StoredFinding.from_json(finding) for finding in findings)
        return cls(ran_at, stored, discard)

    def to_json(self) -> dict[str, Any]:
        raw: dict[str, Any] = {
            _KEY_RAN_AT: self.ran_at,
            _KEY_FINDINGS: finding_dicts(self.findings),
        }
        if self.discard.state is not DiscardState.UNRECORDED:
            # 状態と比は数から導けるので保存しない。キーが無いことが UNRECORDED を表す
            raw[_KEY_DISCARD] = {
                _KEY_DISCOVERED: self.discard.discovered or 0,
                _KEY_DISCARDED: self.discard.discarded or 0,
            }
        return raw

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "HistoryRound":
        """今の版の形の一回を読む。版の移しは済んでいる前提で、新しい回の組み立てには使わない。"""
        stored = raw.get(_KEY_DISCARD)
        if stored is None:
            summary = DiscardSummary(DiscardState.UNRECORDED)
        else:
            summary = summary_of_counts(int(stored[_KEY_DISCOVERED]), int(stored[_KEY_DISCARDED]))
        return cls.of_findings(raw[_KEY_RAN_AT], raw[_KEY_FINDINGS], summary)


@dataclass(frozen=True)
class HistoryFile:
    """findings_history.json の全体。群ごとに、古い回から順の HistoryRound を持つ。"""

    groups: dict[GroupId, tuple[HistoryRound, ...]]

    def to_json(self) -> dict[str, Any]:
        return {
            _KEY_FORMAT: FINDINGS_FORMAT,
            _KEY_GROUPS: {
                name: [entry.to_json() for entry in entries]
                for name, entries in self.groups.items()
            },
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "HistoryFile":
        """今の版の形を読む。版の移しは read_history_json が済ませる。"""
        return cls(
            {
                GroupId(group): tuple(HistoryRound.from_json(entry) for entry in rounds)
                for group, rounds in raw[_KEY_GROUPS].items()
            }
        )


def read_history_json(raw: dict[str, Any], where: str = "") -> HistoryFile:
    """findings_history.json を読む境の唯一の口。版を見て今の版まで移してから型へ読む。

    移した結果は書き戻さない。書くのは常に今の版である。
    """
    return HistoryFile.from_json(
        upgrade_mapping(
            _stored_file_name(where, HISTORY_FILE),
            raw,
            current=FINDINGS_FORMAT,
            upgrades=_HISTORY_UPGRADES,
        )
    )


@dataclass(frozen=True)
class HistoryIntact:
    """読めた履歴。ファイルが無いときは群を持たない空の履歴になる。"""

    groups: dict[GroupId, tuple[HistoryRound, ...]]


@dataclass(frozen=True)
class OwnHistory:
    """このレビュー自身の履歴の警告。"""


@dataclass(frozen=True)
class RelatedHistory:
    """関連するレビューの履歴の警告。review はそのレビューの識別子（@名前）。"""

    review: str


HistoryOrigin = OwnHistory | RelatedHistory


class OwnOriginJson(TypedDict):
    kind: Literal["own"]


class RelatedOriginJson(TypedDict):
    kind: Literal["related"]
    review: str


class HistoryWarningJson(TypedDict):
    """履歴の警告の --json の形。show と run の --json が同じ欄に載せる。"""

    path: str
    reason: str
    text: str
    origin: OwnOriginJson | RelatedOriginJson


@dataclass(frozen=True)
class HistoryWarning:
    """読めなかった履歴の警告。文の組み立てはここだけが持ち、CLI は欄を読むだけにする。"""

    path: Path
    reason: str
    origin: HistoryOrigin = OwnHistory()

    def description(self) -> str:
        return f"{self.path} が壊れています（{self.reason}）"

    def text(self) -> str:
        match self.origin:
            case OwnHistory():
                return f"{self.description()}。前の回の指摘は資料に載りません"
            case RelatedHistory(review=review):
                return (
                    f"関連するレビュー {review} の履歴 {self.description()}。"
                    "そのレビューの前の回の指摘は資料に載りません"
                )
            case unreachable:
                assert_never(unreachable)

    def to_json(self) -> HistoryWarningJson:
        origin: OwnOriginJson | RelatedOriginJson
        match self.origin:
            case OwnHistory():
                origin = {"kind": "own"}
            case RelatedHistory(review=review):
                origin = {"kind": "related", "review": review}
            case unreachable:
                assert_never(unreachable)
        return {
            "path": str(self.path),
            "reason": self.reason,
            "text": self.text(),
            "origin": origin,
        }


@dataclass(frozen=True)
class HistoryCorrupt:
    """壊れていた履歴。"""

    warning: HistoryWarning


HistoryRead = HistoryIntact | HistoryCorrupt


class HistoryCorruptError(Exception):
    """壊れた履歴へ回を足そうとした。足すと壊れた内容を上書きして失うため、書き戻さずに止める。"""


def _read_history(directory: Path) -> HistoryRead:
    """履歴を読む唯一の入口。壊れているかの判定はここで一度だけ行い、結果の型で伝える。"""
    path = directory / HISTORY_FILE
    if not path.is_file():
        return HistoryIntact({})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return HistoryIntact(read_history_json(raw, directory.name).groups)
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ReviewError,
    ) as error:
        # JSONDecodeError と UnicodeDecodeError は ValueError の子。形の崩れは次の三つで来る。
        # 読めない版も壊れた扱いにする。例外で上げると、足す書き手が新しい版の内容を上書きして失う
        return HistoryCorrupt(HistoryWarning(path, f"{type(error).__name__}: {error}"))


def _append_history(
    directory: Path,
    group: GroupId,
    replaced: Sequence[StoredFinding],
    replaced_run: ReplacedRun | None,
) -> None:
    """review_lock の中で呼ぶ。差し替えられる回を、群の履歴の末尾（新しい側）へ足す。

    指摘が 0 件の回も足す。足さないと、後から回の並びを数えたとき、0 件の回の分だけ古い側へずれる。
    同じ番号の指摘は二度足さない。
    """
    if replaced_run is None:
        # 差し替えられる結果を作った実行の記録が無い。時刻も数も言えない
        ran_at, discard = None, DiscardSummary(DiscardState.UNRECORDED)
    else:
        ran_at, discard = replaced_run.ran_at, replaced_run.discard
    read = _read_history(directory)
    if isinstance(read, HistoryCorrupt):
        raise HistoryCorruptError(
            f"{read.warning.description()}。履歴へ回を足すと中身を失うため、レビューの結果を保存せずに止めます。"
            "ファイルを直すか、退避してから実行し直してください"
        )
    # 組み立ての途中は list で溜め、書き出しの境で tuple にする
    groups = {name: list(entries) for name, entries in read.groups.items()}
    rounds = groups.setdefault(group, [])
    if replaced:
        known = {finding.body.id for entry in rounds for finding in entry.findings}
        fresh = [finding for finding in replaced if finding.body.id not in known]
        if not fresh:
            # 同じ回を二度差し替えた。すでに足してある
            return
        rounds.append(HistoryRound(ran_at, tuple(fresh), discard))
    elif not (rounds and rounds[-1].ran_at == ran_at and not rounds[-1].findings):
        rounds.append(HistoryRound(ran_at, (), discard))
    history = HistoryFile({name: tuple(entries) for name, entries in groups.items()})
    write_text_atomic(
        directory / HISTORY_FILE,
        json.dumps(history.to_json(), ensure_ascii=False, indent=2),
    )


@dataclass(frozen=True)
class HistoryLoad:
    """履歴の読み込みの結果。回の並びと警告を一組にして、出力の縁まで同じ型で渡す。

    rounds は群ごとに古い回から順の並び。壊れていれば rounds は空で、warnings が空でない。
    壊れているかは warnings が空かで見分ける。
    """

    rounds: Mapping[GroupId, Sequence[HistoryRound]]
    warnings: tuple[HistoryWarning, ...]


def load_history_checked(directory: Path) -> HistoryLoad:
    """履歴を一度だけ読み、回の並びと警告を返す。ログは流さず、警告の描画は呼び手に任せる。"""
    read = _read_history(directory)
    if isinstance(read, HistoryCorrupt):
        return HistoryLoad({}, (read.warning,))
    return HistoryLoad(read.groups, ())


def mark_stale(directory: Path) -> None:
    """結果のある群をすべて古いものとして印を付ける。結果が無ければ何もしない。"""
    if not (directory / FINDINGS_FILE).is_file():
        # 古くなる結果が無く、印だけの findings.json を作る理由も無い
        return
    with review_lock(directory):
        save_findings_state(directory, read_stored_state(directory).marked_stale())


def passed_concerns(findings: Sequence[TriagedFinding]) -> ShardConcerns:
    """方針の指摘のうち、実装のレビューへ渡す懸念を選ぶ。渡す懸念を絞る規則はここだけに置く。

    懸念の文を取り出せなかった nit は、文として並べず、件数にまとめる。
    """
    passed: list[PassedConcern] = []
    unspecified = 0
    for finding in findings:
        reason = finding.finding.nit_reason
        if reason is None or not reason.sent_to_micro:
            continue
        match reason.concern:
            case ExtractedConcern(text=concern):
                passed.append(PassedConcern(finding.finding.id, concern))
            case NoConcern():
                # 主題を取り出せなかった nit。元の文は並べずに数える
                unspecified += 1
    return ShardConcerns(tuple(passed), unspecified)


def concerns_section(concerns: ShardConcerns) -> str:
    """渡す懸念を、実装のレビューの資料の節にする。

    該当が無ければ空の文字列を返し、節ごと出さない。先頭の空行は、直前の節との区切りになる。
    """
    lines = [f"- 方針 {item.id}: {item.text}" for item in concerns.passed]
    if concerns.unspecified:
        lines.append(
            f"- 方針では確かめていない懸念が {concerns.unspecified} 件ある（主題を取り出せない）。"
        )
    if not lines:
        return ""
    return "\n".join(["", "", f"### {CONCERNS_HEADING}", "", CONCERNS_INTRO, "", *lines])
