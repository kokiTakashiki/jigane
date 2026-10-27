"""図だけの形（macro）と設計書の形（macro_design）が共有する、雛形の部品と定数。

両方が互いを import すると循環するので、共有するものだけをここに置く。
"""

import hashlib

from jigane.design.division import DesignInserts
from jigane.findings.discard import discard_reason_table
from jigane.llm_runs.prompt_stamp import PromptStamp
from jigane.skills import adversarial, history, shared_rules

# 図だけの形と設計書の形が共有する雛形の部品。欠陥の目録と契約の基準、前の回の指摘の扱い。
# 雛形の `$standards` と `$history_rule` に差し込み、写しを持たない
PROMPT_PARTS = {"standards": "macro_standards", "history_rule": "macro_history_rule"}
# 道具を渡さない。空の組は build_command が `--tools ""` にする
DIAGRAM_TOOLS: tuple[str, ...] = ()
SECTION_HISTORY = history.SECTION_HISTORY
NO_DECLARATION_TEXT = "宣言が見つからない。M1 は出さない。"


def prompt_parts() -> dict[str, str]:
    """雛形の部品の文。キーは雛形の置換の名前。"""
    parts = {
        key: adversarial.template_text(name).rstrip("\n") for key, name in PROMPT_PARTS.items()
    }
    # 捨てる理由と種類の対応は DiscardKind の側(findings/discard.py)が持つ。
    # 雛形には写さず、ここで差し込む
    parts["standards"] = shared_rules.insert_into(
        PROMPT_PARTS["standards"],
        parts["standards"].replace("$discard_reason_table", discard_reason_table()),
    )
    return parts


def stamp_template(name: str, text: str, inserts: DesignInserts | None = None) -> PromptStamp:
    """版は、雛形の文面と共有の部品の文面から作る。inserts は設計の雛形へ差し込む部品。

    None は差し込みの無い形（図だけの形）で、刻印は部品を混ぜない。
    """
    digest = hashlib.sha256(adversarial.template_text(name).encode("utf-8"))
    for part in prompt_parts().values():
        digest.update(part.encode("utf-8"))
    if inserts is not None:
        digest.update(inserts.division.encode("utf-8"))
        digest.update(inserts.viewpoints.encode("utf-8"))
    return PromptStamp(digest.hexdigest()[:12], len(text))
