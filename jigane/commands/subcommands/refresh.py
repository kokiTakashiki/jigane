"""`jigane refresh`: 作者の修正を取り込む。"""

import argparse
import json
from pathlib import Path
from typing import assert_never

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_GATE_VIOLATION, EXIT_OK
from jigane.commands.output import id_list, print_lines
from jigane.commands.subcommands.new import add_sarif_arguments
from jigane.commands.views.review import (
    gate_lines,
    has_gate_violation,
    map_lines,
    source_lines,
    warning_lines,
)
from jigane.design.review_documents import refresh_design_doc
from jigane.findings.records import mark_stale
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import (
    NO_DECLARED_INPUTS,
    DeclaredInputs,
    DesignDocOrigin,
    FeatureOrigin,
    PullRequestOrigin,
    RangeOrigin,
    RefreshInputs,
    RefreshResult,
    Review,
    ReviewError,
    ReviewInputs,
    resolve_review,
)
from jigane.static_analysis.intake import sarif_request
from jigane.storage.store import ReviewId


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    refresh = subparsers.add_parser("refresh", help="作者の修正を取り込みます")
    refresh.add_argument("review")
    intent = refresh.add_mutually_exclusive_group()
    intent.add_argument("--intent")
    intent.add_argument("--intent-file", type=Path)
    refresh.add_argument("--json", action="store_true")
    add_sarif_arguments(refresh)
    refresh.set_defaults(handler=run)


def refresh_review(
    review_id: ReviewId,
    inputs: DeclaredInputs = NO_DECLARED_INPUTS,
    working_directory: Path | None = None,
) -> RefreshResult:
    """作者の修正を取り込む。intent か intent_file があれば、宣言を利用者の文に差し替える。

    出所の型から更新の手への振り分けは、ここの一か所だけが持つ。
    """
    review = review_module.load_review(review_id)
    sarif = inputs.sarif
    replacement = review_module.read_intent(
        working_directory or Path.cwd(), inputs.intent, inputs.intent_file
    )
    origin = review.origin
    if replacement is not None and isinstance(origin, PullRequestOrigin):
        raise ReviewError(
            "PR のレビューの宣言は PR のタイトルと本文なので、--intent では差し替えられません。"
            f"PR の本文を直して refresh してください: {review_id}"
        )
    if sarif is not None and isinstance(origin, DesignDocOrigin):
        raise ReviewError(
            f"設計書のレビューには静的検査の指摘を重ねないので、--sarif は渡せません: {review_id}"
        )
    match origin:
        case PullRequestOrigin():
            result = review_module.refresh_pull_request(review, ReviewInputs(sarif))
        case RangeOrigin():
            result = review_module.refresh_range(review, RefreshInputs(sarif, replacement))
        case DesignDocOrigin():
            result = refresh_design_doc(review, replacement)
        case FeatureOrigin():
            result = review_module.refresh_feature(review, RefreshInputs(sarif, replacement))
        case _:
            assert_never(origin)
    if result.updated:
        # 入力が変わったので、スキルの結果は古い入力に対するものになった
        mark_stale(review_id.directory())
    return result


def refresh_and_print(
    text: str,
    *,
    as_json: bool,
    inputs: DeclaredInputs = NO_DECLARED_INPUTS,
) -> Review:
    result: RefreshResult = refresh_review(resolve_review(text), inputs)
    review = result.review
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return review
    if not result.updated:
        print(f"変更なし: {review.id}")
        return review
    changes = result.region_changes
    print_lines(
        [f"レビューを更新しました: {review.id}", f"対象: {len(review.paths)} ファイル"],
        source_lines(review),
        map_lines(review),
        warning_lines(review),
        [
            f"新しい判断の箇所: {id_list(changes.added)}",
            f"内容が変わった判断の箇所: {id_list(changes.changed)}",
            f"消えた判断の箇所: {id_list(changes.removed)}",
        ],
        gate_lines(review),
    )
    return review


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    refreshed = refresh_and_print(
        args.review,
        as_json=args.json,
        inputs=DeclaredInputs(
            sarif=sarif_request(args.sarif, args.sarif_root, Path.cwd()),
            intent=args.intent,
            intent_file=args.intent_file,
        ),
    )
    return EXIT_GATE_VIOLATION if has_gate_violation(refreshed) else EXIT_OK
