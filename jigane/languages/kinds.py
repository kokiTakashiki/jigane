"""宣言と場所の種類の唯一の定義。"""

from enum import StrEnum
from typing import assert_never


class LocationKind(StrEnum):
    """引き当てた先の種類の唯一の定義。行を持つかどうかも種類から決まる。"""

    # 型の宣言（TypeKind と値が揃う）
    CLASS = "class"
    STRUCT = "struct"
    RECORD = "record"
    INTERFACE = "interface"
    ENUM = "enum"
    DELEGATE = "delegate"
    MODULE = "module"
    # 型の中、またはモジュール直下の宣言（MemberKind と値が揃う。FUNCTION はモジュール直下の関数）
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    FIELD = "field"
    PROPERTY = "property"
    PARAMETER = "parameter"
    VARIABLE = "variable"
    # 宣言以外
    FILE = "file"
    DIRECTORY = "directory"
    SUBCOMMAND = "subcommand"
    OPTION = "option"
    STORED_FILE = "stored_file"

    @property
    def has_line(self) -> bool:
        match self:
            case LocationKind.FILE | LocationKind.DIRECTORY:
                return False
            case (
                LocationKind.CLASS
                | LocationKind.STRUCT
                | LocationKind.RECORD
                | LocationKind.INTERFACE
                | LocationKind.ENUM
                | LocationKind.DELEGATE
                | LocationKind.MODULE
                | LocationKind.FUNCTION
                | LocationKind.METHOD
                | LocationKind.CONSTRUCTOR
                | LocationKind.FIELD
                | LocationKind.PROPERTY
                | LocationKind.PARAMETER
                | LocationKind.VARIABLE
                | LocationKind.SUBCOMMAND
                | LocationKind.OPTION
                | LocationKind.STORED_FILE
            ):
                return True
            case _ as unreachable:
                assert_never(unreachable)


# base.py の TypeKind と MemberKind（Literal）が取る値。Literal は列挙から導けないため、
# tests/test_languages_kinds.py が二つの定義の一致を確かめる
TYPE_LOCATION_KINDS: tuple[LocationKind, ...] = (
    LocationKind.CLASS,
    LocationKind.STRUCT,
    LocationKind.RECORD,
    LocationKind.INTERFACE,
    LocationKind.ENUM,
    LocationKind.DELEGATE,
    LocationKind.MODULE,
)
MEMBER_LOCATION_KINDS: tuple[LocationKind, ...] = (
    LocationKind.METHOD,
    LocationKind.CONSTRUCTOR,
    LocationKind.FIELD,
    LocationKind.PROPERTY,
    LocationKind.PARAMETER,
    LocationKind.VARIABLE,
)
