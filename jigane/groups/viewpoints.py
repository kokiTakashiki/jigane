"""観点の表。番号、名前、問いの文は viewpoints.csv だけに置く。"""

import csv
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from jigane.groups.definitions import MICRO, GroupId

VIEWPOINT_FILE = Path(__file__).with_name("viewpoints.csv")


class ViewpointKind(StrEnum):
    """観点の種別。値は表の kind 列と同じ。"""

    DEFECT = "defect"
    TEST_CONVENTION = "test_convention"
    POLICY = "policy"
    SECURITY = "security"
    DESIGN_PRINCIPLE = "design_principle"


@dataclass(frozen=True)
class Viewpoint:
    """uuid は変わらない識別子。number は表示の番号で、改番しても uuid は変わらない。"""

    uuid: uuid.UUID
    # None は群に属さない。設計の原則（P1〜P4）が方針と実装の両方の群で問われるため
    group: GroupId | None
    kind: ViewpointKind
    number: str
    legacy_number: str
    name: str
    question: str
    design_question: str


def _unique[T](values: list[T], what: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"観点の表で {what} が重複しています")


def load_viewpoints(path: Path = VIEWPOINT_FILE) -> tuple[Viewpoint, ...]:
    """表を読み、検証する。違反は ValueError。"""
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    viewpoints = tuple(
        Viewpoint(
            uuid=uuid.UUID(row["uuid"]),
            group=GroupId(row["group"]) if row["group"] else None,
            kind=ViewpointKind(row["kind"]),
            number=row["number"],
            legacy_number=row["legacy_number"],
            name=row["name"],
            question=row["question"],
            design_question=row["design_question"],
        )
        for row in rows
    )
    _unique([viewpoint.uuid for viewpoint in viewpoints], "uuid")
    _unique([viewpoint.number for viewpoint in viewpoints], "number")
    _unique([viewpoint.legacy_number for viewpoint in viewpoints], "legacy_number")
    return viewpoints


VIEWPOINTS = load_viewpoints()


def by_number(number: str) -> Viewpoint:
    for viewpoint in VIEWPOINTS:
        if viewpoint.number == number:
            return viewpoint
    raise KeyError(number)


def viewpoint_fields(number: str) -> dict[str, str | None]:
    """利用者へ出す JSON 用に、表示の番号から観点の識別子、名前、種別を引く。

    表に無い値は三つとも None。
    """
    for viewpoint in VIEWPOINTS:
        if viewpoint.number == number:
            return {
                "viewpoint_id": str(viewpoint.uuid),
                "viewpoint_name": viewpoint.name or None,
                "viewpoint_kind": str(viewpoint.kind),
            }
    # discarded や空文字など、表に無い値は観点を指さない
    return {"viewpoint_id": None, "viewpoint_name": None, "viewpoint_kind": None}


def by_uuid(identifier: uuid.UUID) -> Viewpoint:
    for viewpoint in VIEWPOINTS:
        if viewpoint.uuid == identifier:
            return viewpoint
    raise KeyError(identifier)


def by_legacy_number(number: str) -> Viewpoint:
    for viewpoint in VIEWPOINTS:
        if viewpoint.legacy_number == number:
            return viewpoint
    raise KeyError(number)


def design_principles() -> tuple[Viewpoint, ...]:
    """設計の原則（P1〜P4）を表の順に返す。群に属さず、方針と実装の群が問う。"""
    return tuple(
        viewpoint for viewpoint in VIEWPOINTS if viewpoint.kind == ViewpointKind.DESIGN_PRINCIPLE
    )


def of_group(group: GroupId) -> tuple[Viewpoint, ...]:
    """群の観点を表の順に返す。実装の群は、D と T の後に設計の原則を足す。

    方針の群の原則は、一層目が design_principles() で引く。
    """
    own = tuple(viewpoint for viewpoint in VIEWPOINTS if viewpoint.group == group)
    if group != MICRO:
        return own
    return (*own, *design_principles())


# コードが名指しする観点。番号は改番で変わるため、変わらない uuid で同じ観点を指す。
PREMISE = by_uuid(uuid.UUID("efe2d300-0592-4e7a-8f9b-7126e3cb1520"))
PATH = by_uuid(uuid.UUID("edf5f49f-e80b-4611-84d7-977bb5929dc7"))
STRUCTURE = by_uuid(uuid.UUID("2abd6c81-5831-4059-986d-aee4695d9074"))
BOT_RESPONSE = by_uuid(uuid.UUID("287c11c4-9787-43ef-8ee2-87bfb84fc951"))

# 設計の原則。番号は P1〜P4 で、同じく uuid で指す。
SINGLE_SOURCE = by_uuid(uuid.UUID("622e8c84-0d44-4b94-9b9e-807506dd7048"))
YAGNI = by_uuid(uuid.UUID("9e4f5789-d0d4-4fa1-be1b-c085fbc8e20e"))
KISS = by_uuid(uuid.UUID("eb86c3ec-abd3-4830-aa1e-c7a160687071"))
SEPARATION_OF_CONCERNS = by_uuid(uuid.UUID("7dfa8c6a-d367-4f5b-ae32-cce643a7ec85"))
