"""`jigane sequence`: 変更した入口ごとのシーケンス図を出す。"""

import argparse
import json

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import load_review, resolve_review
from jigane.sequence.diagram import DEFAULT_DEPTH, to_mermaid
from jigane.sequence.review_diagrams import build_model
from jigane.sequence.roots import Side


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sequence = subparsers.add_parser("sequence", help="変更した入口ごとのシーケンス図を出します")
    sequence.add_argument("review")
    target = sequence.add_mutually_exclusive_group()
    target.add_argument("--root", help="この修飾名の宣言を根とする図だけを出します")
    target.add_argument("--region", help="この判断の箇所が現れる図を引きます")
    sequence.add_argument("--side", choices=[side.value for side in Side], default=Side.BOTH.value)
    sequence.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    sequence.add_argument("--full", action="store_true", help="経路の切り詰めをしません")
    sequence.add_argument("--include-tests", action="store_true")
    sequence.add_argument("--format", choices=["mermaid", "json"], default="mermaid")
    sequence.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 図の組み立ては LLM を呼ばない
    review = load_review(resolve_review(args.review))
    model = build_model(
        review,
        args.root,
        args.region,
        args.depth,
        full=args.full,
        include_tests=args.include_tests,
        side=Side(args.side),
    )
    if args.format == "json":
        print(json.dumps(model, ensure_ascii=False, indent=2))
        return EXIT_OK
    chunks = []
    for d in model["diagrams"]:
        # 変更前の図は side が both か before のときだけ付く。after の文面は従来と同じ
        if d.get("before") is not None:
            chunks.append(f"%% root: {d['root']} (before)\n{to_mermaid(d['before'])}")
        chunks.append(f"%% root: {d['root']} ({d['path']}:{d['line']})\n{to_mermaid(d)}")
    print("\n".join(chunks), end="")
    if args.region is not None and not model["diagrams"]:
        print(f"{args.region} は変更された入口から静的に到達しません")
    return EXIT_OK
