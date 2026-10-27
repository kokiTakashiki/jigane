"""実装の群が方針から受け取った懸念を、受けたシャードと作られた指摘に対応づける。"""

from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Any, ClassVar

from jigane.findings.records import TriagedFinding
from jigane.llm_runs.launch_failure import DISCOVERY_ROLE
from jigane.skills.pipeline import GroupRun


def _json_value(value: object) -> object:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, ReceivedConcern):
        return value.to_json()
    return value


def _fields_json(item: object) -> dict[str, Any]:
    # JSON の鍵はデータクラスの項目名と同じ綴りにする。項目の定義から引くので、両者は食い違わない
    return {field.name: _json_value(getattr(item, field.name)) for field in fields(item)}  # type: ignore[arg-type]


@dataclass(frozen=True)
class ReceivedConcern:
    """方針の指摘の番号ごとの、懸念の文と受けたシャードと作られた指摘。"""

    id: str
    text: str
    # 受けたシャードの番号（プロンプトの「担当のシャード」と同じ 1 始まり）の昇順
    shards: tuple[int, ...]
    # from_concern がこの番号に一致する実装の指摘の番号。空なら指摘にしなかった
    finding_ids: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return _fields_json(self)


@dataclass(frozen=True)
class NotRecorded:
    """受けた懸念の記録がこの実行に無い。古い記録で、懸念が無かった実行とは区別する。"""

    state: ClassVar[str] = "not_recorded"

    def to_json(self) -> dict[str, Any]:
        return {"state": self.state, **_fields_json(self)}


@dataclass(frozen=True)
class NoneReceived:
    """記録は有り、発見役のどのシャードにも懸念を渡していない。"""

    state: ClassVar[str] = "none"

    def to_json(self) -> dict[str, Any]:
        return {"state": self.state, **_fields_json(self)}


@dataclass(frozen=True)
class Received:
    """懸念を渡した実行。"""

    state: ClassVar[str] = "received"

    concerns: tuple[ReceivedConcern, ...]
    # 文を取り出せず件数だけ渡した懸念の、全シャードの合計
    unspecified: int

    def to_json(self) -> dict[str, Any]:
        return {"state": self.state, **_fields_json(self)}


ReceivedConcerns = NotRecorded | NoneReceived | Received


def received_concerns(
    run: GroupRun | None, findings: Iterable[TriagedFinding]
) -> ReceivedConcerns:
    """最新の実行の発見役の記録から集める。run が None か記録が無ければ NotRecorded。"""
    recorded = [
        (item.split_index, item.concerns)
        for item in (() if run is None else run.invocations or ())
        if item.role == DISCOVERY_ROLE
        and item.split_index is not None
        and item.concerns is not None
    ]
    if not recorded:
        return NotRecorded()
    texts: dict[str, str] = {}
    shards: dict[str, set[int]] = {}
    unspecified = 0
    for index, found in recorded:
        unspecified += found.unspecified
        for passed in found.passed:
            texts.setdefault(passed.id, passed.text)
            shards.setdefault(passed.id, set()).add(index + 1)
    made: dict[str, list[str]] = {}
    for finding in findings:
        concern_id = finding.finding.from_concern
        if concern_id is not None:
            made.setdefault(concern_id, []).append(finding.finding.id)
    ordered = sorted(texts, key=lambda number: (len(number), number))
    if not ordered and unspecified == 0:
        return NoneReceived()
    return Received(
        tuple(
            ReceivedConcern(
                number, texts[number], tuple(sorted(shards[number])), tuple(made.get(number, ()))
            )
            for number in ordered
        ),
        unspecified,
    )
