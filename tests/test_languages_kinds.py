from typing import get_args

from jigane.languages.base import MemberKind, TypeKind
from jigane.languages.kinds import MEMBER_LOCATION_KINDS, TYPE_LOCATION_KINDS, LocationKind


def test_literal_kinds_match_the_enum_subsets() -> None:
    assert {k.value for k in TYPE_LOCATION_KINDS} == set(get_args(TypeKind))
    assert {k.value for k in MEMBER_LOCATION_KINDS} == set(get_args(MemberKind))


def test_enum_members_equal_the_plain_strings_parsers_produce() -> None:
    value: str = "constructor"
    assert value == LocationKind.CONSTRUCTOR
    assert LocationKind(value) is LocationKind.CONSTRUCTOR
