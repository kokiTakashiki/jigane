"""標準出力へ書く部品。"""

import json
import logging
import sys
from collections.abc import Sequence
from typing import Any

from jigane.commands.views.review import selection_lines
from jigane.findings.records import (
    DisplayCategory,
    HistoryLoad,
    HistoryWarning,
    HistoryWarningJson,
    Triage,
    display_info,
    load_history_checked,
)
from jigane.groups.definitions import GROUP_BY_ID, GroupId
from jigane.languages.plugins import sanitize_text
from jigane.reviews import selection as selection_module
from jigane.reviews.lifecycle import Review


class StderrWarningHandler(logging.Handler):
    """ライブラリが logging に流した警告を、`警告: ` を前置して標準エラーへ出す。"""

    def emit(self, record: logging.LogRecord) -> None:
        # 出力先は呼ぶたびに引く。テストが差し替える標準エラーにも届くようにする
        print(f"警告: {record.getMessage()}", file=sys.stderr)


def install_warning_handler() -> None:
    """jigane のログの警告を標準エラーへ出す handler を一つだけ付ける。"""
    logger = logging.getLogger("jigane")
    if not any(isinstance(h, StderrWarningHandler) for h in logger.handlers):
        logger.addHandler(StderrWarningHandler(level=logging.WARNING))


def print_lines(*sections: Sequence[str]) -> None:
    # 行には指摘の本文やパスなど、レビューの対象に由来する文字列が入る。端末へ出す前に除く
    print(sanitize_text("\n".join(line for section in sections for line in section)))


def id_list(ids: list[str]) -> str:
    return "、".join(ids) if ids else "なし"


def selection_json(review: Review, history: HistoryLoad) -> dict[str, Any]:
    return selection_module.summary(review.id.directory(), history)


def triage_table(group: GroupId) -> list[dict[str, Any]]:
    """その群で使える区分の表。宣言の順が並び。区分の名前も名札もここから受け取る。"""
    return [
        {
            "name": t.value,
            "label": t.traits.label,
            "blocks_gate": t.traits.blocks_gate,
            "read_first": t.traits.read_first,
            "placement": t.traits.placement.value,
        }
        for t in Triage
        if group in t.traits.groups
    ]


def display_category_table() -> list[dict[str, Any]]:
    """実装の群の表示の分類の表。宣言の順が並び。

    名前、名札、テーマで束ねるか（themed）はここから受け取る。
    """
    return [
        {"name": c.value, "label": display_info(c).label, "themed": display_info(c).themed}
        for c in DisplayCategory
    ]


def warnings_json(warnings: Sequence[HistoryWarning]) -> list[HistoryWarningJson]:
    """読めない履歴の警告。show、run、usage の --json が同じ欄に載せる。"""
    return [warning.to_json() for warning in warnings]


def warning_lines(warnings: Sequence[HistoryWarning]) -> list[str]:
    return [f"警告: {warning.text()}" for warning in warnings]


def print_group_json(group: GroupId, body: dict[str, Any], review: Review) -> None:
    history = load_history_checked(review.id.directory())
    document = {
        **body,
        "group_label": GROUP_BY_ID[group].label,
        "triages": triage_table(group),
        "selection": selection_json(review, history),
        "history_warnings": warnings_json(history.warnings),
    }
    print(json.dumps(document, ensure_ascii=False, indent=2))


def print_lost_reasons(lost: Sequence[selection_module.LostReason]) -> None:
    """置き換えで消えた異議の理由を、標準エラーに一件一行で出す。"""
    for item in lost:
        reason = " ".join(item.reason.splitlines())
        print(f"注: {item.number} の異議を置き換えました。消えた理由: {reason}", file=sys.stderr)


def print_removal(removal: selection_module.Removal) -> None:
    """B の番号で外した指摘と、扱いが違って外さなかった指摘を、標準エラーに示す。

    F の番号だけを渡したときは外した番号が入力と同じなので、何も出さない。
    """
    if not removal.via_theme:
        return
    print(f"注: 外した指摘: {', '.join(removal.removed)}", file=sys.stderr)
    if removal.skipped:
        print(f"注: 扱いが違うため外さなかった指摘: {', '.join(removal.skipped)}", file=sys.stderr)


def print_selection(review: Review, *, as_json: bool) -> None:
    history = load_history_checked(review.id.directory())
    if as_json:
        print(json.dumps(selection_json(review, history), ensure_ascii=False, indent=2))
        return
    print_lines(selection_lines(review, history))
