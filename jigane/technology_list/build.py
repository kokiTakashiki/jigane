"""レビューから技術選定のモデルを作る。"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jigane.git.tree_files import read_tree_bytes
from jigane.languages import registry
from jigane.languages.base import ImportAdapter, parse
from jigane.reviews.lifecycle import Review
from jigane.technology_list import bridges, manifests, platforms, repository_modules, tables
from jigane.technology_list.changes import (
    ADDED,
    EXTERNAL,
    LANGUAGE_OS,
    REMOVED,
    SAME_REPOSITORY,
    UNCHANGED,
)


@dataclass
class _Usage:
    # ファイルのパス → そのファイルの行の数
    lines: dict[str, int] = field(default_factory=dict)
    # ファイルのパス → その技術を取り込む import の文の数
    statements: Counter[str] = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        return {"files": len(self.lines), "lines": sum(self.lines.values())}


# (言語の名前, 技術の名前) → 使われ方
_Usages = dict[tuple[str, str], _Usage]


def _usages(root: Path, paths: Iterable[str]) -> _Usages:
    usages: _Usages = {}
    for path in paths:
        adapter = registry.adapter_for(path)
        if not isinstance(adapter, ImportAdapter):
            continue
        source = read_tree_bytes(root, path)
        if source is None:
            continue
        line_count = len(source.decode("utf-8", errors="replace").splitlines())
        for item in adapter.imports(parse(adapter, source).root_node, source):
            if item.module.startswith("."):
                # 相対の import は同じリポジトリの中を指すので、技術の表に出さない
                continue
            usage = usages.setdefault((adapter.name, adapter.technology_of(item.module)), _Usage())
            usage.lines[path] = line_count
            usage.statements[path] += 1
    return usages


def _change(after: _Usage | None, before: _Usage | None) -> str:
    if before is None:
        return ADDED
    if after is None:
        return REMOVED
    grew = len(after.lines) > len(before.lines) or any(
        count > before.statements[path] for path, count in after.statements.items()
    )
    return ADDED if grew else UNCHANGED


def _evidence(
    module: repository_modules.Evidence | None, declared: manifests.Dependency | None
) -> dict[str, Any] | None:
    if module is not None:
        return {"path": module.path, "line": module.line}
    if declared is not None:
        return {
            "path": declared.path,
            "line": declared.line,
            "package": declared.name,
            "version": declared.version,
        }
    return None


def build_model(review: Review) -> dict[str, Any]:
    """技術の表を返す。変更前のツリーが無い機能レビュー式では before と change が None。"""
    after_root = Path(review.repository_root)
    before_root = review.base_tree_path
    after = _usages(after_root, review.paths)
    before = _usages(before_root, review.paths) if before_root is not None else None
    definitions = repository_modules.collect(after_root)
    before_definitions = repository_modules.collect(before_root) if before_root else None
    # 木ごとに一度だけ走査し、依存の宣言と配備の下限を同時に読む
    after_manifests = manifests.read(after_root, review.paths)
    before_manifests = None if before_root is None else manifests.read(before_root, review.paths)
    dependencies = after_manifests.dependencies
    before_dependencies = None if before_manifests is None else before_manifests.dependencies

    technologies: list[dict[str, Any]] = []
    for key in sorted(set(after) | set(before or {})):
        language, name = key
        evidence = repository_modules.match(definitions, language, name)
        if evidence is None and before_definitions is not None:
            evidence = repository_modules.match(before_definitions, language, name)
        declared = None
        if evidence is None:
            declared = manifests.match(dependencies, language, name)
            if declared is None and before_dependencies is not None:
                declared = manifests.match(before_dependencies, language, name)
        in_after = after.get(key)
        in_before = None if before is None else before.get(key)
        technologies.append(
            {
                "name": name,
                "language": language,
                "classification": (
                    SAME_REPOSITORY
                    if evidence is not None
                    else EXTERNAL
                    if declared is not None
                    else LANGUAGE_OS
                ),
                "evidence": _evidence(evidence, declared),
                "after": (in_after or _Usage()).to_dict(),
                "before": None if before is None else (in_before or _Usage()).to_dict(),
                "change": None if before is None else _change(in_after, in_before),
                "files": sorted(in_after.lines) if in_after else [],
            }
        )
    technologies.sort(
        key=lambda t: (t["classification"], -t["after"]["files"], t["language"], t["name"])
    )
    model: dict[str, Any] = {
        "technologies": technologies,
        # 変更前のツリーが無い機能レビュー式では None
        "manifest_changes": (
            None
            if before_dependencies is None
            else manifests.changes(before_dependencies, dependencies)
        ),
        "bridges": bridges.collect(review),
        # 配備の下限。標準の部品が使えるかは、この版で決まる
        "platforms": platforms.model(
            None if before_manifests is None else before_manifests.platforms,
            after_manifests.platforms,
        ),
    }
    model["sections"] = [section.to_dict() for section in tables.sections(model)]
    return model
