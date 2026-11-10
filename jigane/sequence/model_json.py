"""図のモデルを JSON の辞書へ書き出す。対比の印を保存する形の鍵へ展開する唯一の場所。"""

from collections.abc import Mapping
from typing import Any, assert_never

from jigane.sequence.diagram_diff import ArrowMark, PlainMark, Retargeted


def _mark_entries(mark: ArrowMark) -> dict[str, str]:
    match mark:
        case PlainMark():
            return {"mark": mark.value}
        case Retargeted():
            return {
                "mark": "retargeted",
                "paired_receiver": mark.paired_receiver,
                "paired_side": mark.paired_side.value,
            }
        case _ as unreachable:
            assert_never(unreachable)


def _arrow_json(arrow: Mapping[str, Any]) -> dict[str, Any]:
    if "mark" not in arrow:
        return dict(arrow)
    rest = {k: v for k, v in arrow.items() if k != "mark"}
    return {**rest, **_mark_entries(arrow["mark"])}


def _diagram_json(diagram: Mapping[str, Any]) -> dict[str, Any]:
    entry = {**diagram, "arrows": [_arrow_json(a) for a in diagram["arrows"]]}
    before = diagram.get("before")
    if before is not None:
        entry["before"] = _diagram_json(before)
    return entry


def model_to_json(model: Mapping[str, Any]) -> dict[str, Any]:
    """図のモデルの全体を JSON にする。変更前の図の矢印も同じ規則で書き出す。"""
    return {**model, "diagrams": [_diagram_json(d) for d in model["diagrams"]]}
