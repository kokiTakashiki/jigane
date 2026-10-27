"""`jigane technology-list`: 変更したファイルが使う技術の表を出す。"""

import argparse
import json
from typing import Any

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import load_review, resolve_review
from jigane.technology_list.build import build_model


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    technology = subparsers.add_parser(
        "technology-list", help="変更したファイルが使う技術の表を出します"
    )
    technology.add_argument("review")
    technology.add_argument(
        "--json", action="store_true", help="技術選定のモデルを JSON で出します"
    )
    technology.set_defaults(handler=run)


def _aligned(header: list[str], rows: list[list[str]]) -> str:
    lines = [header, *rows]
    widths = [max(len(row[i]) for row in lines) for i in range(len(header))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in lines
    )


def _section(section: dict[str, Any]) -> str:
    body = (
        _aligned(section["header"], section["rows"]) if section["rows"] else section["empty_text"]
    )
    return "\n".join([section["heading"], *section["notes"], body])


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 技術の表の組み立ては LLM を呼ばない
    review = load_review(resolve_review(args.review))
    model = build_model(review)
    if args.json:
        print(json.dumps(model, ensure_ascii=False, indent=2))
    else:
        print("\n\n".join(_section(section) for section in model["sections"]))
    return EXIT_OK
