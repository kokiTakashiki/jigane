"""変更前（基点）の木から、変更後の図に対応する図を作る。"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jigane.figures.models import NoteText
from jigane.sequence.before_tree import BeforeTree
from jigane.sequence.diagram import (
    DiagramInputs,
    construct_diagrams,
)
from jigane.sequence.diagram_diff import mark_differences
from jigane.sequence.diagram_json import diagrams_to_dict
from jigane.sequence.identifiers import identities_of
from jigane.sequence.index import Member
from jigane.sequence.roots import Key, Root, RootSelection, key_of
from jigane.sequence.side import Side

NO_BASE_TREE_TEXT = "変更前のツリーが無いため、変更前の図は作れない。"
NO_BASE_TREE_DISPLAY = "変更前のツリーがないため、変更前の図は作れません。"
BEFORE_NO_ROOT_NOTE = "変更前にこの入口が無い"
BEFORE_NO_PATH_NOTE = "変更前は変更した宣言へ届かない"
AFTER_NO_ARROW_NOTE = "変更後の図に呼び出しが無い"


class BeforeAbsence(StrEnum):
    """変更前の図が無い理由。値は表示の敬体で、図のモデルの before_note にそのまま入る。"""

    NO_BASE_TREE = NO_BASE_TREE_DISPLAY
    AFTER_NO_ARROW = "変更後の図に呼び出しがありません"
    NO_ROOT = "変更前にこの入口がありません"
    NO_PATH = "変更前は変更した宣言へ届きません"

    @property
    def note(self) -> NoteText:
        """表示の敬体と資料の常体を一つにした値。"""
        match self:
            case BeforeAbsence.NO_BASE_TREE:
                material = NO_BASE_TREE_TEXT
            case BeforeAbsence.AFTER_NO_ARROW:
                material = AFTER_NO_ARROW_NOTE
            case BeforeAbsence.NO_ROOT:
                material = BEFORE_NO_ROOT_NOTE
            case BeforeAbsence.NO_PATH:
                material = BEFORE_NO_PATH_NOTE
        return NoteText(material, str(self))


@dataclass(frozen=True)
class BeforeFound:
    """変更前の図が作れた結果。"""

    diagram: dict[str, Any]


@dataclass(frozen=True)
class BeforeMissing:
    """変更前の図が作れなかった結果と、その理由。"""

    reason: BeforeAbsence


BeforeResult = BeforeFound | BeforeMissing


def _build_before(inputs: DiagramInputs) -> dict[str, Any]:
    """変更前の木の図を辞書にする。関係の矢印は足さない。"""
    built = construct_diagrams(inputs, relations=False)
    return diagrams_to_dict(identities_of(built, Side.BEFORE), built.notes, Side.BEFORE)


def _before_of(diagram: dict[str, Any], tree: BeforeTree, after: DiagramInputs) -> BeforeResult:
    """変更後の一枚の図に対する、変更前の図か、無い理由。"""
    if not diagram["arrows"]:
        # 変更後の図が空なら目標が変更前の側の C だけになり、比べる相手が無い
        return BeforeMissing(BeforeAbsence.AFTER_NO_ARROW)
    # オーバーロードや部分クラスで複数あるときは、同じパス、近い行の宣言を採る
    candidates = sorted(
        tree.by_name.get(diagram["root"], ()),
        key=lambda m: (m.path != diagram["path"], abs(m.line - diagram["line"])),
    )
    if not candidates:
        return BeforeMissing(BeforeAbsence.NO_ROOT)
    root = Root(candidates[0])
    # 目標の集合は、変更前の側の C に、変更後の図の矢印の呼び先の宣言を足したもの（4.2.4 項）。
    # 新しいメソッドを足す PR では変更前の C が空に近く、C だけでは既存の流れが消える
    targets = {**tree.changed, **_after_targets(diagram, tree.index.callables.values())}
    before = _build_before(
        DiagramInputs(tree.index, RootSelection(targets, [root]), [], after.depth, full=after.full)
    )
    found = before["diagrams"][0]
    if not found["arrows"]:
        return BeforeMissing(BeforeAbsence.NO_PATH)
    mark_differences(found["arrows"], diagram["arrows"])
    return BeforeFound(found)


def before_diagrams(
    model: dict[str, Any], tree: BeforeTree | None, after: DiagramInputs
) -> list[BeforeResult]:
    """各図に対する変更前の図か無い理由。model の図と同じ順で返す。after は変更後の構築の入力。"""
    if tree is None:
        # tree の None は呼び元が基点の木を読まなかった値、NO_BASE_TREE は図ごとの表示の理由で、
        # 役割が別なので一つにしない。
        return [BeforeMissing(BeforeAbsence.NO_BASE_TREE) for _ in model["diagrams"]]
    return [_before_of(d, tree, after) for d in model["diagrams"]]


def attach_before(model: dict[str, Any], befores: Sequence[BeforeResult]) -> None:
    """各図に、変更前の木で同じ根から辿った図 before と、無い理由 before_note を持たせる。"""
    for diagram, result in zip(model["diagrams"], befores, strict=True):
        match result:
            case BeforeFound(found):
                diagram["before"], diagram["before_note"] = found, None
            case BeforeMissing(reason):
                diagram["before"], diagram["before_note"] = None, reason


def _after_targets(diagram: dict[str, Any], members: Iterable[Member]) -> dict[Key, Member]:
    """変更後の図の矢印の呼び先の宣言を、変更前の索引から修飾名で引く。"""
    names = {f"{a['receiver']}.{a['name']}" for a in diagram["arrows"]}
    return {key_of(m): m for m in members if m.qualified_name in names}
