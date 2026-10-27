"""Claude Code CLI の起動層。"""

import json
import math
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields, replace
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from jigane.git.process import CLAUDE_BINARY_ENVIRONMENT, launch_failure, run_claude_resolved
from jigane.groups.definitions import Effort
from jigane.llm_runs.cost import (
    BudgetOutcome,
    Cost,
    Measured,
    NotCharged,
    Unreported,
    cost_as_record,
    cost_from_record,
    total_by_source,
    with_limit,
)


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
    mcp_config: Path | None = None
    allowed_mcp_tools: tuple[str, ...] = ()
    # None は --max-turns を渡さない（上限なし）
    max_turns: int | None = None


@dataclass(frozen=True)
class StoredInvocationRecord:
    """runs.json に保存される起動の記録。書く側（InvocationRecord）もこの型の系統で上限を持つ。"""

    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    # 使った額とその出所。payload から作るのは parse_record の一か所
    cost: Cost
    duration_ms: int
    num_turns: int
    is_error: bool
    terminal_reason: str | None
    permission_denials: int
    # この起動が持っていた上限。上限の欄が無い古い記録は None
    max_budget_usd: float | None

    def usage_fields(self) -> dict[str, Any]:
        """額を除く欄。額と出所は cost_as_record が書く。"""
        usage = asdict(self)
        del usage["cost"]
        if usage["max_budget_usd"] is None:
            del usage["max_budget_usd"]
        return usage

    def as_row(self) -> dict[str, Any]:
        """runs.json の invocations の行の、起動の記録の部分。"""
        return {**self.usage_fields(), **cost_as_record(self.cost)}

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StoredInvocationRecord":
        """行から起動の記録の欄だけを読む。上限の欄の無い古い行は None。

        names に無い鍵（過去に書いた session_id など）は読まずに捨てる。
        """
        names = {item.name for item in fields(cls)} - {"cost"}
        return cls(
            **{
                "max_budget_usd": None,
                **{key: row[key] for key in names if key in row},
            },
            cost=cost_from_record(dict(row)),
        )

    def budget_outcome(self, failure_kind: "FailureKind | None") -> BudgetOutcome | None:
        """上限について言える結果の唯一の出所。保存する値も、読み直した値もここから導く。

        failure_kind は失敗した起動の種類で、成功した起動は None。失敗した起動は、種類だけが
        上限で止まったかを言う。失敗に超過の判定は当てない（部分の費用は上限を超えうるため）。
        """
        if failure_kind is not None:
            return (
                BudgetOutcome.STOPPED_AT_LIMIT
                if failure_kind is FailureKind.BUDGET_EXCEEDED
                else None
            )
        return BudgetOutcome.EXCEEDED_AFTER_SUCCESS if self.exceeded_after_success() else None

    def exceeded_after_success(self) -> bool:
        """成功した起動の費用が、この記録の持つ上限を超えたか。上限の無い記録は超えたと言えない。"""
        if self.max_budget_usd is None:
            return False
        return total_by_source([self.cost]).usd > self.max_budget_usd


@dataclass(frozen=True)
class InvocationRecord(StoredInvocationRecord):
    # この起動が持っていた上限（Invocation.budget.limit_usd）。記録を作る run が写す
    max_budget_usd: float


@dataclass(frozen=True)
class InvocationResult:
    structured_output: dict[str, Any] | None
    record: InvocationRecord


Invoke = Callable[[Invocation], InvocationResult]


@dataclass(frozen=True)
class RunBudget:
    """1 回の起動のモデルと上限額と、次の一往復の余裕を引くか。いつも一緒に動くので組で持つ。"""

    model: str
    max_budget_usd: float
    # 真なら、CLI に渡す上限から次の一往復の余裕を引く（cli_budget_usd）
    reserve_margin: bool

    def cli_usd(self, prompt_tokens: int) -> float | None:
        return cli_budget_usd(self.model, prompt_tokens, self.max_budget_usd)

    def input_stops_run(self, prompt_tokens: int) -> bool:
        """入力の費用だけで、その起動を止めるか。実行前の検査と見積もりが、止めの判定をここから引く。"""
        if self.reserve_margin:
            return self.cli_usd(prompt_tokens) is None
        return input_cost_usd(self.model, prompt_tokens).usd >= self.max_budget_usd


@dataclass(frozen=True)
class RunSettings:
    """1 回の実行の全ての起動が共有する設定。Invocation はここから `invocation` で作る。

    model と effort を持つ Invocation をこの経路以外で組み立てると、書き忘れた effort が
    黙って既定の挙動になる。
    """

    # モデルと上限と余裕の扱いは組で持つ。走らせる前の検査（skills/input_budget）も
    # 同じ組から決めるので、検査と CLI に渡す上限が食い違わない。
    budget: RunBudget
    # None の群は --effort を渡さず、Claude Code CLI の既定に任せる
    effort: Effort | None
    invoke: Invoke
    # 往復の上限。None の実行は --max-turns を渡さない
    max_turns: int | None = None

    def invocation(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
        working_directory: Path,
        tools: tuple[str, ...] = ("Read",),
        mcp_config: Path | None = None,
        allowed_mcp_tools: tuple[str, ...] = (),
    ) -> Invocation:
        """self.budget.reserve_margin が偽なら、CLI に上限そのものを渡す（余裕を引かない）。"""
        budget = self.budget.max_budget_usd
        if self.budget.reserve_margin:
            tokens = estimate_input_tokens(len(prompt.encode("utf-8")))
            reduced = self.budget.cli_usd(tokens)
            # None は余裕を引くと下限を割る入力。走らせる前の検査（skills/input_budget）が先に
            # 止める約束で、検査の無い起動でも下限の値を渡し、上限を超える側へは倒さない。
            budget = (
                reduced
                if reduced is not None
                else self.budget.max_budget_usd * MIN_CLI_BUDGET_FRACTION
            )
        return Invocation(
            prompt=prompt,
            json_schema=json_schema,
            working_directory=working_directory,
            budget=LaunchBudget(limit_usd=self.budget.max_budget_usd, cli_usd=budget),
            model=self.budget.model,
            tools=tools,
            effort=self.effort,
            mcp_config=mcp_config,
            allowed_mcp_tools=allowed_mcp_tools,
            max_turns=self.max_turns,
        )


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

# HTTP の状態の数字は、金額や小数の一部（0.429、0.4291）に当たらないよう、前後に数字と小数点が
# 続かない 3 桁に限り、さらに「status」「HTTP」「error」の直後か、状態の名前の直前にあるものだけ
# を読む。
_STATUS = r"(?<![\d.])(429|5\d\d)(?!\d|\.\d)"
_HTTP_STATUS_PATTERNS = (
    re.compile(rf"\b(?:status|http|error)(?:\s+code)?\s*[:=]?\s*{_STATUS}", re.IGNORECASE),
    re.compile(
        rf"{_STATUS}\s+(?:too many requests|internal server error|bad gateway|service unavailable"
        r"|gateway time-?out|overloaded)",
        re.IGNORECASE,
    ),
)
_KEYWORD_KINDS: tuple[tuple[re.Pattern[str], FailureKind], ...] = (
    (re.compile(r"overloaded", re.IGNORECASE), FailureKind.OVERLOADED),
    (re.compile(r"rate[ _-]?limit|too many requests", re.IGNORECASE), FailureKind.RATE_LIMIT),
    (re.compile(r"timed? ?out|ETIMEDOUT|タイムアウト", re.IGNORECASE), FailureKind.TIMEOUT),
    (re.compile(r"ECONNRESET|ECONNREFUSED", re.IGNORECASE), FailureKind.CONNECTION),
    (
        re.compile(r"temporarily unavailable|service unavailable", re.IGNORECASE),
        FailureKind.SERVER_ERROR,
    ),
)


def classify_failure_text(text: str) -> FailureKind:
    """失敗の種類を本文から読む。

    claude は種類を構造で返さず、
    標準エラーや is_error の文にだけ書く。

    本文から種類を引くのはこの関数の一か所に限る。投げる地点が構造から決められる種類
    （タイムアウト、上限の超過、出力の形の不正）はここを通さない。
    """
    for status_pattern in _HTTP_STATUS_PATTERNS:
        match = status_pattern.search(text)
        if match is not None:
            if match.group(1) == "429":
                return FailureKind.RATE_LIMIT
            return FailureKind.OVERLOADED if match.group(1) == "529" else FailureKind.SERVER_ERROR
    for pattern, kind in _KEYWORD_KINDS:
        if pattern.search(text):
            return kind
    return FailureKind.OTHER


def _cost(payload: dict[str, Any] | None) -> Cost:
    if payload is not None and "total_cost_usd" in payload:
        return Measured(float(payload["total_cost_usd"]))
    return Unreported()


class ClaudeInvocationError(Exception):
    def __init__(
        self,
        cause: str,
        record: InvocationRecord,
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


def _schema_for_cli(schema: dict[str, Any]) -> dict[str, Any]:
    # Claude Code CLI の検証器は Draft 2020-12 のメタスキーマを解決できず、
    # $schema を含むスキーマを不正として起動に失敗する（2.1.292 で実測）
    return {key: value for key, value in schema.items() if key not in ("$schema", "$id")}


# gh が資格情報として読む変数（gh help environment の一覧）。レビュアーは読み取りの道具だけを
# 持つ前提だが、書き込みの資格情報をプロセスに存在させないため、子の環境から除く。
REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    }
)


CLAUDEAI_MCP_SWITCH = "ENABLE_CLAUDEAI_MCP_SERVERS"
NONESSENTIAL_TRAFFIC_SWITCH = "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"


def reviewer_environment(
    parent: dict[str, str] | os._Environ[str] | None = None,
) -> dict[str, str]:
    source = os.environ if parent is None else parent
    # Windows の環境変数名は大文字小文字を区別しないため、大文字にそろえて照らす。
    environment = {
        name: value
        for name, value in source.items()
        if name.upper() not in REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES
    }
    # claude.ai のコネクタを子の起動に載せない。--strict-mcp-config と二重に止める
    environment[CLAUDEAI_MCP_SWITCH] = "false"
    # 使用の統計、誤りの報告、自動の更新などの付随の送信を止める。利用者の環境に別の値があっても
    # 1 にする。止めていると文書（docs/data-destinations.md）に書くため、値を利用者に委ねない
    environment[NONESSENTIAL_TRAFFIC_SWITCH] = "1"
    return environment


MCP_RESOURCE_TOOLS = ("ReadMcpResourceTool", "ListMcpResourcesTool")


def claude_binary() -> str:
    """起動する claude の実行ファイル。JIGANE_CLAUDE_BINARY を読む唯一の口。

    空や未設定は "claude"。確かめでは偽の claude（scripts/fake_claude.py）に向け、
    本物の起動を構造で防ぐ。
    """
    return os.environ.get(CLAUDE_BINARY_ENVIRONMENT) or "claude"


# 秘密のファイル名の唯一の一覧。claude の Read の拒否、反証役が示した位置の抜粋、
# レビューの対象の単位が引く。一覧に無い名前の秘密は拒めない
SECRET_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "*.p12",
    "credentials.json",
    "id_ecdsa",
    "id_dsa",
    "*.pfx",
    "*.jks",
    "*.keystore",
    ".git-credentials",
    ".htpasswd",
    "*.tfvars",
    "terraform.tfstate",
    ".dockerconfigjson",
)


def is_secret_file(path: str) -> bool:
    """パスの末尾の名前が SECRET_FILE_PATTERNS に当たるか。Read の拒否と同じ名前の見方。"""
    # 大文字小文字を区別しない FS では `.ENV` も同じファイルになるため、小文字にそろえる
    name = PurePosixPath(path.replace("\\", "/")).name.lower()
    return any(fnmatchcase(name, pattern.lower()) for pattern in SECRET_FILE_PATTERNS)


def secret_read_denials() -> list[str]:
    """作業ディレクトリの直下と任意の深さの両方で、秘密のファイルの Read を拒む規則。"""
    return [
        f"Read({prefix}{pattern})" for pattern in SECRET_FILE_PATTERNS for prefix in ("./", "**/")
    ]


def build_command(invocation: Invocation) -> list[str]:
    # プロンプトは引数に入れない。標準入力で渡す（run を参照）
    command = [
        claude_binary(),
        "--restricted",
        # --restricted だけでは作業ディレクトリの .env を Read で読めるため、deny で塞ぐ
        "--settings",
        json.dumps({"permissions": {"deny": secret_read_denials()}}),
        "-p",
        "--tools",
        ",".join(invocation.tools),
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(_schema_for_cli(invocation.json_schema), ensure_ascii=False),
        "--model",
        invocation.model,
        "--max-budget-usd",
        str(invocation.budget.cli_usd),
    ]
    if invocation.effort is not None:
        command += ["--effort", invocation.effort]
    if invocation.max_turns is not None:
        command += ["--max-turns", str(invocation.max_turns)]
    # 利用者の設定と claude.ai のコネクタの MCP を混ぜない。混ざると、その道具の説明が
    # 文脈に入り、遅い接続でキャッシュが外れて金額が膨らむ。渡した設定だけを読ませる
    command.append("--strict-mcp-config")
    if invocation.mcp_config is not None:
        command += ["--mcp-config", str(invocation.mcp_config)]
        if invocation.allowed_mcp_tools:
            command += ["--allowedTools", ",".join(invocation.allowed_mcp_tools)]
    return command


def failure_record(payload: dict[str, Any] | None, limit_usd: float) -> InvocationRecord:
    """失敗した起動の記録。

    金額が報告されない失敗（タイムアウト、出力が JSON でない）は、上限の額で代える。
    使った額の上界で、過少に数えない。上限は最後の一往復の分だけ超えうるので、
    代用の額は厳密な上界ではない。
    結果が往復ゼロ、トークンゼロで額も無いときだけは、API を呼ぶ前に止まったので課金なしとする。
    """
    record = parse_record(payload or {}, limit_usd)
    if _stopped_before_api(payload, record):
        return replace(record, cost=NotCharged())
    return replace(record, cost=with_limit(record.cost, limit_usd))


def _stopped_before_api(payload: dict[str, Any] | None, record: InvocationRecord) -> bool:
    # 欄が無い結果は往復の有無が分からないので含めない。num_turns を明示するものだけを見る
    if payload is None or "total_cost_usd" in payload or payload.get("num_turns") != 0:
        return False
    return (
        record.input_tokens
        + record.cache_read_input_tokens
        + record.cache_creation_input_tokens
        + record.output_tokens
        == 0
    )


def parse_record(payload: dict[str, Any], limit_usd: float) -> InvocationRecord:
    usage = payload.get("usage") or {}
    return InvocationRecord(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
        cost=_cost(payload),
        duration_ms=payload.get("duration_ms", 0),
        num_turns=payload.get("num_turns", 0),
        is_error=bool(payload.get("is_error", False)),
        terminal_reason=payload.get("terminal_reason"),
        permission_denials=len(payload.get("permission_denials") or []),
        max_budget_usd=limit_usd,
    )


FAILURE_REASON_LIMIT = 500


def _tail(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= FAILURE_REASON_LIMIT:
        return flat
    return "…" + flat[-FAILURE_REASON_LIMIT:]


def _failure_reason(completed: subprocess.CompletedProcess[str]) -> str:
    """失敗した起動の理由。標準エラーの末尾に、標準出力の末尾を添える。

    --output-format json の claude は、失敗の理由を標準エラーではなく標準出力の JSON
    （subtype と result）に書くことがある。読めればその二つを、読めなければ生の末尾を添える。
    """
    parts = [_tail(completed.stderr)] if completed.stderr.strip() else []
    stdout = completed.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout, strict=False)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            reasons = [str(payload[key]) for key in ("subtype", "result") if payload.get(key)]
            parts.append(_tail(" ".join(reasons)) if reasons else _tail(stdout))
        else:
            parts.append(_tail(stdout))
    return " / ".join(parts) if parts else "標準エラーと標準出力が空です"


def _stdout_payload(stdout: str) -> dict[str, Any] | None:
    # JSON として読めない出力は subtype も金額も無い扱いにし、本文での分類に任せる
    try:
        payload = json.loads(stdout, strict=False)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _is_error_kind(subtype: object, text: str) -> FailureKind:
    # 費用と往復の上限は subtype の値で決まる。それ以外の理由は本文から読むしかない
    if subtype == "error_max_budget_usd":
        return FailureKind.BUDGET_EXCEEDED
    if subtype == "error_max_turns":
        return FailureKind.TURNS_EXCEEDED
    return classify_failure_text(text)


def run(
    invocation: Invocation,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_claude_resolved,
    timeout_seconds: int = 900,
) -> InvocationResult:
    # 親の環境を受け継ぐ。ログイン済みの認証と CLAUDE_CODE_OAUTH_TOKEN を子に渡すため。
    # ただし GitHub の資格情報は除く（REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES）。
    # プロンプトは標準入力で渡す。差分や PR の本文を含むプロンプトが、Windows の
    # コマンドラインの長さの上限（約 32,767 文字）を超えうるため。-p に位置引数が
    # 無ければ標準入力を読むことを、claude 2.1.292 で実測した。
    try:
        completed = runner(
            build_command(invocation),
            input=invocation.prompt,
            cwd=invocation.working_directory,
            env=reviewer_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as error:
        command = build_command(invocation)
        failure = launch_failure(command[:1], invocation.working_directory, error)
        if failure is None:
            raise
        raise failure from error
    except subprocess.TimeoutExpired as error:
        raise ClaudeInvocationError(
            f"claude が {timeout_seconds} 秒でタイムアウトしました",
            failure_record(None, invocation.budget.limit_usd),
            FailureKind.TIMEOUT,
        ) from error
    if completed.returncode != 0:
        reason = _failure_reason(completed)
        failed_payload = _stdout_payload(completed.stdout)
        raise ClaudeInvocationError(
            f"claude の終了コードが {completed.returncode} でした: {reason}",
            # 上限での失敗は終了コードが 0 でないことがあり、標準出力の結果に使った額が入る
            failure_record(failed_payload, invocation.budget.limit_usd),
            _is_error_kind(
                None if failed_payload is None else failed_payload.get("subtype"), reason
            ),
        )
    try:
        # 出力の文字列に制御文字が生で入りうるため strict=False で読む。
        payload = json.loads(completed.stdout, strict=False)
    except json.JSONDecodeError as error:
        raise ClaudeInvocationError(
            f"出力が JSON ではありません: {error}",
            failure_record(None, invocation.budget.limit_usd),
            FailureKind.BAD_OUTPUT,
        ) from error
    if not isinstance(payload, dict):
        raise ClaudeInvocationError(
            "出力が JSON オブジェクトではありません",
            failure_record(None, invocation.budget.limit_usd),
            FailureKind.BAD_OUTPUT,
        )
    record = parse_record(payload, invocation.budget.limit_usd)
    if record.is_error:
        # subtype と result に失敗の理由（過負荷、上限の超過など）が書かれる
        reasons = [str(payload[key]) for key in ("subtype", "result") if payload.get(key)]
        detail = f": {_tail(' '.join(reasons))}" if reasons else ""
        raise ClaudeInvocationError(
            f"claude が is_error を返しました{detail}",
            failure_record(payload, invocation.budget.limit_usd),
            _is_error_kind(payload.get("subtype"), " ".join(reasons)),
        )
    structured_output = payload.get("structured_output")
    if not isinstance(structured_output, dict):
        raise ClaudeInvocationError(
            "structured_output がありません",
            failure_record(payload, invocation.budget.limit_usd),
            FailureKind.BAD_OUTPUT,
        )
    return InvocationResult(structured_output=structured_output, record=record)


def normalize_path(path: str, working_directory: Path) -> str | None:
    # CLI は絶対パスを返すことがある。作業ディレクトリの外は
    # 指摘から外すため None にする。
    windows = PureWindowsPath(path)
    if windows.drive:
        # ドライブ名の付いたパス（`C:\x`、`C:/x`）や UNC は PurePosixPath では絶対と
        # 見なされず、相対のまま作業ディレクトリの外を指しうる。作業ディレクトリの
        # 配下のときだけ相対へ直す。
        windows_bases = {
            PureWindowsPath(str(working_directory)),
            PureWindowsPath(str(working_directory.resolve())),
        }
        windows_relative = next(
            (windows.relative_to(base) for base in windows_bases if windows.is_relative_to(base)),
            None,
        )
        if windows_relative is None:
            return None
        path = windows_relative.as_posix()
    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute():
        # macOS の /tmp と /private/tmp のように、CLI が返すパスと作業ディレクトリの
        # 表記が symlink の解決の有無で食い違うため、両方の表記と照らす。
        bases = {
            PurePosixPath(working_directory.as_posix()),
            PurePosixPath(working_directory.resolve().as_posix()),
        }
        relative = next(
            (candidate.relative_to(base) for base in bases if candidate.is_relative_to(base)),
            None,
        )
        if relative is None:
            return None
        candidate = relative
    parts: list[str] = []
    for part in candidate.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None
    return PurePosixPath(*parts).as_posix()


def estimate_input_tokens(text_bytes: int) -> int:
    # 日本語、英語、コードが混じるため係数 3 は仮置きである。後の段で実測して直す。
    return math.ceil(text_bytes / 3)


# モデルごとの入力の単価（USD / 100 万トークン）。出所は Anthropic の第一者の API の価格
# （2026-10-06 時点）。表に無いモデルには、表の最高の単価を仮に当てる。
INPUT_USD_PER_MILLION_TOKENS: dict[str, float] = {
    "claude-sonnet-5-5": 2.00,
    "claude-opus-5-5": 4.00,
    "claude-haiku-5-5": 0.10,
    "claude-fable-5-1": 10.00,
}


@dataclass(frozen=True)
class InputCost:
    usd: float
    price_per_million: float
    # 表に無いモデルに、表の最高の単価を当てたか
    assumed_price: bool


def input_cost_usd(model: str, input_tokens: int) -> InputCost:
    price = INPUT_USD_PER_MILLION_TOKENS.get(model)
    assumed = price is None
    if price is None:
        # 表の手入れに安全弁が左右されないよう、安全側に倒して最高の単価で見積もる
        price = max(INPUT_USD_PER_MILLION_TOKENS.values())
    return InputCost(input_tokens * price / 1_000_000, price, assumed)


def assumed_price_notice(model: str, cost: InputCost) -> str:
    return (
        f"注: {model} は単価の表に無いため、表の最高の単価（{cost.price_per_million:g} USD / "
        "100 万トークン）で見積もりました"
    )


# CLI は --max-budget-usd を往復の合間にしか確かめず、最後の一往復で文脈の全部を読み直す分だけ
# 上限を超える（micro の記録 55 件中 36 件が上限 0.5 USD を超えた）。そこで CLI へ渡す値は、
# 上限から「プロンプト全体をもう一度入力する費用」を引いた値にする。プロンプトの大きさは
# 道具で読む分を含まない下限なので、引く額は不足しうるが、超過の大半を占める読み直しは覆える。
# 引いた値が上限のこの割合を割るときは、予算がほとんど残らず走らせる意味が無いので、
# 走らせる前の検査で止める。
MIN_CLI_BUDGET_FRACTION = 0.5


def cli_budget_usd(model: str, prompt_tokens: int, max_budget_usd: float) -> float | None:
    """CLI に渡す上限。引いた値が下限を割るときは None で、その起動は走らせない。"""
    budget = max_budget_usd - input_cost_usd(model, prompt_tokens).usd
    if budget < max_budget_usd * MIN_CLI_BUDGET_FRACTION:
        return None
    return budget
