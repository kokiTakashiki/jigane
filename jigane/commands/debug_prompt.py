"""`jigane debug prompt`: LLM を起動せずに、その群で起動するときのプロンプトの本文を出す。

本文はレビュー対象の本文を含むので、標準出力へ出すだけで、ファイルへは書かない。
"""

import argparse
import json
import sys
from dataclasses import replace
from typing import Any

from jigane.commands.budget_argument import add_max_budget_argument
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK, EXIT_USAGE
from jigane.commands.group_argument import add_group_argument
from jigane.commands.mcp_argument import add_mcps_argument, load_mcp_selection
from jigane.commands.output import print_lines
from jigane.llm_runs import codex
from jigane.llm_runs.defaults import resolve_run_defaults
from jigane.reviews.catalog import load_review
from jigane.reviews.resolution import resolve_review
from jigane.skills.assembly import AssemblyInput
from jigane.skills.launch_prompts import LaunchInput, LaunchPrompt, launch_prompts
from jigane.skills.review_docs import read_docs_for_review
from jigane.storage.agent_selection import Agent, load_agent

DEFAULT_PAGE_SIZE = 5
MAX_PAGE_SIZE = 20


class UsageError(ValueError):
    """起動の引数の誤り。終了コード 64 で返す。"""


def add_prompt_parser(actions: "argparse._SubParsersAction[Any]", notice: str) -> None:
    parser = actions.add_parser(
        "prompt", help=f"群で起動するときのプロンプトの本文を出します。{notice}"
    )
    parser.add_argument("review")
    add_group_argument(parser)
    add_max_budget_argument(parser)
    add_mcps_argument(parser)
    parser.add_argument("--page-size", type=page_size_argument, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--after", type=after_argument, default=0, metavar="MARK")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(handler=run_prompt, provisional_notice=notice)


def page_size_argument(text: str) -> int:
    # int() は符号、空白、下線、ASCII 以外の数字も受け入れるので、--after と同じ規則で先に絞る
    if not text.isascii() or not text.isdecimal():
        raise argparse.ArgumentTypeError(f"整数ではありません: {text!r}")
    size = int(text)
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise argparse.ArgumentTypeError(f"1 から {MAX_PAGE_SIZE} の整数です: {size}")
    return size


def after_argument(text: str) -> int:
    """印は次のページの先頭の位置。"""
    if not text.isascii() or not text.isdecimal():
        raise argparse.ArgumentTypeError(f"印が正しくありません: {text!r}")
    return int(text)


def page_of(
    entries: tuple[LaunchPrompt, ...], start: int, page_size: int
) -> tuple[tuple[LaunchPrompt, ...], str | None]:
    """次の印は、末尾まで出したら None。"""
    if start > len(entries):
        raise UsageError(f"--after の印が件数（{len(entries)}）を超えています: {start}")
    end = start + page_size
    return entries[start:end], (str(end) if end < len(entries) else None)


def record_of(entry: LaunchPrompt) -> dict[str, object]:
    return {"role": entry.role.value, "text": entry.text, "bytes": entry.size}


def run_prompt(args: argparse.Namespace, context: Context) -> int:
    del context  # プロンプトの組み立ては LLM を呼ばない
    uses_codex = load_agent() is Agent.CODEX
    if uses_codex:
        codex.check_options(max_budget_usd=args.max_budget_usd_per_call)
    mcp = load_mcp_selection(args.mcps, args.group, args.mcp_guide, codex_cli=uses_codex)
    review = load_review(resolve_review(args.review))
    assembly = AssemblyInput.load(review, docs=read_docs_for_review(review, args.group))
    budget = resolve_run_defaults(
        args.group, None, None, max_budget_usd=args.max_budget_usd_per_call
    ).budget
    if uses_codex:
        budget = codex.review_budget(budget)
    entries = launch_prompts(LaunchInput(assembly, args.group, mcp, budget))
    if mcp.reference_servers:
        boundary = codex.review_boundary(uses_mcp=True)
        entries = tuple(replace(entry, text=boundary + entry.text) for entry in entries)
    try:
        page, next_mark = page_of(entries, args.after, args.page_size)
    except UsageError as error:
        sys.stderr.write(f"jigane: {error}\n")
        return EXIT_USAGE
    if args.json:
        body = {"prompts": [record_of(entry) for entry in page], "next": next_mark}
        print_lines([json.dumps(body, ensure_ascii=False, indent=2)])
        return EXIT_OK
    lines = [f"--- {e.role.value} ({e.size} バイト)\n{e.text}" for e in page]
    print_lines([*lines, f"次: {next_mark}" if next_mark else "次: なし", args.provisional_notice])
    return EXIT_OK
