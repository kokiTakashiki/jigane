"""図のモデルから、根や判断の箇所で図を選び、図から判断の箇所の番号を引く（4.2.6 項）。"""

from collections.abc import Mapping
from typing import Any


def region_ids(diagram: Mapping[str, Any]) -> list[str]:
    """図の一枚に札として載る判断の箇所の番号。`to_mermaid` が書く順で、重複は除く。"""
    found = [r for p in diagram["participants"] for r in p["regions"]]
    found += [a["region"] for a in diagram["arrows"] if a["region"]]
    return list(dict.fromkeys(found))


def select_diagrams(
    model: dict[str, Any], root: str | None = None, region: str | None = None
) -> dict[str, Any]:
    if region is not None:
        hits = {entry["diagram"] for entry in model["region_index"].get(region, [])}
        return {
            **model,
            "diagrams": [d for d in model["diagrams"] if d["root"] in hits],
            "region_index": {k: v for k, v in model["region_index"].items() if k == region},
        }
    if root is not None:
        return {**model, "diagrams": [d for d in model["diagrams"] if d["root"] == root]}
    return model
