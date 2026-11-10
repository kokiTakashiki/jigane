"""足した公開の名前と呼ぶ側の抽出（Python だけ）。構文木（ast）だけから読み、実行も評価もしない。

公開の名前は、関数、メソッド、クラス、列挙の値、引数で、名前の部品のどれも `_` で始まらない。
変更の前後の木を定義の位置（ファイルと所属を含めた名前）で比べ、後にだけある名前と、
前から在る関数とメソッドに足した引数を拾う。
定義の索引、前後の比べ、呼ぶ側の数え方は `public_names_core` に任せ、
`ast` の歩き方と引数の照合だけを持つ。
呼ぶ側は変更の後の木の中で名前だけを照合して数え、型の解決はしない。
関数とメソッドの呼び出し、クラスの呼び出しと継承、列挙の値の `型.値` の参照を呼ぶ側とする。
クラスは、型注記と式の中で名前を書いた箇所も呼ぶ側とする。文字列で書いた型注記は数えない。
入れ子のクラスは定義に数えないので行にならない。
引数は、その引数を実際に渡す呼び出し（キーワード、または位置）の関数を呼ぶ側とする。
"""

import ast
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import NamedTuple, Self, cast

from jigane.languages.base import PublicName, PublicNameKind
from jigane.languages.public_names_core import (
    AddedParameter,
    Definition,
    Rules,
    Site,
    Tree,
    inside,
    sites_of,
)
from jigane.languages.public_names_core import (
    added_public_names as core_added_public_names,
)
from jigane.languages.python_type_fields import ENUM_BASES

_MODULE_LEVEL = "モジュール"

type _Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(kw_only=True)
class _Definition(Definition):
    node: ast.AST

    def merge(self, other: Self) -> None:
        # 同じ位置と名前の定義（プロパティの setter など）は、後のものが前のものに替わる
        self.kind = other.kind
        self.places = other.places
        self.is_public = other.is_public
        self.node = other.node


@dataclass(frozen=True, kw_only=True)
class _Site(Site):
    is_attribute: bool
    node: ast.AST


def _is_enum(node: ast.ClassDef) -> bool:
    names = {
        base.id if isinstance(base, ast.Name) else base.attr
        for base in node.bases
        if isinstance(base, ast.Name | ast.Attribute)
    }
    return bool(names & ENUM_BASES)


def _enum_value_names(item: ast.stmt) -> list[str]:
    if isinstance(item, ast.Assign):
        targets = item.targets
    elif isinstance(item, ast.AnnAssign):
        targets = [item.target]
    else:
        # 代入以外の文は列挙の値を作らない
        return []
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _is_public(qualified: str) -> bool:
    return not any(part.startswith("_") for part in qualified.split("."))


def _define(tree: Tree, kind: PublicNameKind, path: str, qualified: str, node: ast.stmt) -> None:
    tree.add(_Definition(kind, qualified, [(path, node.lineno)], _is_public(qualified), node=node))


def _define_module(path: str, tree: Tree, module: ast.Module) -> None:
    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _define(tree, PublicNameKind.FUNCTION, path, node.name, node)
        elif isinstance(node, ast.ClassDef):
            _define(tree, PublicNameKind.CLASS, path, node.name, node)
            for item in node.body:
                qualified = f"{node.name}.{getattr(item, 'name', '')}"
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    _define(tree, PublicNameKind.METHOD, path, qualified, item)
                elif _is_enum(node):
                    for value in _enum_value_names(item):
                        enum_value = f"{node.name}.{value}"
                        _define(tree, PublicNameKind.ENUM_VALUE, path, enum_value, item)


class _Parameter(NamedTuple):
    name: str
    # 呼び出しの位置の添字。キーワードのみの引数は None
    position: int | None


def _parameters(definition: Definition) -> list[_Parameter]:
    """self と cls は除く。"""
    function = cast("_Definition", definition).node
    if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
        # 関数とメソッド以外は引数を持たない
        return []
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]
    return [_Parameter(p.arg, index) for index, p in enumerate(positional)] + [
        _Parameter(p.arg, None) for p in arguments.kwonlyargs
    ]


class _SiteCollector(ast.NodeVisitor):
    def __init__(self, path: str, tree: Tree) -> None:
        self.path = path
        self.tree = tree
        self.stack: list[str] = []

    def _where(self) -> str:
        return ".".join(self.stack) or _MODULE_LEVEL

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: _Function) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Name):
            self.tree.calls.append(
                _Site(
                    path=self.path,
                    where=self._where(),
                    name=function.id,
                    is_attribute=False,
                    node=node,
                )
            )
        elif isinstance(function, ast.Attribute):
            self.tree.calls.append(
                _Site(
                    path=self.path,
                    where=self._where(),
                    name=function.attr,
                    is_attribute=True,
                    node=node,
                )
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.tree.record_name(
                _Site(
                    path=self.path,
                    where=self._where(),
                    name=node.id,
                    is_attribute=False,
                    node=node,
                )
            )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        value = node.value
        if isinstance(value, ast.Name | ast.Attribute):
            receiver = value.id if isinstance(value, ast.Name) else value.attr
            self.tree.references.append(
                _Site(
                    path=self.path,
                    where=self._where(),
                    name=node.attr,
                    is_attribute=True,
                    node=node,
                    receiver=receiver,
                )
            )
        self.generic_visit(node)


def _walk(path: str, tree: Tree, root: ast.Module) -> None:
    _SiteCollector(path, tree).visit(root)
    _define_module(path, tree, root)


def _key_of(definition: Definition) -> tuple[str, str]:
    # 別のファイルにある同名の定義は別の行として出す
    return (definition.places[0][0], definition.qualified)


def _sites_of(definition: Definition, tree: Tree) -> list[Site]:
    if definition.kind is PublicNameKind.METHOD:
        # メソッドは属性の呼び出しだけと照合する
        name = definition.qualified.rsplit(".", 1)[-1]
        return [
            s
            for s in tree.calls
            if cast("_Site", s).is_attribute and s.name == name and not inside(s, definition)
        ]
    # クラスは呼び出しと属性の参照も含む。`__init__` は定義にコンストラクタの印を付けないので、
    # コンストラクタの表は引かれない
    return sites_of(
        definition,
        tree,
        constructor_sites=lambda _: [],
        type_sites=lambda t, name: [*t.calls, *t.names.get(name, []), *t.references],
    )


def _passes(site: Site, parameter: _Parameter) -> bool:
    call = cast("_Site", site).node
    if not isinstance(call, ast.Call):
        # 継承の指定は引数を渡さない
        return False
    if any(keyword.arg == parameter.name for keyword in call.keywords):
        return True
    index = parameter.position
    if index is None or any(isinstance(arg, ast.Starred) for arg in call.args):
        # キーワードのみの引数は名前で渡す。星付きの引数は位置を数えられない
        return False
    return len(call.args) > index


def _reaching(
    parameters: list[_Parameter],
    known: set[str],
    sites_of: Callable[[], list[Site]],
) -> list[AddedParameter]:
    added = [p for p in parameters if p.name not in known and not p.name.startswith("_")]
    if not added:
        # 呼ぶ側を数える表は木の呼び出しをすべて辿るので、足した引数の無い定義では引かない
        return []
    sites = sites_of()
    return [AddedParameter(p.name, tuple(s for s in sites if _passes(s, p))) for p in added]


def _added_parameters(
    previous: Definition | None, definition: Definition, _old: Tree, new: Tree
) -> list[AddedParameter]:
    if (
        previous is None
        or definition.kind not in (PublicNameKind.FUNCTION, PublicNameKind.METHOD)
        or not definition.is_public
    ):
        # 前に無い定義は名前の行で出るので、引数の行は重ねない。非公開と引数を持たない種類も対象外
        return []
    known = {p.name for p in _parameters(previous)}
    return _reaching(_parameters(definition), known, lambda: _sites_of(definition, new))


def _added_init_parameters(
    previous: Definition | None, definition: Definition, old: Tree, new: Tree
) -> list[AddedParameter]:
    if definition.kind is not PublicNameKind.METHOD or not definition.qualified.endswith(
        ".__init__"
    ):
        return []
    owner = definition.qualified.removesuffix(".__init__")
    class_key = (definition.places[0][0], owner)
    if class_key not in old.definitions or not _is_public(owner):
        # `__init__` 自身は `_` で始まるので、公開かどうかは所属するクラスで決める。
        # 前から在るクラスの `__init__` だけが、新しい引数の行になる
        return []
    known = {p.name for p in _parameters(previous)} if previous is not None else set()
    # 引数を渡す呼び出しは `__init__` でなくクラスの名前で書かれる
    return _reaching(
        _parameters(definition), known, lambda: _sites_of(new.definitions[class_key], new)
    )


def added_public_names(
    before: Mapping[str, str], after: Mapping[str, str], changed: Collection[str]
) -> list[PublicName]:
    """足した公開の名前の順は、足した名前、足した引数、足した `__init__` の引数。"""
    return core_added_public_names(
        before,
        after,
        changed,
        Rules(
            ast.parse,
            _walk,
            _sites_of,
            key_of=_key_of,
            added_parameters=(_added_parameters, _added_init_parameters),
        ),
    )
