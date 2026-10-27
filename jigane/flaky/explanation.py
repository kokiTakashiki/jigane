"""ゲート違反を ID の代わりに読ませる三つの文の組み立て。

返信の文面とレポートは、ゲートの項目の欄 explanation だけを引く。ここ以外で文を組まない。
"""

from collections.abc import Mapping

from jigane.flaky.catalog import (
    ANNOTATION_EXPLANATION,
    ANNOTATION_ID,
    Item,
    QuerySpec,
)
from jigane.gates.explanation import GateExplanation


def explain(
    item_id: str, by_id: Mapping[str, Item], spec: QuerySpec | None = None
) -> GateExplanation:
    """spec が言語固有の直す向きを持つときは、項目の直す向きの代わりにそれを使う。"""
    if item_id == ANNOTATION_ID:
        return ANNOTATION_EXPLANATION
    item = by_id.get(item_id)
    if item is None:
        # 古い gates.json に残った、目録から消えた ID。別の規則の文は当てない
        return GateExplanation(
            f"{item_id} の説明が目録に無いので、何が懸念かを示せません。",
            "この ID の規則は目録から消えているか、知らない ID です。",
            "目録を確かめ、ゲートを走らせ直して検出し直してください。",
        )
    if spec is not None and spec.direction is not None:
        return item.explanation.with_direction(spec.direction)
    return item.explanation
