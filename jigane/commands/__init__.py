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
from jigane.commands.output import install_warning_handler
from jigane.languages import registry
from jigane.llm_runs import claude


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
        if args.command == "languages":
            # 使えない外の言語があっても、languages の一覧と disable で直せるようにする。
            # ここで止めると、直す手段そのものが塞がれる
            pass
        else:
            # 言語を使わない副コマンドも止める。登録は副コマンドの中のどこからでも引かれ、
            # 使うかどうかを副コマンドごとに見分ける手段が無いため、壊れた状態を一律に先に知らせる
            registry.require_ready()
        context = Context(invoke=invoke)
        if is_terminal is not None:
            context = replace(context, is_terminal=is_terminal)
        if read_answer is not None:
            context = replace(context, read_answer=read_answer)
        return int(args.handler(args, context))
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
