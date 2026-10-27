"""jigane/README.md のサブパッケージの表と、層の図（mermaid）を作る。標準ライブラリだけで動く。

    uv run --locked python scripts/generate_package_readme.py [--check]

正本は二つ。層の割り当ては pyproject.toml の `[tool.importlinter]` の layers 契約、役割の文は
各サブパッケージの README.md の先頭の説明の一文。表の行はサブパッケージ名の辞書順に並べる。
表と図は、jigane/README.md の開始と終了の印の間に書き、印の外は一文字も変えない。
--check は書かずに、印の間が最新かだけを見る。
"""

import argparse
import re
import sys
from itertools import pairwise
from pathlib import Path

from generated_regions import (
    check_or_write,
    fail,
    layers_lines,
    read_exact,
    replace_region,
)

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = "scripts/generate_package_readme.py"
TABLE_BEGIN = f"<!-- BEGIN GENERATED: package-table ({SCRIPT}) -->"
TABLE_END = "<!-- END GENERATED: package-table -->"
DIAGRAM_BEGIN = f"<!-- BEGIN GENERATED: layer-diagram ({SCRIPT}) -->"
DIAGRAM_END = "<!-- END GENERATED: layer-diagram -->"
WHERE = "jigane/README.md"
SEPARATOR = re.compile(r"[:|]")
# layers 契約の行は上の層から並ぶ。図の層の名前は、この並びの位置に対応する
LAYER_NAMES = ("cli", "feature", "core")


def read_layers(pyproject: str) -> list[list[str]]:
    """layers 契約の行ごとのサブパッケージ名。上の層から順に返す。"""
    rows = [[name.strip() for name in SEPARATOR.split(line)] for line in layers_lines(pyproject)]
    if len(rows) != len(LAYER_NAMES):
        raise ValueError(f"契約の層の数が {len(LAYER_NAMES)} ではない")
    return rows


def first_sentence(readme: str) -> str:
    """README の見出しより後の最初の段落から、先頭の一文を返す。末尾の句点は付けない。"""
    paragraph: list[str] = []
    for line in readme.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and not paragraph:
            continue
        if stripped == "":
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    text = "".join(paragraph)
    # コードスパンの中の句点は文の終わりに数えない
    outside = re.sub(r"`[^`]*`", lambda match: "\0" * len(match.group()), text)
    end = outside.find("。")
    if end < 0:
        raise ValueError("先頭の段落に句点が無い")
    return text[:end]


def render_table(names: list[str], roles: dict[str, str], newline: str) -> str:
    lines = [TABLE_BEGIN, "", "| サブパッケージ | 役割 |", "| --- | --- |"]
    for name in sorted(names):
        role = roles[name].replace("|", "\\|")
        lines.append(f"| [`{name}`]({name}/README.md) | {role}。 |")
    return newline.join([*lines, "", TABLE_END])


def render_diagram(rows: list[list[str]], newline: str) -> str:
    lines = [DIAGRAM_BEGIN, "", "```mermaid", "flowchart TB"]
    # 図は下の層（core）から上へ描く
    for layer, members in reversed(list(zip(LAYER_NAMES, rows, strict=True))):
        lines.extend(
            [f"    subgraph {layer}", *(f"        {name}" for name in members), "    end"]
        )
    lines.extend(f"    {upper} --> {lower}" for upper, lower in pairwise(LAYER_NAMES))
    return newline.join([*lines, "```", "", DIAGRAM_END])


def generate(root: Path) -> str:
    """root から読んだ jigane/README.md の、表と図を作り直した全文を返す。"""
    package = root / "jigane"
    current = read_exact(package / "README.md")
    rows = read_layers(read_exact(root / "pyproject.toml"))
    names = [name for row in rows for name in row]
    if len(set(names)) != len(names):
        raise ValueError("layers 契約に重複したサブパッケージがある")
    missing = sorted(name for name in names if not (package / name / "README.md").is_file())
    if missing:
        raise ValueError(f"README.md の無いサブパッケージ: {', '.join(missing)}")
    roles = {name: first_sentence(read_exact(package / name / "README.md")) for name in names}
    # 生成する区間の改行は、ファイルが既に使っている改行に合わせる
    newline = "\r\n" if "\r\n" in current else "\n"
    updated = replace_region(
        current, TABLE_BEGIN, TABLE_END, render_table(names, roles, newline), WHERE
    )
    return replace_region(
        updated, DIAGRAM_BEGIN, DIAGRAM_END, render_diagram(rows, newline), WHERE
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="jigane/README.md の表と層の図を作る")
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="pyproject.toml と jigane/ の置き場"
    )
    parser.add_argument("--check", action="store_true", help="書かずに、最新かだけを見る")
    arguments = parser.parse_args(argv)
    try:
        expected = generate(arguments.root)
    except ValueError as error:
        return fail(error)
    return check_or_write(
        arguments.root / "jigane" / "README.md",
        expected,
        check=arguments.check,
        guidance="`make generate`（Windows は `just generate`）で作り直す",
    )


if __name__ == "__main__":
    sys.exit(main())
