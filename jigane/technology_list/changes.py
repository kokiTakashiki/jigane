"""技術選定のモデルが使う、変化と分類の値、変化の表示の文言。

節（tables.py）が変化の値を文言に訳して表のセルに入れ、値そのものを行ごとに別に持たせる。
資料と CLI は節を描くだけで、この表を持たない。
"""

# 技術の分類。技術の表の根拠が何かを決める
SAME_REPOSITORY = "same_repository"
EXTERNAL = "external"
LANGUAGE_OS = "language_os"

ADDED = "added"
REMOVED = "removed"
VERSION_CHANGED = "version_changed"
UNCHANGED = "unchanged"

CHANGE_TEXT = {
    ADDED: "増えた",
    REMOVED: "消えた",
    VERSION_CHANGED: "版を変えた",
    UNCHANGED: "変わらない",
}


def change_of(*, had_before: bool, has_after: bool, equal: bool) -> str:
    """前後の有無と値の一致から変化を決める。前後どちらにも無い組では呼ばない。

    変わらない（UNCHANGED）を表に出すかは、呼び出し側が決める。
    """
    if not had_before:
        return ADDED
    if not has_after:
        return REMOVED
    if not equal:
        return VERSION_CHANGED
    return UNCHANGED
