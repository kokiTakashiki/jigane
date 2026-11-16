"""設計書のレビューの資料のうち、文言の描画（行番号付きの全文、照合の表、選んだファイルの理由）を組む。"""

from jigane.design.mentions import MentionVia
from jigane.design.recommend import (
    AccessorCallerReason,
    Ambiguous,
    CalleeReason,
    CallerReason,
    Candidate,
    Found,
    Location,
    NamedReason,
    NotFound,
    ReaderReason,
    Reason,
    Recommendation,
    WriterReason,
)
from jigane.design.submodule_notes import submodule_lines
from jigane.markdown.cells import code_cell, markdown_table
from jigane.markdown.fence import fence, one_line
from jigane.policy_materials.design_doc_shape import LOCATION_LABELS, NON_DECLARATION_KINDS
from jigane.text.prompt_labels import DESIGN_DOC_PATH_LABEL

NO_MENTIONS_TEXT = "設計書が名指した型、ファイル、副コマンド、保存物は無い。"
NO_SELECTION_TEXT = "利用者が選んだファイルは無い。"
NOT_RECOMMENDED_TEXT = "候補に無いファイル（利用者が選んだ）"

_VIA_LABELS = {
    MentionVia.CLASS_DIAGRAM: "クラス図で",
    MentionVia.ER_DIAGRAM: "ER 図で",
    MentionVia.SEQUENCE_DIAGRAM: "シーケンス図で",
    MentionVia.CODE_SPAN: "本文で",
    MentionVia.TABLE_CODE_SPAN: "表で",
    MentionVia.TABLE_CELL: "表で",
    MentionVia.PROSE: "本文で",
}

LOCATION_SAMPLE = 3


def numbered_text(path: str, text: str, label: str = DESIGN_DOC_PATH_LABEL.text) -> str:
    """全文を、行番号を付けて切らずに、柵で囲んで載せる。根拠の位置（パス:行）を引けるようにする。

    パスは柵の外に出るので、制御文字を見える形にする。
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        # 末尾の改行の後ろは行ではない
        lines.pop()
    body = "\n".join(f"{number:>4}  {line}" for number, line in enumerate(lines, start=1))
    return f"{label}{one_line(path)}（{len(lines)} 行）\n\n{fence(body)}"


def _code_span(name: str) -> str:
    """名前を一つのコードスパンで囲む。名前のバッククォートで囲みが崩れず、改行で行が増えない。"""
    return f"`{code_cell(one_line(name))}`"


def _location_text(location: Location) -> str:
    kind = LOCATION_LABELS.get(location.kind, location.kind.value)
    path = one_line(location.path)
    position = path if location.line is None else f"{path}:{location.line}"
    if location.kind in NON_DECLARATION_KINDS:
        return f"{kind} {position}"
    return f"{kind} {one_line(location.label)} {position}"


def _locations_text(locations: tuple[Location, ...]) -> str:
    shown = "、".join(_location_text(item) for item in locations[:LOCATION_SAMPLE])
    rest = len(locations) - LOCATION_SAMPLE
    return shown + (f"、ほか {rest} 件" if rest > 0 else "")


def mentions_text(recommendation: Recommendation) -> str:
    if not recommendation.matches:
        return NO_MENTIONS_TEXT
    rows: list[list[str]] = []
    for entry in recommendation.matches:
        mention = entry.mention
        section = one_line(mention.section.title) if mention.section else "節の外"
        match entry:
            case Found():
                result, where = "引き当たった", _locations_text(entry.locations)
            case Ambiguous():
                result = "曖昧（候補が複数で、一つに絞れない）"
                where = _locations_text(entry.locations)
            case NotFound():
                result, where = "見つからない", "いまのコードに無い"
        rows.append(
            [
                one_line(mention.name),
                mention.kind.value,
                section,
                str(mention.line),
                result,
                where,
            ]
        )
    table = markdown_table(
        ["名指し", "種類", "節", "設計書の行", "照合", "引き当て先"],
        rows,
        ["codespan", "raw", "cell", "raw", "raw", "cell"],
    )
    notes = submodule_lines(recommendation.submodules)
    return "\n\n".join([table, *notes]) if notes else table


def _reason_text(reason: Reason) -> str:
    mention = reason.mention
    section = one_line(mention.section.title) if mention.section else "節の外"
    via = _VIA_LABELS[mention.via]
    name = _code_span(mention.name)
    named = f"「{section}」節（{mention.line} 行）が{via} {name} を名指しました"
    match reason:
        case NamedReason():
            return named
        case CallerReason():
            return f"{named}。その宣言を呼ぶ側（一段のたどり）"
        case CalleeReason():
            return f"{named}。その宣言が呼ぶ先（一段のたどり）"
        case ReaderReason():
            return f"{named}。保存物を読む側の関数 {_code_span(reason.function)}（一段のたどり）"
        case WriterReason():
            return f"{named}。保存物を書く側の関数 {_code_span(reason.function)}（一段のたどり）"
        case AccessorCallerReason():
            function = _code_span(reason.function)
            return f"{named}。保存物を読み書きする関数 {function} を呼ぶ側（二段のたどり）"


def selection_blocks(chosen: tuple[str, ...], recommendation: Recommendation) -> dict[str, str]:
    """選んだファイルごとの、名指した節と候補にした理由の文。"""
    by_path = {candidate.path: candidate for candidate in recommendation.candidates}
    blocks: dict[str, str] = {}
    for path in chosen:
        candidate: Candidate | None = by_path.get(path)
        if candidate is None:
            blocks[path] = f"- {one_line(path)}\n  - {NOT_RECOMMENDED_TEXT}"
            continue
        reasons = "\n".join(f"  - {_reason_text(r)}" for r in candidate.reasons)
        blocks[path] = f"- {one_line(path)}\n{reasons}"
    return blocks


def selection_text(chosen: tuple[str, ...], recommendation: Recommendation) -> str:
    if not chosen:
        return NO_SELECTION_TEXT
    return "\n".join(selection_blocks(chosen, recommendation).values())
