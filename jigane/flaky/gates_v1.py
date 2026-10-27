"""版 1（改名の前）の gates.json を版 2 の形へ読み替える。読み替えの置き場はここだけ。"""

from collections.abc import Mapping
from typing import Any

from jigane.flaky.catalog import Item
from jigane.flaky.explanation import explain
from jigane.gates.entry import GATES_FORMAT, GateSource


def _upgrade_entry(entry: dict[str, Any], by_id: Mapping[str, Item]) -> dict[str, Any]:
    item = by_id.get(entry["id"])
    # 版 1 の entry は全て flaky。source は目録の出典の文で、版 2 では origin が持つ
    origin = entry.get("origin", entry.get("source", item.source if item else ""))
    return {
        "number": entry["number"],
        "id": entry["id"],
        "path": entry["path"],
        "line": entry["line"],
        "excerpt": entry["excerpt"],
        "explanation": entry.get("explanation", explain(entry["id"], by_id).to_json()),
        "source": str(GateSource.FLAKY),
        "reason": entry.get("reason", ""),
        "cause": entry.get("cause", item.cause if item else ""),
        "confidence": entry.get("confidence", item.confidence if item else ""),
        "origin": origin,
        "legitimate": entry.get("legitimate", item.legitimate if item else ""),
        "detector_note": entry.get("detector_note", ""),
        **({"equivalent": entry["equivalent"]} if "equivalent" in entry else {}),
    }


def upgrade_v1(state: dict[str, Any], by_id: Mapping[str, Item]) -> dict[str, Any]:
    """知らない鍵は読み捨てる。番号の台帳の鍵は版を跨いでそのまま引き継ぎ、無いファイルは読む境が拒む。"""
    return {
        **state,
        "format": GATES_FORMAT,
        "violations": [_upgrade_entry(e, by_id) for e in state["violations"]],
        "warnings": [_upgrade_entry(e, by_id) for e in state["warnings"]],
    }
