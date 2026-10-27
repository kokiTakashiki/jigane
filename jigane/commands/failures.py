"""失敗を、種類、見出し、理由、次の操作で表す。

種類は jigane が定義する例外の型から決まる。型と見出しと次の操作の対応は `KINDS` の表が持つ。
新しい例外の型を足すときは、ここにも行を足す。足し忘れた型は `unexpected` として表示される。
`CommandError` だけは実行ファイルの名前でも分かれ、`COMMAND_KINDS`（失敗した実行ファイルごと）と
`MISSING_TOOL_KIND`（実行ファイルが無いとき）が見出しと次の操作を持つ。
"""

import json
from dataclasses import dataclass
from enum import StrEnum

from jigane.commands.consent import ConsentDeclinedError, ConsentUnavailableError
from jigane.commands.terms_consent import (
    ACCEPT_TERMS_OPTION,
    TermsConsentDeclinedError,
    TermsConsentUnavailableError,
)
from jigane.findings.records import HistoryCorruptError
from jigane.flaky.catalog import CatalogError
from jigane.git.process import CommandError
from jigane.groups.definitions import GROUP_BY_ID
from jigane.languages.grammars import GrammarError
from jigane.languages.registry import LanguagePluginUnavailableError
from jigane.llm_runs.claude import ClaudeInvocationError
from jigane.reviews.lifecycle import ReviewError
from jigane.sequence.roots import SequenceError
from jigane.skills.mcp import SecurityConfigError
from jigane.skills.pipeline import DiscoveryError, InputBudgetError
from jigane.storage.review_error import ReviewExistsError
from jigane.storage.store import LockTimeoutError, StoredFileInUseError

UNEXPECTED = "unexpected"
# 子のプロセスの標準エラーを理由へ載せる長さの上限。失敗の原因は末尾に出やすいので末尾を残す
STDERR_LIMIT = 800


@dataclass(frozen=True)
class Failure:
    kind: str
    title: str
    reason: str
    action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "title": self.title,
            "reason": self.reason,
            "action": self.action,
        }


class BlockerKind(StrEnum):
    SECURITY_CONFIG = "security_config"


@dataclass(frozen=True)
class Blocker(Failure):
    """実行の前に分かっている、実行を止める前提の欠け。形は実行の失敗と同じで、種類だけを列挙型に絞る。"""

    kind: BlockerKind


@dataclass(frozen=True)
class ExistingReviewFailure(Failure):
    """作ろうとしたレビューが既にあるという失敗。開き先に使える識別子を `review` で足す。"""

    review: str

    def to_dict(self) -> dict[str, str]:
        return {**super().to_dict(), "review": self.review}


REVIEW_EXISTS_KIND = "review_exists"


@dataclass(frozen=True)
class Kind:
    kind: str
    title: str
    action: str


# 先に当たった行を採る。サブクラスの関係にある型は、サブクラスを先に置く
KINDS: tuple[tuple[type[BaseException], Kind], ...] = (
    (
        TermsConsentDeclinedError,
        Kind(
            "terms_declined",
            "取得しませんでした",
            "取得するなら、もう一度 `jigane languages fetch` を実行して確認に y と答えてください",
        ),
    ),
    (
        ConsentDeclinedError,
        Kind(
            "consent_declined",
            "実行しませんでした",
            "実行するなら、もう一度実行して確認に y と答えてください",
        ),
    ),
    (
        TermsConsentUnavailableError,
        Kind(
            "terms_unavailable",
            "取得しませんでした",
            "端末から `jigane languages fetch` を実行するか、"
            f"規約を読んだうえで `{ACCEPT_TERMS_OPTION}` を付けてください",
        ),
    ),
    (
        ConsentUnavailableError,
        Kind(
            "consent_unavailable",
            "実行しませんでした",
            "端末から jigane run を実行してください",
        ),
    ),
    (
        ReviewExistsError,
        Kind(
            REVIEW_EXISTS_KIND,
            "レビューが既にあります",
            "既にあるレビューを開いてください。"
            "作り直すなら refresh するか、別の名前を付けてください",
        ),
    ),
    (
        ReviewError,
        Kind(
            "review",
            "レビューを扱えませんでした",
            "理由に出ている識別子、テンプレートの名前、対象のパスを直して、もう一度実行してください。"
            "`jigane list` でレビューの一覧を確かめられます",
        ),
    ),
    (
        HistoryCorruptError,
        Kind(
            "corrupt_history",
            "レビューの履歴が壊れています",
            "理由に出ている findings_history.json を直すか退避してから、もう一度実行してください",
        ),
    ),
    (
        LanguagePluginUnavailableError,
        Kind(
            "language_plugin_unavailable",
            "外の言語を使えません",
            "理由に出ている手順で、使えない言語を外すか、languages.json を直して、"
            "もう一度実行してください",
        ),
    ),
    (
        GrammarError,
        Kind(
            "grammar",
            "文法を使えません",
            "`jigane languages fetch` を走らせてください。"
            "開発の木では `make setup` を走らせてください",
        ),
    ),
    (
        CommandError,
        Kind(
            "command",
            "外部コマンドが失敗しました",
            "理由に出ている標準エラーを読み、同じコマンドを手元で実行して原因を確かめてください",
        ),
    ),
    (
        ClaudeInvocationError,
        Kind(
            "claude",
            "Claude Code CLI の呼び出しに失敗しました",
            "`claude --version` で CLI を起動できるか、ログインしているかを確かめて、"
            "もう一度実行してください。タイムアウトのときは、対象を絞ってから実行してください",
        ),
    ),
    (
        LockTimeoutError,
        Kind(
            "lock",
            "別の jigane がこのレビューを使っています",
            "実行中の jigane が終わるのを待つか、止めてから、もう一度実行してください",
        ),
    ),
    (
        StoredFileInUseError,
        Kind(
            "file_in_use",
            "保存物を置き換えられませんでした",
            "理由に出ているファイルを開いている jigane の show やエディタなどを閉じてから、"
            "もう一度実行してください",
        ),
    ),
    (
        SecurityConfigError,
        Kind(
            BlockerKind.SECURITY_CONFIG.value,
            "セキュリティ解析の設定が足りません",
            "理由に出ている mcp.json に、"
            "`mcpServers` と --mcps に指定した MCP サーバの起動の設定を"
            "書くか、--mcps を外してください。--mcp-guide を渡したなら、"
            "その TOML の形と名前が --mcps と合っているかも確かめて、もう一度実行してください",
        ),
    ),
    (
        InputBudgetError,
        Kind(
            "input_budget",
            "資料が大きすぎて実行を止めました",
            "理由に挙げた大きい節を小さくするか、対象の範囲を絞ってください。"
            "上限を上げるときは --max-budget-usd-per-call を指定してください",
        ),
    ),
    (
        DiscoveryError,
        Kind(
            "discovery",
            "発見役が失敗しました",
            "もう一度実行してください。続くときは、理由を添えて報告してください",
        ),
    ),
    (
        CatalogError,
        Kind(
            "catalog",
            "flaky の目録が不正",
            "理由に出ている目録のファイルを直してください。同梱の目録なら、理由を添えて報告してください",
        ),
    ),
    (
        SequenceError,
        Kind(
            "sequence",
            "シーケンス図を作れませんでした",
            "理由に出ている引数を直して、もう一度実行してください",
        ),
    ),
    (
        json.JSONDecodeError,
        Kind(
            "corrupt_state",
            "保存したレビューの状態が壊れています",
            "該当のファイルを消すか、jigane refresh で作り直してください",
        ),
    ),
    (
        ValueError,
        Kind(
            "invalid_input",
            "入力が不正",
            "理由に出ている値を直して、もう一度実行してください",
        ),
    ),
    (
        OSError,
        Kind(
            "filesystem",
            "ファイルを読み書きできませんでした",
            "理由に出ているパスが在ること、読み書きの権限があること、"
            "ディスクに空きがあることを確かめてください",
        ),
    ),
)

UNEXPECTED_KIND = Kind(
    UNEXPECTED,
    "想定外の失敗",
    "理由と、直前に行った操作を添えて報告してください",
)

# 実行ファイルごとの、CommandError の見出しと次の操作。表に無い実行ファイルは KINDS の行を使う
COMMAND_KINDS: dict[str, Kind] = {
    "gh": Kind(
        "github",
        "GitHub から取得できませんでした",
        "`gh auth status` でログインを確かめ、URL が開けるか確かめてください。"
        "存在しない PR や、権限の無いリポジトリでも失敗します",
    ),
    "git": Kind(
        "git",
        "git の操作が失敗しました",
        "理由に出ている標準エラーを読み、同じ git コマンドを手元で実行して確かめてください",
    ),
}


# 実行ファイルが無いときの見出し。次の操作は実行ファイルごとに `_missing_tool_action` が作る
MISSING_TOOL_KIND = Kind("missing_tool", "必要なコマンドが見つかりません", "")

# 実行ファイルごとの、入れた後の操作を含む次の操作。表に無い実行ファイルは名前だけで案内する
MISSING_TOOL_ACTIONS = {
    "gh": "gh をインストールして PATH に通し、`gh auth login` でログインしてください",
    "git": "git をインストールして PATH に通してください",
    "claude": "Claude Code をインストールして PATH に通し、ログインしてください",
}


def _missing_tool_action(executable: str) -> str:
    return (
        MISSING_TOOL_ACTIONS.get(executable)
        or f"{executable} をインストールして PATH に通してください"
    )


def _lookup(error: BaseException) -> Kind:
    for error_type, kind in KINDS:
        if isinstance(error, error_type):
            return kind
    return UNEXPECTED_KIND


def _command_reason(error: CommandError) -> str:
    stderr = error.stderr.strip()
    if len(stderr) > STDERR_LIMIT:
        stderr = "…" + stderr[-STDERR_LIMIT:]
    head = f"コマンドが失敗しました: {' '.join(error.command)}"
    return f"{head}\n{stderr}" if stderr else head


def describe(error: BaseException) -> Failure:
    """例外を、利用者に見せる失敗へ写す。"""
    if isinstance(error, CommandError):
        executable = error.command[0] if error.command else ""
        if error.missing_executable:
            kind = MISSING_TOOL_KIND
            return Failure(
                kind.kind, kind.title, _command_reason(error), _missing_tool_action(executable)
            )
        kind = COMMAND_KINDS.get(executable) or _lookup(error)
        return Failure(kind.kind, kind.title, _command_reason(error), kind.action)
    kind = _lookup(error)
    # GrammarError は案内を別に持ち、次の操作と重ねないため問題の文だけを理由にする
    text = error.problem if isinstance(error, GrammarError) else str(error)
    reason = "\n".join([text or type(error).__name__, *getattr(error, "__notes__", [])])
    if kind is UNEXPECTED_KIND:
        reason = f"{type(error).__name__}: {reason}"
    title = kind.title
    if isinstance(error, DiscoveryError):
        title = f"{GROUP_BY_ID[error.group].label}の{title}"
    if isinstance(error, ReviewExistsError):
        return ExistingReviewFailure(kind.kind, title, reason, kind.action, str(error.review_id))
    return Failure(kind.kind, title, reason, kind.action)


def blockers() -> tuple[Blocker, ...]:
    """群の実行を止める前提の欠けの一覧。見積もりの JSON と CLI の文面はここから引く。

    文面を `describe` の表から取るので、同じ failures.py に置くと import が循環しない。
    """
    # 前提の欠けは今のところ無い。セキュリティの MCP は --mcps を指定したときだけ要り、
    # その確かめは起動の前の run_security が行う
    return ()
