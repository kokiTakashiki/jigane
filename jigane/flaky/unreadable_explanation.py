"""読めない記述の警告の説明。文面は Occurrence.wording から組む。"""

from jigane.gates.explanation import GateExplanation
from jigane.judgment_map.unreadable_wording import PREDICATE, UnreadableWording


def explanation_of(wording: UnreadableWording) -> GateExplanation:
    """警告の説明。作った時点の文を gates.json に保存し、読むときは引き直さない。"""
    return GateExplanation(
        "jigane は、言語仕様の上で書ける記述でも、読めない記述をサポートしません。",
        f"{wording.title}です。この記述は{PREDICATE}。",
        wording.fix_hint,
    )
