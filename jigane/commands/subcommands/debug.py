"""`jigane debug`: 利用者と開発者が jigane の内部を確かめる口。出力は暫定で、版で変わりうる。

`pip debug` に倣い、確かめる対象ごとに action を足す。
"""

import argparse
import json
import sys
from decimal import Decimal
from typing import NoReturn, TypedDict

from jigane.commands import debug_prompt, debug_stats, failures
from jigane.commands.budget_argument import positive_finite_usd
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_ESTIMATE_OVER_LIMIT, EXIT_OK, EXIT_USAGE
from jigane.commands.group_argument import add_group_argument
from jigane.commands.mcp_argument import add_mcps_argument
from jigane.commands.output import print_lines
from jigane.commands.subcommands import run as run_subcommand
from jigane.groups.definitions import Effort
from jigane.llm_runs import codex
from jigane.repository_docs.settings import settings_path
from jigane.storage import store
from jigane.storage.agent_selection import Agent


class DebugRunAdded(TypedDict):
    """debug run が --json の本体へ足す鍵。run の鍵と重ならない名前にする。"""

    max_usd: float
    estimated_usd: float | None
    experimental: bool


class DebugRunJson(run_subcommand.RunJson, DebugRunAdded):
    """debug run --json の本体。"""


class DebugStoppedJson(DebugRunAdded):
    """見込みが上限を超えて起動しなかったときの --json の本体。"""

    stopped: str


DEBUG_RUN_JSON_KEYS = frozenset(DebugRunAdded.__annotations__)
CODEX_DEBUG_MAX_USD = Decimal(1)
PROVISIONAL_NOTICE = "出力は暫定で、jigane の版で変わることがあります"
RUN_NOTICE = (
    "実験的な口です。AI の道具がこれを使うときは、`jigane estimate` の見積もりの額を"
    "先に人へ見せて、同意を得てから走らせてください。"
    f"同意の問いを飛ばして `jigane run` と同じ処理を走らせます。{PROVISIONAL_NOTICE}"
)


class _UsageExitParser(argparse.ArgumentParser):
    """引数の誤りを終了コード 64 で止める。argparse 既定の 2 は処理の失敗と区別できない。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _max_usd(text: str) -> Decimal:
    # Claude の入力検査を保ち、Codex の見積もりとの照合には元の十進表現を使う。
    positive_finite_usd(text)
    return Decimal(text)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    debug_parser = subparsers.add_parser(
        "debug", help=f"jigane の内部を確かめます。{PROVISIONAL_NOTICE}"
    )
    actions = debug_parser.add_subparsers(
        dest="action", required=True, metavar="<action>", parser_class=_UsageExitParser
    )

    errors_parser = actions.add_parser(
        "errors", help=f"失敗の種類を一覧します。{PROVISIONAL_NOTICE}"
    )
    errors_parser.add_argument("--json", action="store_true")
    errors_parser.set_defaults(handler=run_errors)

    debug_prompt.add_prompt_parser(actions, PROVISIONAL_NOTICE)
    run_parser = actions.add_parser(
        "run",
        help=RUN_NOTICE,
        description=RUN_NOTICE,
        epilog=(
            "Codex CLI は保存済みの ChatGPT ログインを使います。"
            "Codex の非対話実行では MCP を使えません。"
            "終了コード: --yes と --max-usd の片方が無いか --max-usd の値が不正なら 64、"
            "上限の事前判定で止まれば 2、実行後は run と同じ 0、2、3。"
        ),
    )
    run_parser.add_argument("review")
    add_group_argument(run_parser)
    run_parser.add_argument(
        "--yes",
        action="store_true",
        help="--max-usd が必須。レビュー資料の送信に同意します",
    )
    run_parser.add_argument(
        "--max-usd",
        type=_max_usd,
        default=None,
        help=(
            "実行前に指定した群の見込みと照合します。"
            f"Codex は {CODEX_DEBUG_MAX_USD:g} USD 以下です"
        ),
    )
    run_parser.add_argument("--effort", type=Effort, choices=list(Effort), default=None)
    run_parser.add_argument(
        "--expect-agent", type=Agent, choices=list(Agent), help=argparse.SUPPRESS
    )
    add_mcps_argument(run_parser)
    run_parser.add_argument("--json", action="store_true")
    # `jigane run` の準備の処理を共有するため、受けない引数は既定値で埋める
    run_parser.set_defaults(handler=run_run, model=None, max_budget_usd_per_call=None)
    stats_parser = actions.add_parser(
        "stats", help=f"置き場の全レビューの数と札をまとめます。{PROVISIONAL_NOTICE}"
    )
    debug_stats.add_arguments(stats_parser, PROVISIONAL_NOTICE)
    review_id_parser = actions.add_parser(
        "review-id", help=f"レビューの識別子の形を示します。{PROVISIONAL_NOTICE}"
    )
    review_id_parser.add_argument("--json", action="store_true")
    review_id_parser.set_defaults(handler=run_review_id)
    paths_parser = actions.add_parser(
        "paths", help=f"置き場のパスと根の検査の結果を示します。{PROVISIONAL_NOTICE}"
    )
    paths_parser.add_argument("--json", action="store_true")
    paths_parser.set_defaults(handler=run_paths)


def run_errors(args: argparse.Namespace, context: Context) -> int:
    del context
    kinds = failures.all_kinds()
    if args.json:
        body = {
            "fields": list(failures.failure_fields()),
            "kinds": [
                {
                    "kind": item.kind,
                    "description": item.title,
                    "extra_fields": list(failures.extra_fields(item.kind)),
                }
                for item in kinds
            ],
        }
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return EXIT_OK
    print_lines([f"{item.kind}\t{item.title}" for item in kinds] + [PROVISIONAL_NOTICE])
    return EXIT_OK


def clashing_json_keys() -> list[str]:
    """debug run が足す鍵のうち、run --json の鍵と重なるもの。"""
    return sorted(DEBUG_RUN_JSON_KEYS & run_subcommand.RUN_JSON_KEYS)


def added_json(max_usd: float, estimated_usd: float) -> DebugRunAdded:
    return DebugRunAdded(max_usd=max_usd, estimated_usd=estimated_usd, experimental=True)


def stopped_json(added: DebugRunAdded) -> DebugStoppedJson:
    return DebugStoppedJson(**added, stopped="estimate_exceeds_max_usd")


def debug_run_json(body: run_subcommand.RunJson, added: DebugRunAdded) -> DebugRunJson:
    return DebugRunJson(**body, **added)


def run_run(args: argparse.Namespace, context: Context) -> int:
    if error := _run_argument_error(args):
        print(f"jigane: {error}", file=sys.stderr)
        return EXIT_USAGE
    # 群の実行より前に確かめる。重なったまま走らせると、実行の後に本体を組めず費用だけが残る
    if clash := clashing_json_keys():
        print(
            f"jigane: debug run の --json の鍵が run と重なっています: {clash}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    is_codex = args.agent is Agent.CODEX
    if is_codex and args.max_usd > CODEX_DEBUG_MAX_USD:
        return _stop_above_codex_limit(args)
    prepared = run_subcommand.prepare_run(args, context)
    return _run_estimated(args, prepared, is_codex=is_codex)


def _run_estimated(
    args: argparse.Namespace, prepared: run_subcommand.PreparedRun, *, is_codex: bool
) -> int:
    expectation = prepared.plan.expectation
    if expectation is None:
        raise ValueError("費用の見込みがないため debug run を実行できません")
    estimated = expectation.total_usd
    max_usd = float(args.max_usd)
    added = added_json(max_usd, estimated)
    # nan を超過の側に倒すため、否定の形で比べる
    if not (estimated <= max_usd) or (is_codex and Decimal(str(estimated)) > args.max_usd):
        print(
            f"jigane: 群の見込み {estimated} USD が --max-usd {args.max_usd} USD を"
            "超えるので、起動しません",
            file=sys.stderr,
        )
        if args.json:
            run_subcommand.print_run_json(stopped_json(added))
        return EXIT_ESTIMATE_OVER_LIMIT
    if is_codex:
        codex.check_chatgpt_login()
    return _execute(prepared, added, as_json=args.json)


def _run_argument_error(args: argparse.Namespace) -> str | None:
    if not args.yes or args.max_usd is None:
        return "debug run は非対話のための口です。--yes と --max-usd <額> を両方渡してください"
    if args.agent == Agent.CODEX and (args.mcps or args.mcp_guide is not None):
        return codex.NO_DEBUG_MCP
    return None


def _stop_above_codex_limit(args: argparse.Namespace) -> int:
    print(
        "jigane: Codex の非対話実行には "
        f"{CODEX_DEBUG_MAX_USD:g} USD 以下の上限を指定してください。送信しません",
        file=sys.stderr,
    )
    if args.json:
        added = DebugRunAdded(max_usd=float(args.max_usd), estimated_usd=None, experimental=True)
        run_subcommand.print_run_json(DebugStoppedJson(**added, stopped="confirmation_required"))
    return EXIT_ESTIMATE_OVER_LIMIT


def _execute(prepared: run_subcommand.PreparedRun, added: DebugRunAdded, *, as_json: bool) -> int:
    result = run_subcommand.run_group(prepared.assembly, prepared.settings, plan=prepared.plan)
    report = run_subcommand.report_group_result(result, as_json=as_json)
    if as_json:
        run_subcommand.print_run_json(debug_run_json(report.body, added))
    return report.exit_code


def run_review_id(args: argparse.Namespace, context: Context) -> int:
    del context
    kinds = [
        {"kind": kind.value, "form": f"owner/repo{kind.separator}<キー>"}
        for kind in store.ReviewKind
    ]
    if args.json:
        # 識別子の長さの上限はコードに無いため、max_length の鍵は出さない
        body = {"pattern": store.full_identifier_pattern().pattern, "kinds": kinds}
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return EXIT_OK
    print_lines(
        [f"{item['kind']}\t{item['form']}" for item in kinds]
        + [f"全体の形: {store.full_identifier_pattern().pattern}", PROVISIONAL_NOTICE]
    )
    return EXIT_OK


def run_paths(args: argparse.Namespace, context: Context) -> int:
    del context
    # ensure_data_home は呼ばない。根を作らず、何も書かない
    home = store.data_home()
    check = store.check_data_home(home)
    settings = settings_path()
    if args.json:
        body = {
            "data_home": str(home),
            "data_home_check": {
                "exists": check.exists,
                "stops": check.stops,
                "kind": check.kind.value,
            },
            "repository_docs_settings": str(settings),
        }
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return EXIT_OK
    print_lines(
        [
            f"data_home\t{home}",
            f"data_home_check\t{check.kind.value}",
            f"repository_docs_settings\t{settings}",
        ]
    )
    return EXIT_OK
