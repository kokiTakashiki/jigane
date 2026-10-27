"""各サブパッケージの README.md の「他のモジュールとの関係」に、依存の名前の一覧を作る。

    uv run --locked python scripts/generate_readme_dependencies.py [--check]

一覧は grimp で測った実際の import から作る。関数の中の import と `if TYPE_CHECKING:` の中も
数える（lint-imports と同じ数え方）。単位はサブパッケージで、`commands.subcommands` のような子の
パッケージは親のサブパッケージにまとめる。jigane 直下のモジュール（`__main__` など）は数えない。
一覧は、各 README の開始と終了の印の間に書き、印の外は一文字も変えない。
--check は書かずに、印の間が最新かだけを見る。
"""

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

import grimp
from generated_regions import check_or_write, fail, read_exact, replace_region

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = "scripts/generate_readme_dependencies.py"
BEGIN = f"<!-- BEGIN GENERATED: dependencies ({SCRIPT}) -->"
END = "<!-- END GENERATED: dependencies -->"
NONE = "なし"


def subpackage_names(root: Path) -> list[str]:
    """jigane 直下のサブパッケージの名前。辞書順。"""
    return sorted(init.parent.name for init in (root / "jigane").glob("*/__init__.py"))


def collect_edges(graph: grimp.ImportGraph, names: Iterable[str]) -> set[tuple[str, str]]:
    """サブパッケージの単位の直の import の向き（読む側, 読まれる側）。自分自身は数えない。"""
    known = set(names)
    edges: set[tuple[str, str]] = set()
    for module in graph.modules:
        importer = module.split(".")[1] if "." in module else ""
        if importer not in known:
            # jigane 直下のモジュール（__main__ など）は数えない
            continue
        for imported_module in graph.find_modules_directly_imported_by(module):
            imported = imported_module.split(".")[1] if "." in imported_module else ""
            if imported in known and imported != importer:
                edges.add((importer, imported))
    return edges


def render_names(names: Iterable[str]) -> str:
    ordered = sorted(set(names))
    return "、".join(f"`{name}`" for name in ordered) if ordered else NONE


def render_region(name: str, edges: set[tuple[str, str]], newline: str) -> str:
    reads = [imported for importer, imported in edges if importer == name]
    read_by = [importer for importer, imported in edges if imported == name]
    return newline.join(
        [
            BEGIN,
            "",
            f"読むサブパッケージ：{render_names(reads)}",
            "",
            f"読まれるサブパッケージ：{render_names(read_by)}",
            "",
            END,
        ]
    )


def generate(root: Path, edges: set[tuple[str, str]]) -> dict[str, str]:
    """サブパッケージ名ごとの、一覧を作り直した README の全文を返す。"""
    expected: dict[str, str] = {}
    for name in subpackage_names(root):
        current = read_exact(root / "jigane" / name / "README.md")
        # 生成する区間の改行は、ファイルが既に使っている改行に合わせる
        newline = "\r\n" if "\r\n" in current else "\n"
        expected[name] = replace_region(
            current, BEGIN, END, render_region(name, edges, newline), f"jigane/{name}/README.md"
        )
    return expected


def measure(root: Path) -> set[tuple[str, str]]:
    """root の jigane を grimp で測る。"""
    # root が既定の木と違うとき、その木の jigane を読ませる
    sys.path.insert(0, str(root))
    try:
        graph = grimp.build_graph("jigane")
    finally:
        sys.path.remove(str(root))
    return collect_edges(graph, subpackage_names(root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="各サブパッケージの README の依存の一覧を作る")
    parser.add_argument("--root", type=Path, default=ROOT, help="jigane/ の置き場")
    parser.add_argument("--check", action="store_true", help="書かずに、最新かだけを見る")
    arguments = parser.parse_args(argv)
    try:
        expected = generate(arguments.root, measure(arguments.root))
    except ValueError as error:
        return fail(error)
    status = 0
    for name, text in expected.items():
        status |= check_or_write(
            arguments.root / "jigane" / name / "README.md",
            text,
            check=arguments.check,
            guidance="`make generate`（Windows は `just generate`）で作り直す",
        )
    return status


if __name__ == "__main__":
    sys.exit(main())
