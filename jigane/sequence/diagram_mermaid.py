"""図のモデルを Mermaid の文面にする（4.2.6 項）。"""

from collections.abc import Mapping, Sequence
from typing import Any, assert_never

from jigane.gates.tree_side import TreeSide
from jigane.sequence.diagram_diff import ArrowMark, PlainMark, Retargeted

# 参加者の名札に並べる判断の箇所の数。ツリー図の札（TAG_CAP）と同じ 3 件にして、
# 名札が参加者の名前より長くならない幅に収める
PARTICIPANT_TAG_CAP = 3


def mermaid_text(value: str) -> str:
    return value.replace(";", ",").replace("#", "").replace("\n", " ")


def participant_tag(regions: Sequence[str]) -> str:
    """参加者の名札の判断の箇所。上限を超えたら先頭から上限の数だけ並べ、残りは件数にする。"""
    if len(regions) <= PARTICIPANT_TAG_CAP:
        return "".join(f" [{r}]" for r in regions)
    shown = " ".join(regions[:PARTICIPANT_TAG_CAP])
    return f" [{shown} ほか {len(regions) - PARTICIPANT_TAG_CAP} 件]"


def _arrow_mark_suffix(arrow: Mapping[str, Any], display: Mapping[str, str]) -> str:
    """矢印のラベルに添える印の文面。付け替えには変更前と変更後の受け手の表示名を添える。"""
    mark: ArrowMark = arrow.get("mark", PlainMark.SAME)
    match mark:
        case PlainMark.SAME:
            return ""
        case PlainMark.ADDED:
            return "（追加）"
        case PlainMark.REMOVED:
            return "（削除）"
        case Retargeted():
            paired = mermaid_text(display.get(mark.paired_receiver, mark.paired_receiver))
            # 図の一枚の矢印は自分が変更前か変更後かを持たないので、相手がどちらの側かで言い分ける
            when = "変更後" if mark.paired_side is TreeSide.AFTER else "変更前"
            return f"（呼び先の変更: {when}は {paired}）"
        case _ as unreachable:
            assert_never(unreachable)


def to_mermaid(diagram: Mapping[str, Any]) -> str:
    """図のモデルの一枚を、決まった規則で Mermaid の文面にする。"""
    alias = {p["qualified_name"]: p["alias"] for p in diagram["participants"]}
    display = {p["qualified_name"]: p["display"] for p in diagram["participants"]}
    lines = ["sequenceDiagram"]
    lines.extend(
        f"    participant {p['alias']} as {mermaid_text(p['display'])}"
        f"{participant_tag(p['regions'])}"
        for p in diagram["participants"]
    )
    for arrow in diagram["arrows"]:
        operator = "--x" if arrow["status"] == "unresolved" else "->>"
        tag = f" [{arrow['region']}]" if arrow["region"] else ""
        sender, receiver = alias[arrow["sender"]], alias[arrow["receiver"]]
        mark = _arrow_mark_suffix(arrow, display)
        lines.append(
            f"    {sender}{operator}{receiver}: {mermaid_text(arrow['name'])}(){tag}{mark}"
        )
    first = diagram["participants"][0]["alias"]
    lines.extend(
        f"    Note over {first}: {mermaid_text(note)}"
        for note in [*diagram["labels"], *diagram["notes"]]
    )
    return "\n".join(lines) + "\n"
