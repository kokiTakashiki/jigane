"""gates.json の違反と警告の一件の型。強さと出所を一か所で持ち、gates.json の形と往復させる。

gates.json の全体の形（版 2）は次のとおり。

- `format`: 形の版。版 1 は版の鍵を持たず、flaky.gates_v1 が読み替える。
- 番号の台帳の鍵: NumberLedger.to_json が書き、from_json が読む。どれも必須で、
  無ければ読む境で ValueError にする。
- `violations`、`warnings`: GateEntry.to_json の列。
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class GateSource(StrEnum):
    """検出の出所。値は gates.json の source の綴り。"""

    FLAKY = "flaky"
    REGEX = "regex"


class GateSeverity(StrEnum):
    """一件の強さ。正規表現の二つは値が JSON の severity の綴りで、flaky は保存しない。"""

    FLAKY = "flaky"
    REGEX_WARNING = "warning"
    REGEX_NIT = "nit"

    @property
    def source(self) -> GateSource:
        return GateSource.FLAKY if self is GateSeverity.FLAKY else GateSource.REGEX


@dataclass(frozen=True)
class FlakyDetail:
    """flaky に固有の欄。equivalent は注記の違反には無い。"""

    reason: str
    cause: str
    confidence: str
    origin: str
    legitimate: str
    detector_note: str = ""
    equivalent: dict[str, Any] | None = None


@dataclass(frozen=True)
class RegexDetail:
    """正規表現の札に固有の欄。強さは GateSeverity が持つので、今は欄を持たない。"""


@dataclass(frozen=True)
class Active:
    """終了コードに数える状態。"""


@dataclass(frozen=True)
class Allowed:
    """`jigane: allow-regex` の注記で外した状態。理由はこの状態にだけ結びつく。"""

    reason: str


GateState = Active | Allowed


@dataclass(frozen=True)
class GateEntry:
    number: str
    id: str
    path: str
    line: int
    excerpt: str
    explanation: dict[str, Any]
    severity: GateSeverity
    detail: FlakyDetail | RegexDetail
    state: GateState = Active()

    def __post_init__(self) -> None:
        if isinstance(self.detail, FlakyDetail) != (self.severity.source is GateSource.FLAKY):
            raise ValueError(f"強さ {self.severity} と固有の欄の型が合いません: {self.number}")
        if isinstance(self.state, Allowed) and self.severity.source is GateSource.FLAKY:
            # flaky の外しは検出の段で注記が吸収するので、札としては残らない
            raise ValueError(f"flaky の札は外せません: {self.number}")

    @property
    def source(self) -> GateSource:
        return self.severity.source

    @property
    def blocks_exit(self) -> bool:
        """終了コードに数えるか。外した状態の札だけが数えない。"""
        return isinstance(self.state, Active)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "number": self.number,
            "id": self.id,
            "path": self.path,
            "line": self.line,
            "excerpt": self.excerpt,
            "explanation": self.explanation,
            "source": str(self.source),
        }
        match self.detail:
            case FlakyDetail() as detail:
                data |= {
                    "reason": detail.reason,
                    "cause": detail.cause,
                    "confidence": detail.confidence,
                    "origin": detail.origin,
                    "legitimate": detail.legitimate,
                    "detector_note": detail.detector_note,
                }
                if detail.equivalent is not None:
                    data["equivalent"] = detail.equivalent
            case RegexDetail():
                data["severity"] = str(self.severity)
        if isinstance(self.state, Allowed):
            data["allowed"] = self.state.reason
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "GateEntry":
        source = GateSource(data["source"])
        common: dict[str, Any] = {
            "number": data["number"],
            "id": data["id"],
            "path": data["path"],
            "line": data["line"],
            "excerpt": data["excerpt"],
            "explanation": data["explanation"],
        }
        # allowed は版 2 で後から足した任意の鍵。無い gates.json は外していない札として読む
        reason = data.get("allowed")
        state: GateState = Active() if reason is None else Allowed(reason)
        if source is GateSource.REGEX:
            return cls(
                **common,
                severity=GateSeverity(data["severity"]),
                detail=RegexDetail(),
                state=state,
            )
        detail = FlakyDetail(
            reason=data["reason"],
            cause=data["cause"],
            confidence=data["confidence"],
            origin=data["origin"],
            legitimate=data["legitimate"],
            detector_note=data["detector_note"],
            equivalent=data.get("equivalent"),
        )
        return cls(**common, severity=GateSeverity.FLAKY, detail=detail)


# gates.json の形の版。版 1 は改名の前の形で、最上位に版の鍵を持たない
GATES_FORMAT = 2


class GateKind(StrEnum):
    """検出の区分。値は JSON の kind の綴り、label は番号と表示の頭に付く一文字。"""

    VIOLATION = "violation"
    WARNING = "warning"

    @property
    def label(self) -> str:
        return "G" if self is GateKind.VIOLATION else "W"


_LEDGER_KEYS = ("next_violation", "next_warning", "keys")


@dataclass(frozen=True)
class NumberLedger:
    """番号の払い出しの状態。作った後は変えられず、割り当ては NumberAllocator が行う。

    保存の形はこのクラスの to_json と from_json だけが持つ。
    """

    next_violation: int = 1
    next_warning: int = 1
    keys: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 呼び出し側の dict を後から書き換えても台帳が動かないよう、写して読み取り専用にする
        object.__setattr__(self, "keys", MappingProxyType(dict(self.keys)))

    @staticmethod
    def base_key(path: str, rule_id: str, text: str) -> str:
        """鍵の元。text は行の本文など、同じ場所の別の検出を見分ける文字列。"""
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        return f"{path}\0{rule_id}\0{digest}"

    def allocator(self) -> "NumberAllocator":
        """この台帳を引き継いで、一回の検出の割り当てを始める。"""
        return NumberAllocator(self.next_violation, self.next_warning, dict(self.keys))

    def to_json(self) -> dict[str, Any]:
        return {
            "next_violation": self.next_violation,
            "next_warning": self.next_warning,
            "keys": dict(self.keys),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "NumberLedger":
        missing = [name for name in _LEDGER_KEYS if name not in data]
        if missing:
            raise ValueError(f"gates.json に番号の台帳の鍵がありません: {', '.join(missing)}")
        return cls(data["next_violation"], data["next_warning"], data["keys"])


class NumberAllocator:
    """一回の検出の間だけ使う可変の帳簿。番号は assign の一か所だけが割り当て、再利用しない。"""

    def __init__(self, next_violation: int, next_warning: int, keys: dict[str, str]) -> None:
        self._next_violation = next_violation
        self._next_warning = next_warning
        self._keys = keys
        # 同じ鍵の元が一回の検出の中で何度目かを数える。保存しない
        self._occurrences: dict[str, int] = {}

    def assign(self, base_key: str, kind: GateKind) -> str:
        n = self._occurrences.get(base_key, 0) + 1
        self._occurrences[base_key] = n
        key = f"{base_key}\0{n}"
        number = self._keys.get(key)
        if number is not None and number.startswith(kind.label):
            return number
        # 前回と区分が変わった検出は、旧い番号を手放して今の区分の番号を振り直す
        if kind is GateKind.WARNING:
            number = f"{kind.label}{self._next_warning}"
            self._next_warning += 1
        else:
            number = f"{kind.label}{self._next_violation}"
            self._next_violation += 1
        self._keys[key] = number
        return number

    def settle(self) -> NumberLedger:
        """割り当ての結果を、次回へ引き継ぐ不変の台帳に確定する。"""
        return NumberLedger(self._next_violation, self._next_warning, self._keys)


@dataclass(frozen=True)
class Gates:
    """違反 G の列と警告 W の列。

    ledger は次回へ引き継ぐ不変の番号の状態で、
    列の等しさには数えない。

    保存の形はモジュールの冒頭に書いた。
    """

    violations: tuple[GateEntry, ...] = ()
    warnings: tuple[GateEntry, ...] = ()
    ledger: NumberLedger = field(default_factory=NumberLedger, compare=False)

    @property
    def blocks_exit(self) -> bool:
        """終了コードに数える違反が一件でもあるか。警告は数えない。"""
        return self.counted_count > 0

    @property
    def counted_count(self) -> int:
        """終了コードに数える違反の数。"""
        return sum(entry.blocks_exit for entry in self.violations)

    @property
    def allowed_count(self) -> int:
        """注記で外した違反の数。"""
        return len(self.violations) - self.counted_count

    def to_json(self) -> dict[str, Any]:
        return {
            **self.ledger.to_json(),
            "format": GATES_FORMAT,
            "violations": [entry.to_json() for entry in self.violations],
            "warnings": [entry.to_json() for entry in self.warnings],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Gates":
        """現行の形（版 2）だけを厳しく読む。版 1 は flaky.gates_v1 が読み替えてから渡す。"""
        if data["format"] != GATES_FORMAT:
            raise ValueError(f"知らない gates.json の版です: {data['format']}")
        return cls(
            tuple(GateEntry.from_json(entry) for entry in data["violations"]),
            tuple(GateEntry.from_json(entry) for entry in data["warnings"]),
            NumberLedger.from_json(data),
        )
