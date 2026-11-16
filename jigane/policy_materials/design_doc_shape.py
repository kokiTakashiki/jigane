"""設計書のレビューの資料のうち、いまの形の図の節の文を組む。"""

from pathlib import Path

from jigane.design.recommend import Found, Recommendation
from jigane.languages.kinds import LocationKind
from jigane.policy_materials.current_shape import current_shape

NO_CURRENT_SHAPE_TEXT = "選んだファイルから描けるいまの形の図は無い。"
# 宣言ではない位置の種類の表。文言の描画（design_doc_render.py）もこの表を使う
LOCATION_LABELS = {
    LocationKind.FILE: "ファイル",
    LocationKind.DIRECTORY: "ディレクトリ",
    LocationKind.SUBCOMMAND: "副コマンド",
    LocationKind.OPTION: "オプション",
    LocationKind.STORED_FILE: "保存物",
}
# 表に無い種類（型と、型の中やモジュール直下の宣言）は、宣言の名前を持つ
NON_DECLARATION_KINDS = frozenset(LOCATION_LABELS)


def shape_names(chosen: tuple[str, ...], recommendation: Recommendation) -> list[str]:
    """照合で引き当たった宣言のうち、選んだファイルにあるものの名前。図の入口と型の指定に渡す。"""
    paths = set(chosen)
    names: set[str] = set()
    for match in recommendation.matches:
        if not isinstance(match, Found):
            continue
        for location in match.locations:
            if location.path in paths and location.kind not in NON_DECLARATION_KINDS:
                names.add(location.label)
    return sorted(names)


def current_shape_text(
    root: Path, chosen: tuple[str, ...], recommendation: Recommendation
) -> tuple[str, bool]:
    """いまの形の図の文と、描けた図が無いか。"""
    if not chosen:
        return NO_CURRENT_SHAPE_TEXT, True
    shape = current_shape(root, chosen, shape_names(chosen, recommendation))
    drawable = (
        shape.tree.is_drawable(),
        shape.classes.is_drawable(),
        shape.sequences.is_drawable(),
    )
    if not any(drawable):
        return NO_CURRENT_SHAPE_TEXT, True
    text = "\n\n".join(
        (
            f"#### ツリー図\n\n{shape.tree.text()}",
            f"#### クラス図\n\n{shape.classes.text()}",
            f"#### シーケンス図\n\n{shape.sequences.text()}",
        )
    )
    return text, False
