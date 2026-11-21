"""jigane の CLI の唯一の入口。

副コマンドは `subcommands/` に置かれたモジュールとして並ぶ。副コマンドの一覧は
`subcommands/` のモジュールそのものであり、別に対応表を持たない。
"""

import argparse
import importlib
import io
import json
import pkgutil
import sys
import traceback
from collections.abc import Callable, Sequence
from dataclasses import replace

from jigane import __version__
from jigane.commands import failures, subcommands
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_INTERRUPTED, EXIT_PROCESSING_FAILURE
from jigane.commands.language_guidance import detail_with_guidance
from jigane.commands.output import install_warning_handler
from jigane.languages import registry
from jigane.llm_runs import claude
from jigane.reviews.catalog import collapse_skipped_warnings
from jigane.storage.agent_selection import AgentSelectionError, load_agent, require_agent
from jigane.storage.store import data_home, require_private_data_home


class _StrictParser(argparse.ArgumentParser):
    """副コマンドの解釈器も、オプションの短縮形を受けない。"""

    def __init__(self, **kwargs: object) -> None:
        kwargs["allow_abbrev"] = False
        super().__init__(**kwargs)  # type: ignore[arg-type]


def build_parser() -> argparse.ArgumentParser:
    """`subcommands/` の各モジュールの `register` を呼んで、副コマンドを登録する。"""
    parser = _StrictParser(prog="jigane")
    parser.add_argument("--version", action="version", version=f"jigane {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for module_info in pkgutil.iter_modules(subcommands.__path__):
        module = importlib.import_module(f"{subcommands.__name__}.{module_info.name}")
        module.register(subparsers)
    return parser


def _write_streams_as_utf8() -> None:
    # Windows は標準出力と標準エラーの符号化を、パイプやリダイレクトでは cp1252 などの
    # ロケールの符号化にする。日本語の出力がそのまま UnicodeEncodeError になるため、
    # 入口で UTF-8 に揃える。argparse の --help も解釈の途中で書くので、解釈の前に行う
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() not in {
            "utf-8",
            "utf8",
        }:
            stream.reconfigure(encoding="utf-8")


def _wants_machine_output(args: argparse.Namespace) -> bool:
    # sequence は --json を持たず、--format json で機械が読む出力を選ぶ
    return bool(getattr(args, "json", False)) or getattr(args, "format", None) == "json"


# データの置き場へ書かないと確かめた副コマンド。ここに無い副コマンドは書くものとして、
# 根の権限を検査する。debug は run が群を走らせて書くので入れない
READ_ONLY_COMMANDS = frozenset(
    {
        "show",
        "list",
        "usage",
        "estimate",
        "export-reply",
        "export-report",
        "templates",
        "technology-list",
        "sequence",
        "diagrams",
        "flaky",
        "languages",
        "design",
    }
)


def _require_private_home_for_write(command: str, action: str | None) -> None:
    if command in READ_ONLY_COMMANDS:
        return  # 読み取りは根の権限が広くても止めない
    if command == "debug" and action == "paths":
        return  # 根の検査の結果を示す口なので、検査で止めると示せない
    home = data_home()
    # 根を作る副作用は持たせない。無ければ検査せず、書く側が作る
    if home.exists():
        require_private_data_home(home)


def _run_handler(args: argparse.Namespace, context: Context) -> int:
    if args.command == "list":
        # list は読めない記録を 1 件ずつ理由つきで見せる入口。畳むと理由が消える
        return int(args.handler(args, context))
    # 置き場の全件を引く箇所が、読めない記録ごとに警告を並べないよう、入口で一度だけ囲む
    with collapse_skipped_warnings():
        try:
            return int(args.handler(args, context))
        finally:
            # 端末でない標準出力は終了の時に書かれる。先に書き出して、まとめの警告を末尾に置く
            sys.stdout.flush()


def _prepare_command(args: argparse.Namespace) -> None:
    if args.command == "run" or (args.command == "debug" and args.action == "run"):
        args.agent = require_agent()
        expected = getattr(args, "expect_agent", None)
        if expected is not None and args.agent != expected:
            raise AgentSelectionError(f"承認時のエージェント {expected} から選択が変わりました")
    if args.command == "estimate":
        args.agent = load_agent()
    if args.command == "select-agent":
        # 言語の状態を読まず、保存する場合だけ保存側で根の権限を検査する
        return
    if args.command != "languages":
        # 言語の設定が壊れていても languages から修復できるようにする
        registry.require_ready(detail_with_guidance)
    _require_private_home_for_write(args.command, getattr(args, "action", None))


def main(
    argv: Sequence[str] | None = None,
    *,
    invoke: claude.Invoke = claude.run,
    is_terminal: Callable[[], bool] | None = None,
    read_answer: Callable[[str], str] | None = None,
) -> int:
    _write_streams_as_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_PROCESSING_FAILURE
    install_warning_handler()
    try:
        _prepare_command(args)
        context = Context(invoke=invoke)
        if is_terminal is not None:
            context = replace(context, is_terminal=is_terminal)
        if read_answer is not None:
            context = replace(context, read_answer=read_answer)
        return _run_handler(args, context)
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as error:  # noqa: BLE001 最上位の受け口。型の分からない失敗を報告に回す
        failure = failures.describe(error)
        if failure.kind == failures.UNEXPECTED:
            # 型が表に無い失敗は、原因を辿れるよう、人が読む標準エラーに経路も残す
            traceback.print_exc()
        print(f"jigane: {failure.reason}", file=sys.stderr)
        if failure.action:
            print(f"次の操作: {failure.action}", file=sys.stderr)
        if _wants_machine_output(args):
            print(json.dumps({"error": failure.to_dict()}, ensure_ascii=False))
        return EXIT_PROCESSING_FAILURE
