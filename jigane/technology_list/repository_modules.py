"""ツリーの中で定義されたモジュールの名前と、その根拠。"""

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jigane.git.tree_files import read_tree_text, walk_tree_files

_SWIFT_TARGET = re.compile(
    r"\.(?:target|executableTarget|testTarget|systemLibrary|binaryTarget|plugin|macro)"
    r'\s*\(\s*name:\s*"([^"]+)"'
)
_JAVA_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


@dataclass(frozen=True)
class Evidence:
    """分類の根拠。root からの posix の相対パスと、1 始まりの行。"""

    path: str
    line: int


# 言語の名前 → 定義された名前 → 根拠。名前は小文字にそろえる
Definitions = dict[str, dict[str, Evidence]]


def collect(root: Path) -> Definitions:
    definitions: Definitions = {"swift": {}, "csharp": {}, "python": {}, "java": {}}
    for path in walk_tree_files(root, root):
        name = PurePosixPath(path).name
        if name == "Package.swift":
            _swift_targets(root, path, definitions["swift"])
        elif name.endswith(".csproj"):
            definitions["csharp"].setdefault(name[: -len(".csproj")].lower(), Evidence(path, 1))
        elif name == "__init__.py":
            package = PurePosixPath(path).parent.name
            if package:
                definitions["python"].setdefault(package.lower(), Evidence(path, 1))
        elif name.endswith(".java"):
            _java_package(root, path, definitions["java"])
    return definitions


def _swift_targets(root: Path, path: str, found: dict[str, Evidence]) -> None:
    text = read_tree_text(root, path)
    if text is None:
        return
    for match in _SWIFT_TARGET.finditer(text):
        line = text.count("\n", 0, match.start(1)) + 1
        target = match.group(1).lower()
        # Swift はモジュールの名前に使えない "-" を "_" に置き換えて取り込む
        found.setdefault(target, Evidence(path, line))
        found.setdefault(target.replace("-", "_"), Evidence(path, line))


def _java_package(root: Path, path: str, found: dict[str, Evidence]) -> None:
    text = read_tree_text(root, path)
    if text is None:
        return
    match = _JAVA_PACKAGE.search(text)
    if match is not None:
        line = text.count("\n", 0, match.start(1)) + 1
        found.setdefault(match.group(1).lower(), Evidence(path, line))


def match(definitions: Definitions, language: str, technology: str) -> Evidence | None:
    """技術の名前に当たる定義の根拠。当たらなければ None。"""
    defined = definitions.get(language, {})
    key = technology.lower()
    if language in ("swift", "python"):
        return defined.get(key)
    for name, evidence in defined.items():
        # 名前空間やパッケージは、プロジェクトの名前の前後どちらかに伸びる
        if name == key or name.startswith(f"{key}.") or key.startswith(f"{name}."):
            return evidence
    return None
