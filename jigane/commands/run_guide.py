"""見積もりの JSON の `run_guide`: 未実行の群の案内の文を、計画の種類と群ごとに持つ。"""

from dataclasses import dataclass

from jigane.groups.definitions import GroupId
from jigane.reviews.lifecycle import Review
from jigane.skills.micro import VIEWPOINTS as MICRO_VIEWPOINTS
from jigane.skills.shards import ShardPlanKind, plan_kind_for
from jigane.storage.store import ReviewKind

_MACRO = "宣言、背景、ツリー図と表から、変更の道筋が目的に届くかを M0〜M6 の観点で問う。"


def _span(prefix: str) -> str:
    """観点の一覧のうち、prefix で始まる観点の最初と最後を「〜」でつなぐ。"""
    ids = [viewpoint for viewpoint in MICRO_VIEWPOINTS if viewpoint.startswith(prefix)]
    return f"{ids[0]}〜{ids[-1]}"


_MICRO_VIEWPOINTS = (
    f"{_span('D')} の観点（セキュリティの D5 はセキュリティ画面が扱う）"
    f"とテスト規約 {_span('T')} を"
)
_VERIFIERS = "反証役と判定役が検証します。"

# 方針の群はシャードに分けず計画の種類を持たないため、ここには入れない。
_SHARD_GUIDE_TEXT: dict[tuple[ShardPlanKind, GroupId], str] = {
    (ShardPlanKind.DIFF, GroupId.MICRO): (
        f"差分を複数のシャードに分け、{_MICRO_VIEWPOINTS}網羅的に拾い、{_VERIFIERS}"
    ),
    (ShardPlanKind.DIFF, GroupId.SECURITY): (
        f"差分のセキュリティ上の懸念を拾い、参照する体系の識別子とともに示し、{_VERIFIERS}"
    ),
    (ShardPlanKind.FILES, GroupId.MICRO): (
        f"選んだファイルの本文を複数のシャードに分け、{_MICRO_VIEWPOINTS}網羅的に拾い、{_VERIFIERS}"
    ),
    (ShardPlanKind.FILES, GroupId.SECURITY): (
        f"選んだファイルの本文のセキュリティ上の懸念を拾い、参照する体系の識別子とともに示し、{_VERIFIERS}"
    ),
    (ShardPlanKind.DESIGN, GroupId.MICRO): (
        "設計書のシャード（設計書だけで決まる懸念）と、ファイルのシャード"
        "（設計書と選んだファイルを突き合わせて分かる懸念）を問い、"
        f"{_MICRO_VIEWPOINTS}拾い、{_VERIFIERS}"
    ),
    (ShardPlanKind.DESIGN, GroupId.SECURITY): (
        "設計書のシャード（設計書だけで決まる懸念）と、ファイルのシャード"
        "（設計書と選んだファイルを突き合わせて分かる懸念）を問い、"
        f"セキュリティ上の懸念を拾い、参照する体系の識別子とともに示し、{_VERIFIERS}"
    ),
}


@dataclass(frozen=True)
class RunGuide:
    """未実行の群の実行の案内。review_kind はレビューの種類、text は案内の文。"""

    review_kind: ReviewKind
    text: str

    def to_json(self) -> dict[str, str]:
        """見積もりの JSON のキーを決める、唯一の置き場。"""
        return {"review_kind": self.review_kind.value, "text": self.text}


def run_guide(review: Review, group: GroupId) -> RunGuide:
    if group is GroupId.MACRO:
        return RunGuide(review.kind, _MACRO)
    return RunGuide(review.kind, _SHARD_GUIDE_TEXT[(plan_kind_for(group, review), group)])
