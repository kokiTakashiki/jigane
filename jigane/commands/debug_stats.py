"""`jigane debug stats`: 置き場の全レビューの数と札をまとめて返す。

既定は数と札だけを出し、指摘の本文は --with-body のときだけ出す。
"""

import argparse
import json
from typing import Any

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.output import print_lines
from jigane.findings.records import load_findings_state, stored_findings_size
from jigane.reviews.catalog import (
    ARCHIVED_INCLUDE,
    ReviewSummary,
    list_review_summaries_counting_skipped,
)
from jigane.storage.store import ReviewError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
# findings.json 1 件の大きさの上限。超える記録は読み込む前に、壊れた記録と同じに数えて飛ばす
MAX_FINDINGS_FILE_BYTES = 4 * 1024 * 1024
LABEL_KEYS = ("id", "severity", "triage")


def page_size_argument(text: str) -> int:
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"整数で指定してください: {text}") from error
    if not 1 <= value <= MAX_PAGE_SIZE:
        raise argparse.ArgumentTypeError(f"1 以上 {MAX_PAGE_SIZE} 以下で指定してください")
    return value


def add_arguments(parser: argparse.ArgumentParser, notice: str) -> None:
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--page-size", type=page_size_argument, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--after", default=None, help="前の出力の next の値。その続きを出します")
    parser.add_argument("--with-body", action="store_true", help="指摘の本文も出します")
    # debug.py が持つ暫定の注意書きを、循環した取り込みを避けて渡してもらう
    parser.set_defaults(handler=run_stats, notice=notice)


class OversizeError(Exception):
    """findings.json が大きさの上限を超えた記録。"""


def _review_row(summary: ReviewSummary, *, with_body: bool) -> dict[str, Any]:
    if stored_findings_size(summary.id.directory()) > MAX_FINDINGS_FILE_BYTES:
        raise OversizeError
    state = load_findings_state(summary.id.directory())
    keys = (*LABEL_KEYS, "claim") if with_body else LABEL_KEYS
    findings = {
        str(group): [{key: raw[key] for key in keys} for raw in (f.to_json() for f in items)]
        for group, items in state.groups.items()
    }
    return {
        "id": str(summary.id),
        "finding_counts": {group: len(items) for group, items in findings.items()},
        "findings": findings,
    }


def run_stats(args: argparse.Namespace, context: Context) -> int:
    del context
    listed = list_review_summaries_counting_skipped(ARCHIVED_INCLUDE)
    # skipped はこのページを作る間に飛ばした数。順序に置けない要約の破損は最初のページだけで数え、
    # 全ページの和が全体の数になるようにする
    skipped = listed.skipped if args.after is None else 0
    # 印は識別子の並びで決める。作成時刻の並びは、間にレビューが増えると続きがずれる
    ordered = sorted(listed.summaries, key=lambda summary: str(summary.id))
    remaining = [s for s in ordered if args.after is None or str(s.id) > args.after]
    page = remaining[: args.page_size]
    rows: list[dict[str, Any]] = []
    for summary in page:
        try:
            rows.append(_review_row(summary, with_body=args.with_body))
        except (ReviewError, ValueError, KeyError, TypeError, OSError, OversizeError):
            # 壊れた findings.json は一覧と同じく、全体を止めずに数えて飛ばす
            skipped += 1
    body = {
        "reviews": rows,
        "next": str(page[-1].id) if len(remaining) > len(page) else None,
        "skipped": skipped,
        "limits": {
            "default_page_size": DEFAULT_PAGE_SIZE,
            "max_page_size": MAX_PAGE_SIZE,
            "max_findings_file_bytes": MAX_FINDINGS_FILE_BYTES,
        },
    }
    if args.json:
        print_lines([json.dumps(body, ensure_ascii=False, indent=2)])
        return EXIT_OK
    print_lines(
        [f"{row['id']}\t{sum(row['finding_counts'].values())}" for row in rows]
        + [f"skipped\t{skipped}", args.notice]
    )
    return EXIT_OK
