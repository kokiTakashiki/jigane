"""設計書のレビューの資料を、設計書の全文、構造、照合の表、選んだファイル、いまの形の図から組む。

レビューの種類にも出所にも依らない。設計書の本文と、選んだファイルのパスだけを受け取る。
LLM は呼ばない。同じ設計書と同じコードからは、同じ資料ができる。
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from jigane.design.division import section_marks
from jigane.design.parse import parse_design
from jigane.design.recommend import Ambiguous, Found, recommend
from jigane.markdown.fence import one_line
from jigane.policy_materials.design_doc_render import (
    mentions_text,
    numbered_text,
    selection_blocks,
    selection_text,
)
from jigane.policy_materials.design_doc_shape import current_shape_text
from jigane.policy_materials.design_doc_structure import is_structure_empty, structure_text
from jigane.policy_materials.missing import MissingMaterial, ordered_missing
from jigane.reviews.attachments import AttachmentContent
from jigane.text.prompt_labels import ATTACHMENT_PATH_LABEL

SECTION_DOCUMENT = "設計書の全文"
# 付属の文書を渡すときの見出し。主の設計書の見出しは SECTION_DOCUMENT から替わる
SECTION_MAIN_DOCUMENT = "主の設計書"
SECTION_ATTACHMENT_PREFIX = "付属の文書: "
SECTION_STRUCTURE = "設計書の構造"
SECTION_MENTIONS = "名指しの照合の表"
SECTION_SELECTION = "選んだファイルと候補にした理由"
SECTION_CURRENT_SHAPE = "いまの形の図"

# recommend の上限。選んだファイルの理由を、候補の上限で落とさずに引くために大きく取る
REASON_LIMIT = 10_000

# 設計書の範囲で数える材料。すべて欠けたときが材料なし
DESIGN_MATERIALS = {
    MissingMaterial.DOCUMENT,
    MissingMaterial.STRUCTURE,
    MissingMaterial.MENTIONS,
    MissingMaterial.CURRENT_SHAPE,
}


@dataclass(frozen=True)
class DesignMaterials:
    """資料の五つの節の文と、四つの材料のうち欠けているもの。

    節の文は見出し（`### `）を含まない。見出しは `titled` で付ける。
    selection は、材料なしの判定に数えない（選んだファイルが無ければ、いまの形の図も空になる）。
    """

    document: str
    structure: str
    mentions: str
    selection: str
    current_shape: str
    # 付属の文書。主の設計書の後ろに並ぶ。無ければ空
    attachments: tuple[AttachmentContent, ...]
    # 照合の表は引き当たらない行だけでも出るので、欠けは節の文から決まらない
    missing: tuple[MissingMaterial, ...]

    @property
    def is_empty(self) -> bool:
        return set(self.missing) == DESIGN_MATERIALS

    def sections(self) -> tuple[tuple[str, str], ...]:
        """資料の順（設計書の全文、構造、照合の表、選んだファイル、いまの形の図）の見出しと文。"""
        document_title = SECTION_MAIN_DOCUMENT if self.attachments else SECTION_DOCUMENT
        return (
            (document_title, self.document),
            *attachment_sections(self.attachments),
            (SECTION_STRUCTURE, self.structure),
            (SECTION_MENTIONS, self.mentions),
            (SECTION_SELECTION, self.selection),
            (SECTION_CURRENT_SHAPE, self.current_shape),
        )

    def text(self) -> str:
        return "\n\n".join(f"### {title}\n\n{body}" for title, body in self.sections())


def attachment_sections(
    attachments: Sequence[AttachmentContent],
) -> tuple[tuple[str, str], ...]:
    """付属の文書を、見出し付きの節の並びにする。パスの順に並べる。表示の直前の唯一の置き場。"""
    return tuple(
        (
            f"{SECTION_ATTACHMENT_PREFIX}{one_line(item.path)}",
            numbered_text(item.path, item.text, ATTACHMENT_PATH_LABEL.text),
        )
        for item in sorted(attachments, key=lambda item: item.path)
    )


def design_materials(
    root: Path,
    document_path: str,
    document_text: str,
    selected: Collection[str],
    attachments: Sequence[AttachmentContent] = (),
) -> DesignMaterials:
    """設計書の本文と、利用者が選んだファイルから、資料を組む。

    root はいまのコードの作業ディレクトリ。selected は root からの相対パス。
    attachments は付属の文書で、主の設計書とは別の節にする。
    名指しの照合と構造は主の設計書だけから作る（付属の名指しは候補に入れない）。
    """
    document = parse_design(document_text)
    chosen = tuple(sorted(set(selected)))
    recommendation = recommend(root, document, REASON_LIMIT)
    unreadable = document.all_unreadable()
    structure_empty = is_structure_empty(document, unreadable)
    mentions_empty = not any(isinstance(m, Found | Ambiguous) for m in recommendation.matches)
    shape_text, shape_empty = current_shape_text(root, chosen, recommendation)
    missing = ordered_missing(
        kind
        for kind, is_empty in (
            (MissingMaterial.DOCUMENT, not document_text.strip()),
            (MissingMaterial.STRUCTURE, structure_empty),
            (MissingMaterial.MENTIONS, mentions_empty),
            (MissingMaterial.CURRENT_SHAPE, shape_empty),
        )
        if is_empty
    )
    return DesignMaterials(
        document=numbered_text(document_path, document_text),
        structure=structure_text(document, section_marks(document.sections), unreadable),
        mentions=mentions_text(recommendation),
        selection=selection_text(chosen, recommendation),
        current_shape=shape_text,
        attachments=tuple(attachments),
        missing=missing,
    )


@dataclass(frozen=True)
class ShardMaterials:
    """実装の群の設計書の計画が使う部品。全文、見出しの木、選んだファイルごとの理由。"""

    document: str
    structure: str
    reasons: Mapping[str, str]


def shard_materials(
    root: Path,
    document_path: str,
    document_text: str,
    selected: Collection[str],
) -> ShardMaterials:
    """設計書の本文と選んだファイルから、シャードの資料の部品を組む。候補は一度だけ引く。"""
    document = parse_design(document_text)
    chosen = tuple(sorted(set(selected)))
    recommendation = recommend(root, document, REASON_LIMIT)
    return ShardMaterials(
        document=numbered_text(document_path, document_text),
        structure=structure_text(
            document, section_marks(document.sections), document.all_unreadable()
        ),
        reasons=selection_blocks(chosen, recommendation),
    )
