"""設計書のレビューの受け持ちの表と、見出しの語から導く受け持ちの印。

三つの群（方針、実装、セキュリティ）のどれがどの懸念を問うかを、ここだけに置く。
設計書の形のレビューで使う観点の文は、この表のほかに持たない。見出しの語は HEADING_WORDS に置く。
LLM は呼ばない。

「ここだけ」の範囲は設計書の形の観点の文と表であり、観点の識別子の組には及ばない。
`jigane.skills.macro.VIEWPOINTS`（M5 を含む文字列の組で、指摘のスキーマの enum に使う）と、
図だけの雛形にも観点が残る。識別子の組を `ViewpointId` から導かないのは、図の形が M5 を使うため。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import assert_never

from jigane.design.parse import Section
from jigane.groups.definitions import GROUP_BY_ID, GROUP_IDS, MACRO, SECURITY, GroupId
from jigane.judgment_map.kinds import Level, SectionKind


class ViewpointId(StrEnum):
    """方針の観点の識別子。M5（指摘への対応）は設計書の形では使わない。"""

    M0 = "M0"
    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M6 = "M6"


@dataclass(frozen=True)
class Viewpoint:
    label: str
    text: str


# 観点の文の唯一の定義。雛形の `$viewpoints` と表の方針の行が、ここから引く
VIEWPOINTS: Mapping[ViewpointId, Viewpoint] = MappingProxyType(
    {
        ViewpointId.M0: Viewpoint("前提", "なぜこの設計が要るのか。既存の仕組みで補えないか。"),
        ViewpointId.M1: Viewpoint(
            "宣言と実現",
            "宣言した目標を、提案設計が実現しているか。目標と非目標が食い違っていないか。"
            "宣言が無いときは M1 を出さない。",
        ),
        ViewpointId.M2: Viewpoint(
            "道筋",
            "提案した分け方や依存の向きを続けた先で、目的に近づくか。"
            "同じ形の手が他に残っていて、直す場所を一つにまとめるべきではないか。",
        ),
        ViewpointId.M3: Viewpoint(
            "いまの構造との整合", "設計書が書いた形が、いまの形の図と矛盾しないか。"
        ),
        ViewpointId.M4: Viewpoint(
            "利用者に向けたインターフェース",
            "使う側の視点で、設計書の公開の契約の形や保存の形が、"
            "後から直しにくい形になっているとき。",
        ),
        ViewpointId.M6: Viewpoint(
            "技術選定",
            "選んだ技術は、宣言した目的に最適か。"
            "自作した仕組み（構文の読み、検証、依存の規則）は、既製の道具で置き換えられないか。",
        ),
    }
)


@dataclass(frozen=True)
class PolicyRow:
    """方針の行。問う懸念は観点の集合で、印を決める語は持たない（根の既定が方針）。"""

    viewpoints: frozenset[ViewpointId]


@dataclass(frozen=True)
class ConcernRow:
    """実装とセキュリティの行。問う懸念を持つ。印を決める語は HEADING_WORDS が持つ。"""

    concerns: str


Row = PolicyRow | ConcernRow


@dataclass(frozen=True)
class KindWordRow:
    """見出しの語の行のうち、節の種類だけを決めるもの。"""

    words: tuple[str, ...]
    kind: SectionKind


@dataclass(frozen=True)
class GroupWordRow:
    """見出しの語の行のうち、群だけを決めるもの。"""

    words: tuple[str, ...]
    group: GroupId


@dataclass(frozen=True)
class KindGroupWordRow:
    """見出しの語の行のうち、節の種類と群の両方を決めるもの。"""

    words: tuple[str, ...]
    kind: SectionKind
    group: GroupId


HeadingWordRow = KindWordRow | GroupWordRow | KindGroupWordRow

# 見出しの語から節の種類と群を引く唯一の表。語を読むのは heading_group と section_kind だけ。
# 並びが先に当たる順を決める。群は実装、セキュリティの順、種類は表示の定義の順に並べる
HEADING_WORDS: tuple[HeadingWordRow, ...] = (
    KindWordRow(("決定", "判断"), SectionKind.DECISION),
    KindWordRow(("非目標",), SectionKind.NON_GOAL),
    KindWordRow(("リスク", "限界"), SectionKind.RISK),
    KindWordRow(("範囲", "境目", "スコープ"), SectionKind.BOUNDARY),
    KindWordRow(("提案", "設計"), SectionKind.PROPOSAL),
    GroupWordRow(("実装計画",), GroupId.MICRO),
    KindGroupWordRow(("検証方法",), SectionKind.VERIFICATION, GroupId.MICRO),
    KindGroupWordRow(("試験", "テスト"), SectionKind.VERIFICATION, GroupId.MICRO),
    KindWordRow(("検証",), SectionKind.VERIFICATION),
    GroupWordRow(("運用", "手順"), GroupId.MICRO),
    GroupWordRow(
        (GROUP_BY_ID[SECURITY].label, "権限", "秘密", "認証", "供給網", "脅威"),
        GroupId.SECURITY,
    ),
)


def _row_group(row: HeadingWordRow) -> GroupId | None:
    """行が決める群。決めない行は None。"""
    match row:
        case GroupWordRow(group=group) | KindGroupWordRow(group=group):
            return group
        case KindWordRow():
            return None
        case _:
            assert_never(row)


def _row_kind(row: HeadingWordRow) -> SectionKind | None:
    """行が決める節の種類。決めない行は None。"""
    match row:
        case KindWordRow(kind=kind) | KindGroupWordRow(kind=kind):
            return kind
        case GroupWordRow():
            return None
        case _:
            assert_never(row)


def group_words(group: GroupId) -> tuple[str, ...]:
    """群の印を決める語を、見出しの語の表の並びで返す。"""
    return tuple(word for row in HEADING_WORDS if _row_group(row) is group for word in row.words)


# 受け持ちの表（DIVISION）は群ごとの懸念の文を持ち、語の表とは役割が違うので統合しない
POLICY_ROW = PolicyRow(frozenset(VIEWPOINTS))
MICRO_ROW = ConcernRow(
    "実装計画の段の分け方と順序、関数と型の分け方の個々の形、試験と検証の方法、"
    "運用の手順、移行の手順の誤りと抜け",
)
SECURITY_ROW = ConcernRow(
    "権限と認証、秘密の扱い、公開の設定と公開の範囲、供給網（依存、取得の経路）、"
    "信頼の境界、履歴に残る情報",
)


def _row_of(group: GroupId) -> Row:
    """群の行。群を足すと、ここが型検査で落ちる。"""
    match group:
        case GroupId.MACRO:
            return POLICY_ROW
        case GroupId.MICRO:
            return MICRO_ROW
        case GroupId.SECURITY:
            return SECURITY_ROW
        case _:
            assert_never(group)


DIVISION: Mapping[GroupId, Row] = MappingProxyType({group: _row_of(group) for group in GroupId})

# 群をまたぐ懸念の規則
CROSS_RULES: tuple[str, ...] = (
    (
        "設計書と現行のコードの食い違いは、食い違う中身の行の群が持つ。"
        "契約や構成の食い違いは方針、関数の分け方や手順の食い違いは実装、"
        "権限や公開の設定の食い違いはセキュリティである。"
    ),
    "技術選定（M6）は方針の行に入るが、依存の取得の経路や供給網の安全はセキュリティの行が持つ。",
    (
        "方針の群から実装の群への懸念の受け渡し（図から確かめられない懸念）は、"
        "表で方針の行に当たり、資料からは確かめられない懸念に限る。"
        "実装とセキュリティの行に当たる懸念は、方針の群が候補にしないので、受け渡しにも載らない。"
    ),
)


def policy_viewpoints() -> tuple[ViewpointId, ...]:
    """方針の行の観点を、識別子の順で返す。"""
    return tuple(sorted(POLICY_ROW.viewpoints))


def viewpoints_text() -> str:
    """雛形の `$viewpoints` に差し込む、方針の行の観点の一覧。"""
    return "\n".join(
        f"- {item} {VIEWPOINTS[item].label}: {VIEWPOINTS[item].text}"
        for item in policy_viewpoints()
    )


@dataclass(frozen=True)
class DesignInserts:
    """設計の雛形へ差し込む部品。本文の組み立てと版の刻印が、同じ値から作る。"""

    division: str
    viewpoints: str


def design_inserts() -> DesignInserts:
    """表から差し込む部品を組む。呼ぶたびに表の現在の中身から作る。"""
    return DesignInserts(division=division_text(), viewpoints=viewpoints_text())


def division_text() -> str:
    """雛形の `$division` に差し込む、受け持ちの表と群をまたぐ懸念の規則。"""
    rows = ["| 群 | 問う懸念 | 印を決める見出しの語 |", "| --- | --- | --- |"]
    for identifier in GROUP_IDS:
        label = GROUP_BY_ID[identifier].label
        row = DIVISION[identifier]
        match row:
            case PolicyRow(viewpoints=viewpoints):
                names = "、".join(
                    f"{item} {VIEWPOINTS[item].label}" for item in sorted(viewpoints)
                )
                rows.append(f"| {label} | 方針の観点そのもの：{names} | （根の既定） |")
            case ConcernRow(concerns=concerns):
                words = "、".join(group_words(identifier))
                rows.append(f"| {label} | {concerns} | {words} |")
            case _:
                assert_never(row)
    rules = "\n".join(f"- {rule}" for rule in CROSS_RULES)
    return "\n".join(rows) + "\n\n" + rules


def heading_group(title: str, parent_group: GroupId) -> GroupId:
    """見出しの題から受け持ちの印（群の識別子）を決める。語を読むのはこの関数だけ。

    見出しの語の表の並びの順に調べ、群を決める行のうち先に当たった行を採る。
    どれも含まなければ親の印を引き継ぐ。方針の群は語を持たない（根の既定が方針）。
    """
    for row in HEADING_WORDS:
        group = _row_group(row)
        if group is not None and any(word in title for word in row.words):
            return group
    return parent_group


# 文書の題にあたる見出しの深さ
_TITLE_LEVEL = 1


def inherited_value[T](section: Section, parent_value: T, decide: Callable[[str, T], T]) -> T:
    """節の木を歩くとき、その節が子へ渡す値（受け持ちの印や節の種類）を決める唯一の定義。

    文書の題（最上位の見出し）は、語を持っていても値を決めず、親の値をそのまま返す。
    題の「設計」のような語が全ての節に及ぶのを防ぐため。
    それ以外は判定 `decide(題, 親の値)` に任せる。
    """
    if section.level == _TITLE_LEVEL:
        return parent_value
    return decide(section.title, parent_value)


def section_marks(roots: tuple[Section, ...]) -> Mapping[Section, GroupId]:
    """節の木の全ての節に、受け持ちの印を付ける。根の既定は方針。"""
    marks: dict[Section, GroupId] = {}

    def visit(section: Section, parent_group: GroupId) -> None:
        group = inherited_value(section, parent_group, heading_group)
        marks[section] = group
        for child in section.children:
            visit(child, group)

    for root in roots:
        visit(root, MACRO)
    return marks


@dataclass(frozen=True)
class SectionKindRow:
    """節の種類の行。表示の名前と、人の判断の要否の段を持つ。"""

    label: str
    level: Level


# 節の種類の表示の定義。見出しの語から種類を引く表（HEADING_WORDS）とは役割が違うので統合しない
SECTION_KINDS: Mapping[SectionKind, SectionKindRow] = MappingProxyType(
    {
        SectionKind.DECISION: SectionKindRow("決定", Level.REQUIRED),
        SectionKind.NON_GOAL: SectionKindRow("非目標", Level.REQUIRED),
        SectionKind.RISK: SectionKindRow("リスクと限界", Level.REQUIRED),
        SectionKind.BOUNDARY: SectionKindRow("範囲の境目", Level.CONFIRM),
        SectionKind.PROPOSAL: SectionKindRow("提案設計", Level.REQUIRED),
        SectionKind.VERIFICATION: SectionKindRow("検証方法", Level.CONFIRM),
        SectionKind.OTHER: SectionKindRow("その他", Level.MACHINE),
    }
)


def section_kind(title: str, parent_kind: SectionKind) -> SectionKind:
    """見出しの題から節の種類を決める。どの語も含まなければ親の種類を引き継ぐ。

    見出しの語の表の並びの順に調べ、種類を決める行のうち先に当たった行を採る。
    """
    for row in HEADING_WORDS:
        kind = _row_kind(row)
        if kind is not None and any(word in title for word in row.words):
            return kind
    return parent_kind
