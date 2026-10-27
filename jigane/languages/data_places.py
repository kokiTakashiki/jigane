"""言語の資料（照会と flaky の型）の置き場。本体の置き場と、外の言語の置き場の中の形を持つ。"""

from importlib.resources.abc import Traversable
from pathlib import Path

BUILTIN_QUERIES_DIR = Path(__file__).with_name("queries")
BUILTIN_FLAKY_QUERIES_DIR = Path(__file__).parents[1] / "flaky" / "queries"

# 外の言語の LanguagePlugin.data の根からの相対
PLUGIN_QUERIES_PARTS = ("languages", "queries")
PLUGIN_FLAKY_QUERIES_PARTS = ("flaky", "queries")


def descend(base: Traversable, parts: tuple[str, ...]) -> Traversable:
    for part in parts:
        base = base.joinpath(part)
    return base


def toml_names(directory: Traversable) -> list[str]:
    """置き場の中の .toml のファイル名。置き場が無ければ空。"""
    if not directory.is_dir():
        # 外の言語は資料を持たなくてよいので、置き場が無いのは誤りではない
        return []
    return sorted(child.name for child in directory.iterdir() if child.name.endswith(".toml"))


def overlap_error(data: Traversable) -> str | None:
    """外の言語の資料に、本体と同じ名前のファイルがあれば、その説明。無ければ None。"""
    pairs = (
        (PLUGIN_QUERIES_PARTS, BUILTIN_QUERIES_DIR),
        (PLUGIN_FLAKY_QUERIES_PARTS, BUILTIN_FLAKY_QUERIES_DIR),
    )
    clashes: list[str] = []
    for parts, builtin in pairs:
        builtin_names = set(toml_names(builtin))
        label = "/".join(parts)
        clashes.extend(
            f"{label}/{name}" for name in toml_names(descend(data, parts)) if name in builtin_names
        )
    if not clashes:
        return None
    return f"資料のファイルが本体と同じ名前です（本体のものが使われます）: {', '.join(clashes)}"
