"""Markdown の表と、そのセルに入れる文字列を整える。資料の表が共有する。"""

from collections.abc import Sequence
from typing import Literal


def cell(text: str) -> str:
    """表の区切りの `|` と改行をセルの中で無害にする。バッククォートはそのまま残す。"""
    return text.replace("|", "\\|").replace("\n", " ")


def code_cell(text: str) -> str:
    """`cell` に加え、バッククォートを `'` に置く。コードスパンの中に入れる文字列に使う。"""
    return cell(text.replace("`", "'"))


CellKind = Literal["raw", "cell", "code", "codespan"]


def markdown_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    kinds: Sequence[CellKind],
    *,
    cap: int | None = None,
    more: str = "ほか {rest} 件",
) -> str:
    """見出しと行から Markdown の表を作る。資料の表はすべてここから作る。

    kinds は列ごとのセルの種類。raw は整えずに入れる（番号や、呼び出し側が組んだ文字列）。
    cell は `cell`、code は `code_cell`、codespan は `code_cell` で整えてバッククォートで囲む。
    cap を渡すと先頭の cap 行だけを出し、超えた分は表の後に空行を挟んで more の一行で示す。
    more の {rest} に超えた行数が入る。
    """
    shown = rows if cap is None else rows[:cap]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in shown:
        rendered = (_render(text, kind) for text, kind in zip(row, kinds, strict=True))
        lines.append("| " + " | ".join(rendered) + " |")
    if cap is not None and len(rows) > cap:
        lines.append("\n" + more.format(rest=len(rows) - cap))
    return "\n".join(lines)


def _render(text: str, kind: CellKind) -> str:
    if kind == "cell":
        return cell(text)
    if kind == "code":
        return code_cell(text)
    if kind == "codespan":
        return f"`{code_cell(text)}`"
    return text
