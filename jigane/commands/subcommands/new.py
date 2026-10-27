"""`jigane new`: レビューを作る。"""

import argparse
import json
from pathlib import Path

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_GATE_VIOLATION, EXIT_OK
from jigane.commands.output import print_lines
from jigane.commands.views.review import (
    gate_lines,
    has_gate_violation,
    map_lines,
    source_lines,
    warning_lines,
)
from jigane.design.review_documents import create_design_doc_review
from jigane.reviews.lifecycle import (
    Review,
    create_feature_review,
    create_pull_request_review,
    create_range_review,
)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    new = subparsers.add_parser("new", help="レビューを作ります")
    new_modes = new.add_subparsers(dest="mode", metavar="<mode>", required=True)
    files = new_modes.add_parser("files", help="機能レビュー式のレビューを作ります")
    files.add_argument("paths", nargs="+", metavar="path")
    files.add_argument("--name", required=True)
    intent = files.add_mutually_exclusive_group()
    intent.add_argument("--intent")
    intent.add_argument("--intent-file", type=Path)
    files.add_argument("--json", action="store_true")

    pull = new_modes.add_parser("pr", help="差分レビュー式のレビューを作ります")
    pull.add_argument("url")
    pull.add_argument("--json", action="store_true")
    range_mode = new_modes.add_parser(
        "range",
        help=(
            "手元のコミットの範囲のレビューを作ります。"
            "同じ名前が既にあれば refresh で作り直します。"
            "別のレビューを残すなら --name を使います"
        ),
    )
    range_mode.add_argument("range", metavar="<base>..<head>")
    range_mode.add_argument(
        "--name",
        help=(
            "レビューの名前です。既定は head の名前から作ります。"
            "同じ名前が既にあるときに別の名前を付けます"
        ),
    )
    range_intent = range_mode.add_mutually_exclusive_group()
    range_intent.add_argument("--intent")
    range_intent.add_argument("--intent-file", type=Path)
    range_mode.add_argument("--json", action="store_true")
    design_doc = new_modes.add_parser(
        "design-doc",
        help="設計書をレビューします。実装の前に方針の綻びを問います",
    )
    design_doc.add_argument("document", metavar="<設計書のパス>")
    design_doc.add_argument(
        "--name",
        help="レビューの名前です。既定は設計書のファイル名から作ります",
    )
    design_doc.add_argument(
        "--select",
        nargs="+",
        metavar="ファイル",
        help="突き合わせるファイルです。既定は推し（jigane design suggest）の候補すべてです",
    )
    design_doc.add_argument(
        "--with",
        dest="attach",
        nargs="+",
        metavar="ファイル",
        help=(
            "付属の文書です（検証メモなど）。既定は付けません。"
            "候補は設計書と同じ場所の reports/ の .md です"
        ),
    )
    design_doc.add_argument("--json", action="store_true")
    new.set_defaults(handler=run)


def print_new(review: Review, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(review.to_dict(), ensure_ascii=False, indent=2))
        return
    print_lines(
        [
            f"レビューを作りました: {review.id}",
            f"次に打つコマンドの例: jigane estimate {review.id} macro",
            f"対象: {len(review.paths)} ファイル",
        ],
        source_lines(review),
        map_lines(review),
        warning_lines(review),
        gate_lines(review),
        # 警告やゲートの行が続いても識別子を見つけられるよう、末尾に改めて出す
        [f"このレビューの識別子: {review.id}"],
    )


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # レビューの作成は Claude Code CLI を起動しない
    if args.mode == "pr":
        review = create_pull_request_review(args.url)
    elif args.mode == "range":
        review = create_range_review(
            Path.cwd(), args.range, args.name, args.intent, args.intent_file
        )
    elif args.mode == "design-doc":
        review = create_design_doc_review(
            Path.cwd(), args.document, args.name, args.select, args.attach
        )
    else:
        review = create_feature_review(
            Path.cwd(), args.paths, args.name, args.intent, args.intent_file
        )
    print_new(review, as_json=args.json)
    return EXIT_GATE_VIOLATION if has_gate_violation(review) else EXIT_OK
