"""返信とレポートの文面を作る共通の手順。"""

import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from jigane.publishing import rendering as templates_module
from jigane.reviews.lifecycle import ReviewError, load_review, resolve_review


class ExportKind(StrEnum):
    """書き出す文面の種類。値は副コマンドの名前と同じ。"""

    REPLY = "export-reply"
    REPORT = "export-report"


def generate(
    review_name: str,
    template: str,
    out: Path | None,
    kind: ExportKind,
    *,
    as_json: bool,
) -> None:
    """最終的な指摘から文面を作り、標準出力かファイルへ書く。

    export-reply は差分レビュー式の指摘から文を作る。機能レビューは差分レビュー式でないので、
    export-reply でなく export-report を使う。

    Args:
        review_name: レビューの指定。
        template: テンプレートの名前。
        out: 書き出し先。None か `-` なら標準出力。
        kind: 書き出す種類。
        as_json: 文面を `{"body": ...}` の JSON で包むか。
    """
    review = load_review(resolve_review(review_name))
    match kind:
        case ExportKind.REPLY:
            if not review.is_diff:
                raise ReviewError(
                    "export-reply は差分レビュー式のレビューだけで使います。"
                    "機能レビューは export-report を使ってください"
                )
        case ExportKind.REPORT:
            # export-report は機能レビューも書き出せるので、検査は要らない
            pass
        case unreachable:
            assert_never(unreachable)
    entry = templates_module.find_template(template)
    context, warnings = templates_module.build_context(review)
    for warning in warnings:
        print(warning, file=sys.stderr)
    if not context["findings"]:
        print("注: 最終的な指摘が 0 件です", file=sys.stderr)
    text = templates_module.render(entry, context)
    if as_json:
        text = (
            json.dumps(
                {"body": text},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    # "-" は標準出力。ファイルを介さず文面を受け取れる
    if out is None or str(out) == "-":
        sys.stdout.write(text)
    else:
        out.write_text(text, encoding="utf-8")
