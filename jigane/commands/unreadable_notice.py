"""読めない記述の案内の文面。"""

from jigane.gates.tree_side import TreeSide
from jigane.judgment_map.unreadable_constructs import Occurrence, UnreadableConstructs
from jigane.judgment_map.unreadable_wording import FIX_HINTS, PREDICATE, TITLES
from jigane.text.terminal import one_line_text

# 規則ごとに示す記述の数。残りは件数だけにする
_SHOWN_PER_RULE = 5


def occurrence_text(item: Occurrence) -> str:
    label = "変更前 " if item.side is TreeSide.BEFORE else ""
    return f"{label}{one_line_text(item.path)}:{item.line}"


def _line(title: str, items: tuple[Occurrence, ...]) -> str:
    shown = ", ".join(occurrence_text(item) for item in items[:_SHOWN_PER_RULE])
    rest = len(items) - _SHOWN_PER_RULE
    return f"{title}（{len(items)} 件）: {shown}" + (f" ほか {rest} 件" if rest > 0 else "")


def unreadable_text(found: UnreadableConstructs) -> list[str]:
    """読めない記述の案内の文面。無ければ空の並び。"""
    if not found.occurrences:
        return []
    lines = ["読めない記述が、判断の地図の区間のファイルに含まれています。"]
    present = [kind for kind in TITLES if found.of_kind(kind)]
    lines.extend(_line(TITLES[kind], found.of_kind(kind)) for kind in present)
    lines.append("jigane は、言語仕様の上で書ける記述でも、読めない記述をサポートしません。")
    lines.append(f"これらの記述は{PREDICATE}。直し方は種類ごとに次のとおりです。")
    lines.extend(f"{TITLES[kind]}: {FIX_HINTS[kind]}" for kind in present)
    return lines
