"""`jigane flaky` が出す検出一件の形。区分、終了コード、テキスト、JSON はここから引く。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jigane.flaky.catalog import Item
from jigane.flaky.detector import Detection, QueryDetection
from jigane.flaky.explanation import explain
from jigane.flaky.gates import gate_kind
from jigane.gates.entry import GateKind
from jigane.gates.explanation import GateExplanation


@dataclass(frozen=True)
class Finding:
    id: str
    kind: GateKind
    path: str
    line: int
    explanation: GateExplanation

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "path": self.path,
            "line": self.line,
            "concern": self.explanation.concern,
            "violation": self.explanation.violation,
            "direction": self.explanation.direction,
        }

    def to_lines(self) -> list[str]:
        return [
            f"{self.kind.label} {self.path}:{self.line} [{self.id}]",
            f"  懸念: {self.explanation.concern}",
            f"  理由: {self.explanation.violation}",
            f"  直す向き: {self.explanation.direction}",
        ]


def to_finding(detection: Detection, by_id: Mapping[str, Item]) -> Finding:
    spec = detection.spec if isinstance(detection, QueryDetection) else None
    return Finding(
        detection.item_id,
        gate_kind(detection, by_id),
        detection.path,
        detection.line,
        explain(detection.item_id, by_id, spec),
    )


def of_kind(findings: Sequence[Finding], kind: GateKind) -> list[Finding]:
    return [finding for finding in findings if finding.kind is kind]


def fails(findings: Sequence[Finding], *, strict: bool) -> bool:
    """違反があれば失敗。strict なら警告でも失敗。"""
    return bool(of_kind(findings, GateKind.VIOLATION)) or (
        strict and bool(of_kind(findings, GateKind.WARNING))
    )
