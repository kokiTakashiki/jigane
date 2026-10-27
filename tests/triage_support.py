"""区分の決定を試す検査が、最小の辞書から型の指摘を作る口。"""

from typing import Any

from review_fakes import stored_finding

from jigane.findings.records import StoredFinding, Triage, TriageDecision, decide_triage
from jigane.groups.definitions import GroupId


def stored(raw: dict[str, Any], scope_record: dict[str, Any] | None = None) -> StoredFinding:
    """検査が要る欄だけを持つ辞書を、型の指摘にする。範囲外の記録は指摘の側に載せる。"""
    data: dict[str, Any] = {"id": "F1", **raw}
    if scope_record is not None:
        data["out_of_scope"] = scope_record
    return stored_finding(data)


def decided(
    group: GroupId,
    raw: dict[str, Any],
    *,
    theme_position: int | None = None,
    scope_record: dict[str, Any] | None = None,
) -> TriageDecision:
    return decide_triage(group, stored(raw, scope_record), theme_position=theme_position)


def assigned(
    group: GroupId,
    raw: dict[str, Any],
    *,
    theme_position: int | None = None,
    scope_record: dict[str, Any] | None = None,
) -> Triage:
    return decided(group, raw, theme_position=theme_position, scope_record=scope_record).triage
