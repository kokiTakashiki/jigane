"""ゲート違反を規則の ID の代わりに読ませる三つの文の型。

flaky と正規表現の規則が、返信の経路を共通にするために共有する。どちらの目録にも依存しない。
"""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class GateExplanation:
    """ゲート違反を規則の ID の代わりに読ませる三つの文。何が懸念か、なぜ違反か、直す向き。"""

    concern: str
    violation: str
    direction: str

    @property
    def text(self) -> str:
        return self.concern + self.violation + self.direction

    def with_direction(self, direction: str) -> "GateExplanation":
        return replace(self, direction=direction)

    def to_json(self) -> dict[str, str]:
        return {
            "concern": self.concern,
            "violation": self.violation,
            "direction": self.direction,
            "text": self.text,
        }
