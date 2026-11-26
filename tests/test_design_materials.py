from pathlib import Path

import pytest
from design_materials_support import work

from jigane.languages.kinds import LocationKind
from jigane.policy_materials.design_doc import design_materials
from jigane.policy_materials.design_doc_render import (
    NO_SELECTION_TEXT,
    NOT_RECOMMENDED_TEXT,
)
from jigane.policy_materials.design_doc_shape import (
    LOCATION_LABELS,
    NO_CURRENT_SHAPE_TEXT,
    NON_DECLARATION_KINDS,
)
from jigane.policy_materials.missing import MissingMaterial
from jigane.skills.macro_design import build_design_prompt

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


def test_sections_are_built_in_order(tmp_path: Path) -> None:
    materials = design_materials(work(tmp_path), "docs/d.md", DOCUMENT, ["shop/cart.py"])
    titles = [title for title, _ in materials.sections()]
    assert titles == [
        "設計書の全文",
        "設計書の構造",
        "名指しの照合の表",
        "選んだファイルと候補にした理由",
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
    root = work(tmp_path)
    (root / "shop" / "other.py").write_text("X = 1\n", encoding="utf-8")
    materials = design_materials(root, "docs/d.md", DOCUMENT, ["shop/other.py"])
    assert NOT_RECOMMENDED_TEXT in materials.selection


def test_without_selection_there_is_no_current_shape(tmp_path: Path) -> None:
    materials = design_materials(work(tmp_path), "docs/d.md", DOCUMENT, [])
    assert materials.selection == NO_SELECTION_TEXT
    assert materials.current_shape == NO_CURRENT_SHAPE_TEXT
    assert materials.missing == (MissingMaterial.CURRENT_SHAPE,)
    assert not materials.is_empty


def test_full_text_is_not_truncated(tmp_path: Path) -> None:
    body = "\n".join(f"行 {number} " + "あ" * 100 for number in range(1, 600))
    text = f"# 題\n\n{body}\n"
    assert len(text) > 16_000
    materials = design_materials(work(tmp_path), "docs/long.md", text, [])
    for number in (1, 300, 599):
        assert f"行 {number} " + "あ" * 100 in materials.document
    assert "docs/long.md" in materials.document
    # 行番号は設計書の行と揃う（見出しが 1 行目、空行が 2 行目、本文は 3 行目から）
    assert "   3  行 1 " in materials.document


def test_document_text_does_not_start_a_prompt_section(tmp_path: Path) -> None:
    text = "# 題\n\n### 設計書の中の見出し\n\n$mode と ${materials} を含む本文\n"
    materials = design_materials(work(tmp_path), "docs/d.md", text, [])
    prompt = build_design_prompt(materials, "前の回の指摘は無い。")
    assert not any(line.startswith("### 設計書の中の見出し") for line in prompt.splitlines())
    assert "$mode と ${materials} を含む本文" in prompt


def test_empty_document_has_no_material(tmp_path: Path) -> None:
    materials = design_materials(work(tmp_path), "docs/d.md", "", [])
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
    materials = design_materials(work(tmp_path), "docs/d.md", text, [])
    assert "読めなかった箇所" in materials.structure
    assert "閉じない柵" in materials.structure


def test_same_input_gives_the_same_materials(tmp_path: Path) -> None:
    root = work(tmp_path)
    first = design_materials(root, "docs/d.md", DOCUMENT, ["shop/cart.py"])
    second = design_materials(root, "docs/d.md", DOCUMENT, ["shop/cart.py"])
    assert first == second


def test_declaration_kinds_are_outside_the_labelled_location_kinds() -> None:
    declarations = set(LocationKind) - NON_DECLARATION_KINDS
    assert declarations
    assert declarations.isdisjoint(LOCATION_LABELS)


def _write_sources(root: Path, files: dict[str, str]) -> None:
    for path, source in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")


def test_generated_file_hint_resolves_functions_in_sections_with_types(tmp_path: Path) -> None:
    functions = {
        "git_invocation": "jigane/git/invocation.py",
        "build_git_invocation": "jigane/git/command_line.py",
        "_classify_file": "jigane/judgment_map/classification.py",
        "materials_sections": "jigane/policy_materials/changed_files.py",
        "file_change": "jigane/judgment_map/changes.py",
    }
    _write_sources(
        tmp_path, {path: f"def {name}():\n    pass\n" for name, path in functions.items()}
    )
    _write_sources(tmp_path, {"jigane/git/kinds.py": "class GitSubcommand:\n    pass\n"})
    document = """\
# 生成物の印

## 起動の入口
`GitSubcommand` と `git_invocation` と `build_git_invocation`。
`jigane/git/invocation.py` を参照する。

## 資料の組み方
`GitSubcommand` と `_classify_file` と `materials_sections` と `file_change`。
"""
    table = design_materials(tmp_path, "docs/generated-file-hint.md", document, []).mentions
    assert "| `GitSubcommand` | type | 起動の入口 | 4 | 引き当たった | class " in table
    assert (
        "| `jigane/git/invocation.py` | path | 起動の入口 | 5 | 引き当たった | ファイル " in table
    )
    expected = set()
    for name, path in functions.items():
        section, line = ("起動の入口", 4) if "git" in name else ("資料の組み方", 8)
        qualified = path.removesuffix(".py").replace("/", ".") + "." + name
        expected.add(
            f"| `{name}` | type | {section} | {line} | 引き当たった | "
            f"function {qualified} {path}:1 |"
        )
    assert expected <= set(table.splitlines())


def test_handover_notice_resolves_parenthesized_function_with_type_in_same_section(
    tmp_path: Path,
) -> None:
    path = "claudeworks/core/host.py"
    _write_sources(
        tmp_path,
        {
            path: "\n" * 20 + 'def detect_host_kind() -> str:\n    return "linux"\n',
            "claudeworks/core/runner.py": "class Runner:\n    pass\n",
        },
    )
    document = (
        f"# 引き継ぎ\n\n## 先行する副コマンド\n`Runner` と `{path}` の `detect_host_kind()`。\n"
    )
    doc_path = "docs/private/handover-notice/handover-notice.md"
    table = design_materials(tmp_path, doc_path, document, []).mentions
    assert (
        "| `detect_host_kind` | type | 先行する副コマンド | 4 | 引き当たった | "
        f"function claudeworks.core.host.detect_host_kind {path}:21 |"
    ) in table.splitlines()


@pytest.mark.parametrize(
    ("path", "source", "type_kind", "type_name", "method_name", "function_label"),
    [
        (
            "tools.py",
            (
                "class Worker:\n    def check_item(self):\n        pass\n"
                "async def read_item():\n    pass\n"
            ),
            "class",
            "tools.Worker",
            "check_item",
            "tools.read_item",
        ),
        (
            "Tools.swift",
            (
                "struct Worker {\n    func checkItem(value: Int) {}\n}\n"
                "func read_item(value: Int) {}\n"
            ),
            "struct",
            "Worker",
            "checkItem",
            "read_item(value:)",
        ),
        (
            "Worker.cs",
            "class Worker {\n    void checkItem(int value) {}\n}\n",
            "class",
            "Worker",
            "checkItem",
            None,
        ),
        (
            "Worker.java",
            "class Worker {\n    void checkItem(int value) {}\n}\n",
            "class",
            "Worker",
            "checkItem",
            None,
        ),
    ],
    ids=["python", "swift", "csharp", "java"],
)
def test_callable_mentions_use_language_declarations(
    tmp_path: Path,
    path: str,
    source: str,
    type_kind: str,
    type_name: str,
    method_name: str,
    function_label: str | None,
) -> None:
    _write_sources(tmp_path, {path: source})
    text = (
        "# 設計\n\n## 入口\n"
        f"`Worker`、`{method_name}()`、`Worker.{method_name}` と `read_item`。\n"
    )
    rows = design_materials(tmp_path, "docs/design.md", text, []).mentions.splitlines()
    method_location = f"method {type_name}.{method_name} {path}:2"
    assert (
        f"| `Worker` | type | 入口 | 4 | 引き当たった | {type_kind} {type_name} {path}:1 |" in rows
    )
    assert f"| `{method_name}` | type | 入口 | 4 | 引き当たった | {method_location} |" in rows
    assert (
        f"| `Worker.{method_name}` | qualified_name | 入口 | 4 | 引き当たった | "
        f"{method_location} |"
    ) in rows
    if function_label is not None:
        assert (
            f"| `read_item` | type | 入口 | 4 | 引き当たった | "
            f"function {function_label} {path}:4 |"
        ) in rows


def test_overloads_are_shown_together_and_separate_function_owners_are_ambiguous(
    tmp_path: Path,
) -> None:
    _write_sources(
        tmp_path,
        {
            "Tools.swift": "func read_item() {}\nfunc read_item(value: Int) {}\n",
            "Worker.swift": "struct Worker {}\n",
        },
    )
    text = "# 設計\n\n## 入口\n`Worker` と `read_item()`。\n"
    locations = "function read_item() Tools.swift:1、function read_item(value:) Tools.swift:2"
    materials = design_materials(tmp_path, "docs/design.md", text, ["Tools.swift"])
    assert f"引き当たった | {locations} |" in materials.mentions
    assert "`read_item` を名指しました" in materials.selection

    _write_sources(tmp_path, {"other.py": "def read_item():\n    pass\n"})
    materials = design_materials(tmp_path, "docs/design.md", text, ["Tools.swift", "other.py"])
    assert (
        f"曖昧（候補が複数で、一つに絞れない） | {locations}、"
        "function other.read_item other.py:1 |"
    ) in materials.mentions
    assert materials.selection.count(NOT_RECOMMENDED_TEXT) == 2
