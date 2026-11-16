"""プロンプトの固定のラベルの文言と一致の方式。

プロンプトの組み立てと送る情報の対応表（llm_runs/claude_inputs.py）は、ここから文言を取る。
雛形（skills/prompts/*.md）は Python を読めないので文言を写しで持つ。
全文一致のラベルだけを試験で照合する。
ラベルごとの渡す素材は、下の層のここへ置けないので対応表の側が持つ。
"""

from dataclasses import dataclass
from enum import Enum


class Match(Enum):
    # 値の入る行は、通し番号や名前、パスが続くので、行頭の一致で引く
    EXACT = "exact"
    PREFIX = "prefix"


@dataclass(frozen=True)
class PromptLabel:
    text: str
    match: Match


def _exact(text: str) -> PromptLabel:
    return PromptLabel(text, Match.EXACT)


def _prefixed(text: str) -> PromptLabel:
    return PromptLabel(text, Match.PREFIX)


# 雛形（rebuttal.md ほか）にも同じ文言が書かれている
QUESTIONS_LABEL = _exact("問うこと:")
# 先の役の出力を JSON で渡す節。雛形が書き、adversarial.py の render_prompt が値を差し込む
CANDIDATES_LABEL = _exact("候補 (JSON):")
CHALLENGES_LABEL = _exact("反証役の出力 (JSON):")
ALTERNATIVES_LABEL = _exact("代案役の出力 (JSON):")
DEFENSES_LABEL = _exact("擁護役の出力 (JSON):")
EXCERPTS_LABEL = _exact("反証役が確かめた位置と抜粋 (JSON):")
SECTION_TREE_LABEL = _exact("見出しの木（印は受け持ちの目安）:")
TRUST_BOUNDARY_LABEL = _prefixed("信頼の境界: ")
DESIGN_DOC_PATH_LABEL = _prefixed("設計書のパス: ")
ATTACHMENT_PATH_LABEL = _prefixed("付属の文書のパス: ")
MCP_AVAILABLE_LABEL = _prefixed("次の MCP が使える: ")
NO_CALL_ENTRIES_LABEL = _prefixed("呼び出しの無い入口 ")
UNRELATED_CALL_ENTRIES_LABEL = _prefixed("変更に関わる呼び出しの無い入口 ")
