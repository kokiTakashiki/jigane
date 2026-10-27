"""発見役が挙げた、開示済みの既知の限界。

実装とセキュリティの群の発見役は、開示の場所を disclosed_at に書く。取り込みで、
方針の群が捨てた候補から写す nit と同じ nit_reason（種類は disclosed_limitation）に直す。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from jigane.findings.discard import DisclosedNitReason, ExtractedConcern, places_of
from jigane.findings.records import StoredFinding
from jigane.llm_runs.finding_model import KEY_DISCLOSED_AT
from jigane.preconditions.integers import non_negative_int

_MIN_GROUP_SIZE = 2


def disclosed_as_nit(candidate: dict[str, Any]) -> dict[str, Any]:
    """disclosed_at のある候補を nit にし、nit_reason を持たせる。無い候補はそのまま返す。

    viewpoint と evidence は発見役の値を残す。disclosed_at は保存の形に無いので外す。
    """
    if KEY_DISCLOSED_AT not in candidate:
        return candidate
    rest = {key: value for key, value in candidate.items() if key != KEY_DISCLOSED_AT}
    claim = rest["claim"]
    reason = DisclosedNitReason(
        claim, ExtractedConcern(claim), places_of(candidate[KEY_DISCLOSED_AT])
    )
    return {**rest, "severity": "nit", "nit_reason": reason.to_json()}


@dataclass(frozen=True)
class VettedGroups:
    """検めを通った組と、捨てた組の数。組の要素は対象の指摘の位置（0 始まり）。"""

    groups: tuple[tuple[int, ...], ...]
    discarded: int


def _is_position(value: object, target_count: int) -> bool:
    """値が 0 以上 target_count 未満の整数（bool を除く）か。"""
    index = non_negative_int(value)
    return index is not None and index < target_count


def vet_groups(groups: list[list[int]], target_count: int) -> VettedGroups:
    """束ねの役が返した組から、使えるものだけを残す。

    残す条件は、要素が 2 件以上、位置が 0 以上 target_count 未満の整数、組の中で重ならない、
    先に残した組とも重ならない、の全てである。外れた組は丸ごと捨てる。
    """
    kept: list[tuple[int, ...]] = []
    taken: set[int] = set()
    discarded = 0
    for group in groups:
        usable = (
            len(group) >= _MIN_GROUP_SIZE
            and all(_is_position(i, target_count) for i in group)
            and len(set(group)) == len(group)
            and taken.isdisjoint(group)
        )
        if usable:
            kept.append(tuple(group))
            taken.update(group)
        else:
            discarded += 1
    return VettedGroups(tuple(kept), discarded)


def _union(lists: list[list[Any]]) -> list[Any]:
    """順を保って重なりを除く。要素は辞書でもよいので等しさで比べる。"""
    result: list[Any] = []
    for items in lists:
        for item in items:
            if item not in result:
                result.append(item)
    return result


def _merged(members: list[StoredFinding]) -> StoredFinding:
    lead = members[0]
    extra = dict(lead.body.extra)
    for key in ("evidence", "references"):
        if any(key in m.body.extra for m in members):
            extra[key] = _union([m.body.extra.get(key, []) for m in members])
    reason = lead.body.nit_reason
    if isinstance(reason, DisclosedNitReason) and reason.places:
        reasons = [m.body.nit_reason for m in members]
        places = tuple(
            _union([list(r.places) for r in reasons if isinstance(r, DisclosedNitReason)])
        )
        reason = replace(reason, places=places)
    return StoredFinding(replace(lead.body, extra=extra, nit_reason=reason), lead.decision)


def merge_groups(
    findings: list[StoredFinding], groups: Sequence[Sequence[int]]
) -> list[StoredFinding]:
    """検め済みの組ごとに一件へまとめる。組に入らない指摘はそのまま、元の順で残る。

    先頭の指摘の主張、番号、その他の欄を保つ。根拠は重なりを除いて合わせ、開示の場所は
    各指摘の場所を順に並べて重なりを除く。groups は vet_groups を通した値を渡す。
    """
    merged_at = {group[0]: group for group in groups}
    absorbed = {i for group in groups for i in group[1:]}
    result: list[StoredFinding] = []
    for index, finding in enumerate(findings):
        if index in absorbed:
            continue
        group = merged_at.get(index)
        result.append(finding if group is None else _merged([findings[i] for i in group]))
    return result
