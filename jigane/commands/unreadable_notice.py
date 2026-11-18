"""読めない記述の案内の文面。"""

from jigane.gates.construct_kind import ConstructKind
from jigane.gates.tree_side import TreeSide
from jigane.judgment_map.unreadable_constructs import Occurrence, UnreadableConstructs
from jigane.judgment_map.unreadable_wording import PREDICATE, UnreadableWording
from jigane.languages import registry
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


def _present_kinds(found: UnreadableConstructs) -> list[ConstructKind]:
    """案内に出す種類。構文エラー、registry の登録順の言語、同じ言語の中は種類の値の昇順。"""
    order = {adapter.name: index for index, adapter in enumerate(registry.adapters())}

    def position(kind: ConstructKind) -> tuple[bool, int, str]:
        # 構文エラー（言語が None）と登録に無い言語は末尾に置く
        return (
            kind != ConstructKind.SYNTAX_ERROR,
            order.get(kind.language or "", len(order)),
            kind.value,
        )

    return sorted({o.kind for o in found.occurrences}, key=position)


def _wording_of(found: UnreadableConstructs, kind: ConstructKind) -> UnreadableWording:
    # 同じ種類の文面は検出が一致を確かめているので、最初の一件で足りる
    return found.of_kind(kind)[0].wording


def unreadable_text(found: UnreadableConstructs) -> list[str]:
    """読めない記述の案内の文面。無ければ空の並び。"""
    if not found.occurrences:
        return []
    lines = ["読めない記述が、判断の地図の区間のファイルに含まれています。"]
    present = _present_kinds(found)
    lines.extend(_line(_wording_of(found, kind).title, found.of_kind(kind)) for kind in present)
    lines.append("jigane は、言語仕様の上で書ける記述でも、読めない記述をサポートしません。")
    lines.append(f"これらの記述は{PREDICATE}。直し方は種類ごとに次のとおりです。")
    for kind in present:
        wording = _wording_of(found, kind)
        lines.append(f"{wording.title}: {wording.fix_hint}")
    return lines
