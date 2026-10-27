"""方針のレビューの状態の案内。実装とセキュリティは止めず、実行の前に理由を一行で知らせる。"""

from jigane.findings.records import load_findings_state
from jigane.groups.definitions import MACRO
from jigane.reviews.lifecycle import Review
from jigane.skills.pipeline import read_runs


def policy_notice(review: Review) -> str | None:
    """方針の状態に知らせることがあれば、その理由を返す。無ければ None。"""
    directory = review.id.directory()
    runs = read_runs(directory)
    state = load_findings_state(directory)
    if MACRO not in runs or MACRO not in state.groups:
        return "方針のレビューがまだ実行されていません"
    if MACRO in state.stale_groups:
        return "方針のレビューが古くなっています。更新の後に方針を実行し直していません"
    blocking = [
        finding.finding.id
        for finding in state.groups[MACRO]
        # 読めない区分は読み込み口が補い済みなので、ここでは別の規則を持たない
        if finding.decision.triage.traits.blocks_gate
    ]
    if blocking:
        return f"方針に必読の指摘が残っています: {', '.join(blocking)}"
    return None
