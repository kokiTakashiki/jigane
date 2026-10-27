"""pyproject.toml の import-linter の兄弟の forbidden 契約を作る。標準ライブラリだけで動く。

    uv run --locked python scripts/generate_import_contracts.py [--check]

正本は二つ。層の割り当ては `[tool.importlinter]` の layers 契約、宣言済みの兄弟の依存は
`[tool.jigane.allowed_sibling_imports]` の表。同じ層の兄弟ごとに、表で許していない相手を全部並べた
forbidden 契約を、pyproject.toml の開始と終了の印の間に書く。印の外は一文字も変えない。
--check は書かずに、印の間が最新かだけを見る。
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

from generated_regions import (
    check_or_write,
    fail,
    layers_lines,
    read_exact,
)
from generated_regions import replace_region as replace_marked_region

ROOT = Path(__file__).resolve().parent.parent

BEGIN = "# BEGIN GENERATED: sibling-contracts (scripts/generate_import_contracts.py)"
END = "# END GENERATED: sibling-contracts"
SEPARATOR = re.compile(r"[:|]")


def sibling_groups(layers: list[str]) -> list[list[str]]:
    """層の行ごとの兄弟の並び。下の層から順に返し、兄弟が一つだけの層は含めない。"""
    groups = [[name.strip() for name in SEPARATOR.split(line)] for line in reversed(layers)]
    return [group for group in groups if len(group) > 1]


def render_contracts(layers: list[str], allowed: dict[str, list[str]]) -> str:
    """印を含む契約の全文を返す。表が層の兄弟でない名前や重複を持てば ValueError にする。"""
    groups = sibling_groups(layers)
    siblings_of = {name: group for group in groups for name in group}
    for name, targets in allowed.items():
        if name not in siblings_of:
            raise ValueError(f"兄弟を持たないか、層にないサブパッケージが表にある: {name}")
        if len(set(targets)) != len(targets):
            raise ValueError(f"{name} の許す相手に重複がある")
        outside = sorted((set(targets) - set(siblings_of[name])) | ({name} & set(targets)))
        if outside:
            raise ValueError(f"{name} の許す相手が同じ層の兄弟でない: {', '.join(outside)}")
    blocks = [BEGIN]
    for group in groups:
        for name in group:
            forbidden = [
                other for other in group if other != name and other not in allowed.get(name, [])
            ]
            lines = [
                "[[tool.importlinter.contracts]]",
                f'name = "{name} は宣言した兄弟だけを読む"',
                'type = "forbidden"',
                f'source_modules = ["jigane.{name}"]',
                "forbidden_modules = [",
                *(f'    "jigane.{other}",' for other in forbidden),
                "]",
                "allow_indirect_imports = true",
            ]
            blocks.append("\n".join(lines))
    # 兄弟が一つだけの層は契約を持たない（禁じる相手がいない）
    return "\n\n".join(blocks) + "\n\n" + END


class Sources(NamedTuple):
    layers: list[str]
    allowed: dict[str, list[str]]


def read_sources(text: str) -> Sources:
    allowed = tomllib.loads(text)["tool"]["jigane"]["allowed_sibling_imports"]
    return Sources(layers=layers_lines(text), allowed=allowed)


def replace_region(text: str, contracts: str) -> str:
    return replace_marked_region(text, BEGIN, END, contracts, "pyproject.toml")


def generate(text: str) -> str:
    sources = read_sources(text)
    contracts = render_contracts(sources.layers, sources.allowed)
    # 生成する区間の改行は、ファイルが既に使っている改行に合わせる
    newline = "\r\n" if "\r\n" in text else "\n"
    return replace_region(text, contracts.replace("\n", newline))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="import-linter の兄弟の forbidden 契約を作る")
    parser.add_argument("--root", type=Path, default=ROOT, help="pyproject.toml の置き場")
    parser.add_argument("--check", action="store_true", help="書かずに、最新かだけを見る")
    arguments = parser.parse_args(argv)
    pyproject = arguments.root / "pyproject.toml"
    try:
        expected = generate(read_exact(pyproject))
    except ValueError as error:
        return fail(error)
    return check_or_write(
        pyproject,
        expected,
        check=arguments.check,
        guidance="`make generate`（Windows は `just generate`）で作り直す",
    )


if __name__ == "__main__":
    sys.exit(main())
