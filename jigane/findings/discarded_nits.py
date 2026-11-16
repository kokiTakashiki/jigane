"""方針の群が捨てた候補を、nit の指摘に写す。実行のときに写して保存する。"""

from typing import Any

from jigane.findings.discard import (
    DISCARDED_VIEWPOINT,
    DiscardedCandidate,
    NotNit,
)
from jigane.findings.records import (
    KEY_NIT_REASON,
)


def discarded_nit_candidate(item: DiscardedCandidate) -> dict[str, Any] | None:
    """捨てた候補一件を、主張が捨てた理由で本文と根拠の位置が空の nit の候補に写す。

    主張には懸念の文を入れ、取り出せないものは元の理由の文を入れる。種類と懸念の文は、
    モデルの明示の項目があればそれを使い、無いものだけ文から読み戻す。
    nit になった理由（種類、元の文、懸念の文）は NitReason に分けて持つ。
    直った論点と空の理由は懸念が残らないので None。
    """
    decision = item.decision()
    if isinstance(decision, NotNit):
        return None
    reason = decision.reason
    return {
        "severity": "nit",
        "viewpoint": DISCARDED_VIEWPOINT,
        "claim": reason.claim,
        KEY_NIT_REASON: reason.to_json(),
        "body": "",
        "evidence": [],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
    }
