from pathlib import Path

from jigane.policy_materials.design_doc import (
    NO_CURRENT_SHAPE_TEXT,
    NO_SELECTION_TEXT,
    NOT_RECOMMENDED_TEXT,
    design_materials,
)
from jigane.policy_materials.missing import MissingMaterial
from jigane.skills.macro_design import build_design_prompt

FILES = {
    "shop/cart.py": """\
from shop.pricing import Pricing


class Cart:
    def checkout(self) -> int:
        return Pricing().total(self)
""",
    "shop/pricing.py": """\
class Pricing:
    def total(self, cart: object) -> int:
        return 1
""",
}

DOCUMENT = """\
# 題

## 1. 序論

`Cart` を直す。`Ghost` と `missing/none.py` が出る。

## 2. 設計

```mermaid
classDiagram
    class Cart {
        +checkout()
    }
```

| 列 | 値 |
| --- | --- |
| a | 1 |
| b | 2 |
"""


def _work(tmp_path: Path) -> Path:
    for relative, text in FILES.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def test_sections_are_built_in_order(tmp_path: Path) -> None:
    materials = design_materials(_work(tmp_path), "docs/d.md", DOCUMENT, ["shop/cart.py"])
    titles = [title for title, _ in materials.sections()]
    assert titles == [
        "設計書の全文",
        "設計書の構造",
        "名指しの照合の表",
        "選んだファイルと推しの理由",
        "いまの形の図",
    ]
    assert not materials.is_empty
    assert materials.missing == ()

    # 構造: 見出しの木、図ごとの要素、表の列と行の数
    assert "- 2. 設計（" in materials.structure
    assert "クラス図: 型 1 個（Cart）" in materials.structure
    assert "列 2（列、値）、行 2" in materials.structure

    # 照合の表: 引き当たったものと、見つからないもの
    rows = {line.split("|")[1].strip(): line for line in materials.mentions.splitlines()[2:]}
    assert "引き当たった" in rows["`Cart`"]
    assert "shop/cart.py" in rows["`Cart`"]
    assert "見つからない" in rows["`Ghost`"]
    assert "見つからない" in rows["`missing/none.py`"]

    # 選んだファイルと理由
    assert materials.selection.startswith("- shop/cart.py")
    assert "「1. 序論」節" in materials.selection
    assert "`Cart` を名指しました" in materials.selection

    # 現在の形の図。名前は照合で引き当たった宣言から渡る
    assert "```mermaid" in materials.current_shape
    assert "checkout" in materials.current_shape


def test_selected_file_outside_the_recommendation_is_marked(tmp_path: Path) -> None:
    root = _work(tmp_path)
    (root / "shop" / "other.py").write_text("X = 1\n", encoding="utf-8")
    materials = design_materials(root, "docs/d.md", DOCUMENT, ["shop/other.py"])
    assert NOT_RECOMMENDED_TEXT in materials.selection


def test_without_selection_there_is_no_current_shape(tmp_path: Path) -> None:
    materials = design_materials(_work(tmp_path), "docs/d.md", DOCUMENT, [])
    assert materials.selection == NO_SELECTION_TEXT
    assert materials.current_shape == NO_CURRENT_SHAPE_TEXT
    assert materials.missing == (MissingMaterial.CURRENT_SHAPE,)
    assert not materials.is_empty


def test_full_text_is_not_truncated(tmp_path: Path) -> None:
    body = "\n".join(f"行 {number} " + "あ" * 100 for number in range(1, 600))
    text = f"# 題\n\n{body}\n"
    assert len(text) > 16_000
    materials = design_materials(_work(tmp_path), "docs/long.md", text, [])
    for number in (1, 300, 599):
        assert f"行 {number} " + "あ" * 100 in materials.document
    assert "docs/long.md" in materials.document
    # 行番号は設計書の行と揃う（見出しが 1 行目、空行が 2 行目、本文は 3 行目から）
    assert "   3  行 1 " in materials.document


def test_document_text_does_not_start_a_prompt_section(tmp_path: Path) -> None:
    text = "# 題\n\n### 設計書の中の見出し\n\n$mode と ${materials} を含む本文\n"
    materials = design_materials(_work(tmp_path), "docs/d.md", text, [])
    prompt = build_design_prompt(materials, "前の回の指摘は無い。")
    assert not any(line.startswith("### 設計書の中の見出し") for line in prompt.splitlines())
    assert "$mode と ${materials} を含む本文" in prompt


def test_empty_document_has_no_material(tmp_path: Path) -> None:
    materials = design_materials(_work(tmp_path), "docs/d.md", "", [])
    assert materials.is_empty
    assert materials.missing == (
        MissingMaterial.DOCUMENT,
        MissingMaterial.STRUCTURE,
        MissingMaterial.MENTIONS,
        MissingMaterial.CURRENT_SHAPE,
    )
    assert tuple(kind.text for kind in materials.missing) == (
        "設計書の本文が渡っていません",
        "設計書から見出し、図、表が読めません",
        "設計書の名指しが一つも引き当たりません",
        "いまの形の図が描けません",
    )


def test_structure_reports_unreadable_places(tmp_path: Path) -> None:
    text = "# 題\n\n```mermaid\nclassDiagram\n"
    materials = design_materials(_work(tmp_path), "docs/d.md", text, [])
    assert "読めなかった箇所" in materials.structure
    assert "閉じない柵" in materials.structure


def test_same_input_gives_the_same_materials(tmp_path: Path) -> None:
    root = _work(tmp_path)
    first = design_materials(root, "docs/d.md", DOCUMENT, ["shop/cart.py"])
    second = design_materials(root, "docs/d.md", DOCUMENT, ["shop/cart.py"])
    assert first == second
