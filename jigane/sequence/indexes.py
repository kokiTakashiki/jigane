"""言語アダプタごとの索引を、型の名前から引けるようにまとめる。"""

from collections.abc import Iterable
from pathlib import Path

from jigane.git.tree_files import read_tree_bytes
from jigane.languages import registry
from jigane.languages.base import SequenceAdapter
from jigane.sequence.index import Index, TypeInfo, TypeKey, build_index


class Indexes:
    """言語アダプタごとの索引を、型の名前から引けるようにまとめたもの。"""

    def __init__(self, root: Path, paths: Iterable[str]) -> None:
        listed = list(paths)
        self.root = root
        adapters: dict[str, SequenceAdapter] = {}
        for path in listed:
            adapter = registry.adapter_for(path)
            if isinstance(adapter, SequenceAdapter):
                adapters.setdefault(adapter.name, adapter)
        self.indexes: list[Index] = [build_index(root, listed, a) for a in adapters.values()]

    def types(self) -> Iterable[TypeInfo]:
        for index in self.indexes:
            yield from index.types.values()

    def find(self, key: TypeKey) -> TypeInfo | None:
        return next((i.types[key] for i in self.indexes if key in i.types), None)

    def display(self, info: TypeInfo) -> str:
        """図に出す型の名前。モジュールを型として持つ言語では、モジュールの接頭辞を落とす。"""
        for index in self.indexes:
            module = index.modules.get(info.path)
            if module is not None and info.qualified_name.startswith(module + "."):
                return info.qualified_name.removeprefix(module + ".")
        return info.qualified_name

    def enum_values(self, info: TypeInfo) -> list[str]:
        """列挙型の値の名前。型とメンバの宣言には値が無いので、宣言の記録から取る。"""
        if info.kind != "enum":
            return []
        prefix = self.display(info) + "."
        for index in self.indexes:
            tree = index.trees.get(info.path)
            source = read_tree_bytes(self.root, info.path)
            if tree is None or source is None:
                continue
            names: list[str] = []
            for declaration in index.adapter.declarations(tree.root_node, source):
                value = declaration.qualified_name.removeprefix(prefix)
                if (
                    declaration.kind == "variable"
                    and declaration.qualified_name.startswith(prefix)
                    and "." not in value
                    and value not in names
                ):
                    names.append(value)
            return names
        return []

    def declared(self, path: str, name: str) -> TypeInfo | None:
        """宣言の記録の名前とパスで引いた型。同じ名前のファイルに閉じる型も、パスで区別できる。"""
        return next(
            (t for i in self.indexes if (t := i.type_of_declaration(path, name)) is not None), None
        )

    def named(self, name: str) -> list[TypeInfo]:
        """型注記と基底の名前の引き先。ファイル全体を表す型は、注記が指すものではないので除く。"""
        return [t for i in self.indexes for t in i.by_name.get(name, []) if t.kind != "module"]
