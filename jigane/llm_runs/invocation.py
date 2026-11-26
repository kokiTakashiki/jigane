"""レビューエージェントに共通の起動要求と結果。"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jigane.groups.definitions import Effort, GroupId
from jigane.groups.policy_runs import PolicyRole
from jigane.llm_runs.codex_mcp import StdioServer
from jigane.llm_runs.cost import Cost

if TYPE_CHECKING:
    from jigane.llm_runs.claude import InvocationRecord


def reported_output_tokens(usage: object) -> int | None:
    count = usage.get("output_tokens") if isinstance(usage, dict) else None
    return count if type(count) is int and count >= 0 else None


@dataclass(frozen=True)
class LaunchBudget:
    """1 回の起動の上限。CLI に渡す額と失敗の代用の額を、この一つの値から導く。"""

    # 上限そのもの。金額が報告されない失敗の額をこの値で代える（run）
    limit_usd: float
    # CLI に渡す額。上限から次の一往復の余裕を引いてある（cli_budget_usd）
    cli_usd: float


@dataclass(frozen=True)
class Invocation:
    prompt: str
    json_schema: dict[str, Any]
    working_directory: Path
    budget: LaunchBudget
    model: str
    tools: tuple[str, ...] = ("Read",)
    effort: Effort | None = None
    mcp_configs: tuple[Path, ...] = ()
    allowed_mcp_tools: tuple[str, ...] = ()
    # None は --max-turns を渡さない（上限なし）
    max_turns: int | None = None
    policy_role: PolicyRole | None = None
    principle: str | None = None
    reference_mcp: tuple[StdioServer, ...] = ()
    mcp_server_names: tuple[str, ...] = ()
    discovery_group: GroupId | None = None
    denied_read_paths: frozenset[str] = frozenset()


@dataclass(frozen=True)
class InvocationResult:
    structured_output: dict[str, Any] | None
    record: "InvocationRecord"


Invoke = Callable[[Invocation], InvocationResult]


class FailureKind(StrEnum):
    """起動の失敗の種類。失敗を投げる地点が決める。

    走らせ直せば通りうるのは `transient` が真の種類だけ。
    """

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    SERVER_ERROR = "server_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    TURNS_EXCEEDED = "turns_exceeded"
    BAD_OUTPUT = "bad_output"
    SHORT_EMPTY_OUTPUT = "short_empty_output"
    MCP_RESPONSE = "mcp_response"
    OTHER = "other"

    @property
    def transient(self) -> bool:
        return self in _TRANSIENT_KINDS


_TRANSIENT_KINDS = frozenset(
    {
        FailureKind.TIMEOUT,
        FailureKind.CONNECTION,
        FailureKind.RATE_LIMIT,
        FailureKind.OVERLOADED,
        FailureKind.SERVER_ERROR,
    }
)


class InvocationError(Exception):
    def __init__(
        self,
        cause: str,
        record: "InvocationRecord",
        kind: FailureKind = FailureKind.OTHER,
    ) -> None:
        super().__init__(cause)
        # cause は表示と保存の文面だけ。種類は投げる地点が kind で決める
        self.cause = cause
        # 金額が報告されない失敗の額は、投げる地点が起動の上限で代えて渡す（failure_record）
        self.record = record
        self.kind = kind

    @property
    def transient(self) -> bool:
        return self.kind.transient

    @property
    def cost(self) -> Cost:
        return self.record.cost
