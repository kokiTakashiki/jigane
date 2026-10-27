"""`jigane archive`: レビューをデータを残したまま一覧から隠す。"""

import argparse
import json

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import resolve_review, set_archived


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    archive = subparsers.add_parser("archive", help="レビューを一覧から隠します。データは残ります")
    archive.add_argument("review")
    archive.add_argument("--json", action="store_true")
    archive.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 印を付け外しするだけで LLM も外部のコマンドも呼ばない
    return change_archive(args.review, archived=True, as_json=args.json)


def change_archive(review_name: str, *, archived: bool, as_json: bool) -> int:
    review_id = resolve_review(review_name)
    changed = set_archived(review_id, archived=archived)
    if as_json:
        print(
            json.dumps(
                {"id": str(review_id), "archived": archived, "changed": changed},
                ensure_ascii=False,
            )
        )
        return EXIT_OK
    word = "アーカイブ" if archived else "アーカイブ解除"
    print(f"{word}しました: {review_id}" if changed else f"すでに{word}済みです: {review_id}")
    return EXIT_OK
