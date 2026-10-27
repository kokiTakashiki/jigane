"""`jigane list`: レビューを一覧する。"""

import argparse
import json

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.views.review import origin_lines, total_lines
from jigane.languages.plugins import sanitize_text
from jigane.reviews.lifecycle import (
    ARCHIVED_EXCLUDE,
    ARCHIVED_ONLY,
    DesignDocOrigin,
    PullRequestOrigin,
    RangeOrigin,
    is_archived,
    list_reviews,
)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    listing = subparsers.add_parser("list", help="レビューを一覧します")
    listing.add_argument("--json", action="store_true")
    listing.add_argument(
        "--archived", action="store_true", help="アーカイブしたものだけを出します"
    )
    listing.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    mode = ARCHIVED_ONLY if args.archived else ARCHIVED_EXCLUDE
    reviews = list_reviews(mode)
    if args.json:
        rows = []
        for review in reviews:
            required, total = total_lines(review)
            origin = review.origin
            rows.append(
                {
                    "id": str(review.id),
                    "repository": f"{review.id.owner}/{review.id.repo}",
                    "key": review.id.key,
                    **review.kind_fields(),
                    # PR のレビューは PR の題。範囲と機能のレビューは題が無く null（名前は key）
                    "title": origin.info.title if isinstance(origin, PullRequestOrigin) else None,
                    # 範囲のレビューは作ったときの範囲の文字列。PR と機能のレビューは null
                    "range_text": origin.range.range_text
                    if isinstance(origin, RangeOrigin)
                    else None,
                    # 設計書のレビューは設計書のパスと版、いまのコードの版、選択。ほかの種類は null
                    "design_doc": origin.to_dict()
                    if isinstance(origin, DesignDocOrigin)
                    else None,
                    "created_at": review.created_at,
                    "required_lines": required,
                    "total_lines": total,
                    "file_count": len(review.paths),
                    "archived": is_archived(review.id),
                }
            )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return EXIT_OK
    for review in reviews:
        required, total = total_lines(review)
        # 通常の一覧はアーカイブ済みを隠すので、印は --archived の一覧でだけ付く
        mark = "  [アーカイブ済み]" if is_archived(review.id) else ""
        print(f"{review.id}  {review.created_at}  判断が要る行 {required}/{total}{mark}")
        # 文字表示の一覧では PR の題と URL を出さず、範囲の文字列だけを足す
        if isinstance(review.origin, RangeOrigin):
            print(sanitize_text("\n".join(f"  {line}" for line in origin_lines(review))))
        elif isinstance(review.origin, DesignDocOrigin):
            print(sanitize_text(f"  設計書: {review.origin.document_path}"))
    return EXIT_OK
