"""方針の群が捨てた候補を、nit の指摘に写す。

新しい記録は実行のときに写して保存する。写す前の古い記録は、読むときに同じ写し方で組み立てる。
"""

from collections.abc import Sequence
from typing import Any

from jigane.findings.discard import (
    DISCARDED_VIEWPOINT,
    Considered,
    DiscardedCandidate,
    NotNit,
)
from jigane.findings.records import StoredFinding, TriagedFinding, triaged
from jigane.groups.definitions import GroupId

# 保存した F の番号と重ならないよう、捨てた候補の通し番号（S1 など）から作る
LEGACY_ID_PREFIX = "D-"

# 古い記録から組み立てた nit は保存した指摘に無く、返信にもレポートにも入れられない。
# show の行と JSON の selection_note、番号を受ける操作の誤りが同じ文を使う
LEGACY_NOT_SELECTABLE = "古い記録の候補で、選べません"


def is_legacy_nit_id(finding_id: str) -> bool:
    return finding_id.startswith(LEGACY_ID_PREFIX)


def selection_note_of(finding_id: str) -> str | None:
    """選べない指摘に付ける注記。選べる指摘は None。"""
    return LEGACY_NOT_SELECTABLE if is_legacy_nit_id(finding_id) else None


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
        "nit_reason": reason.to_json(),
        "body": "",
        "evidence": [],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
    }


def legacy_discarded_nits(
    group: GroupId, findings: Sequence[TriagedFinding], considered: Considered | None
) -> list[TriagedFinding]:
    """写す前の古い記録の捨てた候補を、読むときだけ nit の指摘に組み立てる。保存物は書き換えない。

    写した nit が指摘に既にある記録（観点が捨てた候補の指摘がある）は、そのまま読めるので空を返す。
    番号は LEGACY_ID_PREFIX に通し番号を続け、F の番号と重ならない。
    根拠は、記録が通し番号の指す位置を持たないため付けない。
    """
    if considered is None:
        return []
    if any(f.finding.viewpoint == DISCARDED_VIEWPOINT for f in findings):
        return []
    built: list[TriagedFinding] = []
    for item in considered.discarded:
        candidate = discarded_nit_candidate(item)
        if candidate is None:
            continue
        stored = StoredFinding.of_model_output(
            {
                "id": f"{LEGACY_ID_PREFIX}{item.source}",
                **candidate,
                "group": group.value,
                "theme": None,
                "triage": None,
            },
            None,
        )
        built.append(triaged(group, stored))
    return built
