"""技術選定のモデルの節（見出し、表、空のときの文、案内文）を決める。

節の組み立てはここだけが持つ。資料（materials.py）は Markdown の表に、CLI は桁をそろえた
テキストに描くだけで、節の並びも文言も作らない。
"""

from typing import Any, NamedTuple

from jigane.technology_list.changes import CHANGE_TEXT, EXTERNAL, LANGUAGE_OS, SAME_REPOSITORY
from jigane.technology_list.manifests import PLATFORM_FILES, PLATFORM_LANGUAGES

CLASSIFICATION_TEXT = {
    SAME_REPOSITORY: "同じリポジトリ",
    EXTERNAL: "外部",
    LANGUAGE_OS: "言語と OS",
}
# 橋渡しの表（languages）の見つけ方の値をすべて訳す
VIA_TEXT = {
    "base": "準拠・継承",
    "creation": "生成",
    "attribute": "属性",
    "modifier": "修飾子",
    "import": "import",
}

# 列の種類。セルの見せ方はこの値で決まる
TEXT = "text"
CHANGE = "change"
PATH = "path"

NO_IMPORTS_TEXT = "変更したファイルに import が無い。"
NO_MANIFEST_BASE_TEXT = "機能レビュー式には変更前が無いため、依存の宣言の変化は無い。"
MANIFEST_UNCHANGED_TEXT = "依存の宣言は変わっていない。"
NO_BRIDGES_TEXT = "技術の橋渡しは無い。"
NO_PLATFORMS_TEXT = "読めるマニフェストに配備の下限は見つからなかった。"
PLATFORMS_GUIDE_TEXT = (
    "標準の部品には導入された版がある。下限より新しい版で導入された部品は使えないため、"
    "標準で同じ役目の部品があるかは、この下限を見てから判断する。"
)


class Section(NamedTuple):
    key: str
    heading: str
    header: list[str]
    # header と同じ長さ。セルの種類（TEXT、CHANGE、PATH）
    kinds: list[str]
    rows: list[list[str]]
    # rows と同じ長さ。各行の変化の値（changes.py の値）。変化の列を持たない節と、
    # 比べる相手が無い行は None。色はこの値で決まる
    row_changes: list[str | None]
    # rows が空のときに表の代わりに出す文
    empty_text: str
    # 表（または empty_text）の前に置く案内文
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self._asdict()


def _change_text(change: str | None) -> str:
    return "-" if change is None else CHANGE_TEXT[change]


def _technology_section(model: dict[str, Any]) -> Section:
    rows = []
    row_changes = []
    for t in model["technologies"]:
        before, evidence = t["before"], t["evidence"]
        rows.append(
            [
                t["name"],
                t["language"],
                CLASSIFICATION_TEXT[t["classification"]],
                str(t["after"]["files"]),
                str(t["after"]["lines"]),
                "-" if before is None else f"{before['files']} / {before['lines']}",
                _change_text(t["change"]),
                "-" if evidence is None else f"{evidence['path']}:{evidence['line']}",
            ]
        )
        row_changes.append(t["change"])
    return Section(
        "technologies",
        "使っている技術",
        ["技術", "言語", "分類", "ファイル", "行", "変更前(ファイル/行)", "変化", "根拠"],
        [TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, CHANGE, PATH],
        rows,
        row_changes,
        NO_IMPORTS_TEXT,
        [],
    )


def _manifest_changes_section(model: dict[str, Any]) -> Section:
    changes = model["manifest_changes"]
    rows = [
        [
            CHANGE_TEXT[c["change"]],
            c["name"],
            c["language"],
            c["before_version"] or "-",
            c["after_version"] or "-",
            f"{c['path']}:{c['line']}",
        ]
        for c in changes or []
    ]
    # 変更前のツリーが無い機能レビュー式は比べられず、変わらなかったとは別の文を出す
    empty = NO_MANIFEST_BASE_TEXT if changes is None else MANIFEST_UNCHANGED_TEXT
    return Section(
        "manifest_changes",
        "依存の宣言の変化",
        ["変化", "パッケージ", "言語", "変更前の版", "変更後の版", "宣言"],
        [CHANGE, TEXT, TEXT, TEXT, TEXT, PATH],
        rows,
        [c["change"] for c in changes or []],
        empty,
        [],
    )


def _body_lines_text(body_lines: dict[str, int] | None) -> str:
    return "、".join(f"{member} {lines} 行" for member, lines in (body_lines or {}).items()) or "-"


def _bridges_section(model: dict[str, Any]) -> Section:
    rows = [
        [
            b["name"],
            f"{b['bridge']}（{b['from']} → {b['to']}）",
            _body_lines_text(b["body_lines"]),
            "-" if b["type_lines"] is None else f"{b['type_lines']} 行",
            "-" if b["members"] is None else str(b["members"]),
            VIA_TEXT.get(b["via"], b["via"]),
            f"{b['path']}:{b['line']}",
        ]
        for b in model["bridges"]
    ]
    return Section(
        "bridges",
        "技術の橋渡し",
        ["名前", "橋渡し", "本体の行", "型の行", "メンバ", "見つけ方", "場所"],
        [TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, PATH],
        rows,
        [None] * len(rows),
        NO_BRIDGES_TEXT,
        [],
    )


def _platforms_section(model: dict[str, Any]) -> Section:
    rows = [
        [
            p["platform"],
            p["minimum"] or "-",
            _change_text(p["change"]),
            p["before_minimum"] or "-",
            p["path"],
        ]
        for p in model["platforms"]
    ]
    coverage = platform_coverage(model["technologies"])
    note = f"下限を読むマニフェスト: {', '.join(coverage['manifests'])}。"
    if coverage["unread_languages"]:
        languages = ", ".join(coverage["unread_languages"])
        note += f"技術の表にあって下限を読んでいない言語: {languages}。"
    # 下限が見つかって初めて、標準の部品の版との照合を案内する意味が生まれる
    notes = [note, PLATFORMS_GUIDE_TEXT] if rows else [note]
    return Section(
        "platforms",
        "配備の下限",
        ["対象", "下限", "変化", "変更前の下限", "宣言"],
        [TEXT, TEXT, CHANGE, TEXT, PATH],
        rows,
        [p["change"] for p in model["platforms"]],
        NO_PLATFORMS_TEXT,
        notes,
    )


def platform_coverage(technologies: list[dict[str, Any]]) -> dict[str, list[str]]:
    """下限を読むマニフェストと、技術の表にあって下限を読んでいない言語。"""
    return {
        "manifests": list(PLATFORM_FILES),
        "unread_languages": sorted({t["language"] for t in technologies} - PLATFORM_LANGUAGES),
    }


def sections(model: dict[str, Any]) -> list[Section]:
    """資料と CLI が同じ順で出す四つの節。model の元のデータだけから作る。"""
    return [
        _technology_section(model),
        _manifest_changes_section(model),
        _bridges_section(model),
        _platforms_section(model),
    ]
