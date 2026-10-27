"""方針のレビューの資料の「技術選定」の節を、技術選定のモデルから Markdown で作る。"""

from typing import Any

from jigane.markdown.cells import CellKind, markdown_table
from jigane.reviews.lifecycle import Review
from jigane.technology_list.build import build_model
from jigane.technology_list.changes import ADDED, CHANGE_TEXT, LANGUAGE_OS, REMOVED
from jigane.technology_list.tables import CLASSIFICATION_TEXT


def _section(section: dict[str, Any]) -> str:
    # セルは文章や場所で、バッククォートで囲まない
    kinds: list[CellKind] = ["cell"] * len(section["header"])
    body = (
        markdown_table(section["header"], section["rows"], kinds)
        if section["rows"]
        else section["empty_text"]
    )
    return "\n\n".join([f"#### {section['heading']}", *section["notes"], body])


SHORT_INTRO = (
    "変更で使う技術に起きた変化だけの表（追加、削除、使う箇所の増減、依存の宣言の追加と削除）。"
    "根拠は、同じリポジトリの定義または依存の宣言の位置。"
)


def _short_change(technology: dict[str, Any]) -> str | None:
    """変化の文言。変わっていなければ None。変更前の無い機能レビュー式は言語と OS の技術を除く。"""
    change, before, after = technology["change"], technology["before"], technology["after"]
    if change is None:
        return None if technology["classification"] == LANGUAGE_OS else "対象のファイルが使う"
    if change in (ADDED, REMOVED):
        return CHANGE_TEXT[change]
    if before["files"] != after["files"]:
        verb = "増えた" if after["files"] > before["files"] else "減った"
        return f"使うファイルが{verb}（{before['files']} から {after['files']}）"
    # 同じファイル数のまま import の文だけが増減した差は、外形の手戻りに関わらない
    return None


def short_section_text(review: Review) -> str:
    """一回の形の「### 技術選定」の節。

    変化のあった技術だけを、名前、分類、変化、
    根拠の表にする。
    """
    model = build_model(review)
    rows: list[list[str]] = []
    names: set[str] = set()
    unchanged = 0
    for technology in model["technologies"]:
        text = _short_change(technology)
        if text is None:
            unchanged += 1
            continue
        names.add(technology["name"])
        evidence = technology["evidence"]
        rows.append(
            [
                technology["name"],
                CLASSIFICATION_TEXT[technology["classification"]],
                text,
                "-" if evidence is None else f"{evidence['path']}:{evidence['line']}",
            ]
        )
    for declared in model["manifest_changes"] or []:
        # import に現れない依存の宣言の追加と削除も、技術の変化として載せる
        if declared["name"] in names:
            continue
        rows.append(
            [
                declared["name"],
                CLASSIFICATION_TEXT["external"],
                f"依存の宣言が{CHANGE_TEXT[declared['change']]}",
                f"{declared['path']}:{declared['line']}",
            ]
        )
    body = (
        markdown_table(["技術", "分類", "変化", "根拠"], rows, ["cell"] * 4)
        if rows
        else "変化のあった技術は無い。"
    )
    count = f"変化の無い技術は {unchanged} 件で、表から省いた。"
    return f"### 技術選定\n\n{SHORT_INTRO}\n\n{body}\n\n{count}"
