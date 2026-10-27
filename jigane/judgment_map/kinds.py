from enum import StrEnum


class Kind(StrEnum):
    COMMENT_ONLY = "comment_only"
    FORMAT_ONLY = "format_only"
    GENERATED = "generated"
    TEST = "test"
    LOGIC = "logic"
    SENSITIVE_LOGIC = "sensitive_logic"
    PUBLIC_INTERFACE = "public_interface"
    INTERNAL_INTERFACE = "internal_interface"
    DEPENDENCY_BUILD = "dependency_build"
    BEHAVIOR_REMOVAL = "behavior_removal"
    BOT_RESPONSE = "bot_response"


class StoredMode(StrEnum):
    """保存物の mode。書く側（地図の組み立て）と読む側（map_kind_of_mode）がこの語彙を共有する。"""

    DIFF = "diff"
    FEATURE = "feature"
    DESIGN_DOC = "design_doc"


class Level(StrEnum):
    REQUIRED = "required"
    CONFIRM = "confirm"
    MACHINE = "machine"

    @property
    def rank(self) -> int:
        # 重さの順は required > confirm > machine
        return {Level.REQUIRED: 2, Level.CONFIRM: 1, Level.MACHINE: 0}[self]


LEVEL_OF_KIND: dict[Kind, Level] = {
    Kind.PUBLIC_INTERFACE: Level.REQUIRED,
    Kind.INTERNAL_INTERFACE: Level.REQUIRED,
    Kind.DEPENDENCY_BUILD: Level.REQUIRED,
    Kind.BEHAVIOR_REMOVAL: Level.REQUIRED,
    Kind.SENSITIVE_LOGIC: Level.REQUIRED,
    Kind.BOT_RESPONSE: Level.REQUIRED,
    Kind.TEST: Level.CONFIRM,
    Kind.LOGIC: Level.CONFIRM,
    Kind.COMMENT_ONLY: Level.MACHINE,
    Kind.FORMAT_ONLY: Level.MACHINE,
    Kind.GENERATED: Level.MACHINE,
}


class SectionKind(StrEnum):
    """設計書の節の種類。判断の地図の箇所の種類になる。"""

    DECISION = "decision"
    NON_GOAL = "non_goal"
    RISK = "risk"
    BOUNDARY = "boundary"
    PROPOSAL = "proposal"
    VERIFICATION = "verification"
    OTHER = "other"


# 行の kind の値が節の種類か、コードの種類かで型を決める。二つの列挙の値は重ならない
SECTION_KIND_VALUES = frozenset(kind.value for kind in SectionKind)
