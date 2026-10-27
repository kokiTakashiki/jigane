"""設計書の方針のプロンプトを組む。レビューの種類にも出所にも依らず、資料の型だけを受け取る。

雛形は prompts/macro_design.md。欠陥の目録と契約の基準、前の回の指摘の扱いは、図だけの形と
共有の部品（macro_shared.prompt_parts）から差し込む。入力の上限の検査は
macro.check_input_budget を使う。
"""

from jigane.design.division import design_inserts
from jigane.llm_runs.prompt_stamp import PromptStamp
from jigane.policy_materials.design_doc import DesignMaterials
from jigane.skills import adversarial, macro_shared

DESIGN_PROMPT = "macro_design"


def design_materials_text(materials: DesignMaterials, history_text: str) -> str:
    """資料の五つの節の後ろに、前の回の指摘の節を続ける。"""
    return f"{materials.text()}\n\n### {macro_shared.SECTION_HISTORY}\n\n{history_text}"


def build_design_prompt(
    materials: DesignMaterials,
    history_text: str,
    declaration: str = macro_shared.NO_DECLARATION_TEXT,
) -> str:
    """設計書の形のプロンプト。コードの本文を含めず、道具の使い方も書かない。

    history_text は前の回の指摘の文（history.history_text が組む）。declaration は宣言の文。
    """
    inserts = design_inserts()
    return adversarial.load_prompt(DESIGN_PROMPT).substitute(
        declaration=declaration,
        materials=design_materials_text(materials, history_text),
        division=inserts.division,
        viewpoints=inserts.viewpoints,
        **macro_shared.prompt_parts(),
    )


def stamp_design_prompt(text: str) -> PromptStamp:
    """版は、設計書の雛形の文面と共有の部品から作る。"""
    return macro_shared.stamp_template(DESIGN_PROMPT, text, design_inserts())
