"""足した公開の名前と呼ぶ側の抽出（Python だけ）。構文木（ast）だけから読み、実行も評価もしない。

公開の名前は、関数、メソッド、クラス、列挙の値、引数で、名前の部品のどれも `_` で始まらない。
変更の前後の木を定義の位置（ファイルと所属を含めた名前）で比べ、後にだけある名前と、
前から在る関数とメソッドに足した引数を拾う。
呼ぶ側は変更の後の木の中で名前だけを照合して数え、型の解決はしない。
関数とメソッドの呼び出し、クラスの呼び出しと継承、列挙の値の `型.値` の参照を呼ぶ側とする。
引数は、その引数を実際に渡す呼び出し（キーワード、または位置）の関数を呼ぶ側とする。
"""

import ast
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum


class PublicNameKind(StrEnum):
    """変更で足した公開の名前の種類。"""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    ENUM_VALUE = "enum_value"
    PARAMETER = "parameter"


@dataclass(frozen=True)
class PublicName:
    """変更で足した、または引数を足した公開の名前一つと、変更の後の木の中の呼ぶ側。"""

    name: str
    kind: PublicNameKind
    path: str
    line: int
    # 呼ぶ側の関数。「パス の 所属」の形で、重複を除き、再帰は含めない
    callers: tuple[str, ...]


_ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
_MODULE_LEVEL = "モジュール"

type _Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _Definition:
    kind: PublicNameKind
    path: str
    qualified: str
    line: int
    node: ast.AST
    # メソッドと列挙の値が属するクラスの名前
    owner: str | None = None


@dataclass(frozen=True, kw_only=True)
class _Site:
    path: str
    where: str
    name: str
    is_attribute: bool
    node: ast.AST
    # 属性の参照の左辺の名前
    receiver: str | None = None


def _is_enum(node: ast.ClassDef) -> bool:
    names = {
        base.id if isinstance(base, ast.Name) else base.attr
        for base in node.bases
        if isinstance(base, ast.Name | ast.Attribute)
    }
    return bool(names & _ENUM_BASES)


def _enum_value_names(item: ast.stmt) -> list[str]:
    if isinstance(item, ast.Assign):
        targets = item.targets
    elif isinstance(item, ast.AnnAssign):
        targets = [item.target]
    else:
        # 代入以外の文は列挙の値を作らない
        return []
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _parse(sources: Mapping[str, str]) -> dict[str, ast.Module]:
    trees = {}
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
            # 後の _collect_sites は再帰する NodeVisitor で、parse が通る深さでも落ちる。
            # 落ちる木はここで見つけ、定義も呼び出しも拾わずにそのファイルを丸ごと飛ばす
            _SiteCollector(path).visit(tree)
        except (SyntaxError, RecursionError, MemoryError, ValueError):
            # 読めないファイルの名前は拾えない。他のファイルの名前で表は成り立つので止めない。
            # 入れ子の深い式は RecursionError、巨大な入力は MemoryError、
            # NUL 文字は ValueError で落ちる
            continue
        trees[path] = tree
    return trees


def _definitions(trees: Mapping[str, ast.Module]) -> dict[tuple[str, str], _Definition]:
    found: dict[tuple[str, str], _Definition] = {}
    for path, module in trees.items():
        for node in module.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[(path, node.name)] = _Definition(
                    PublicNameKind.FUNCTION, path, node.name, node.lineno, node
                )
            elif isinstance(node, ast.ClassDef):
                found[(path, node.name)] = _Definition(
                    PublicNameKind.CLASS, path, node.name, node.lineno, node
                )
                for item in node.body:
                    qualified = f"{node.name}.{getattr(item, 'name', '')}"
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        found[(path, qualified)] = _Definition(
                            PublicNameKind.METHOD, path, qualified, item.lineno, item, node.name
                        )
                    elif _is_enum(node):
                        for value in _enum_value_names(item):
                            enum_value = f"{node.name}.{value}"
                            found[(path, enum_value)] = _Definition(
                                PublicNameKind.ENUM_VALUE,
                                path,
                                enum_value,
                                item.lineno,
                                item,
                                node.name,
                            )
    return found


def _is_public(qualified: str) -> bool:
    return not any(part.startswith("_") for part in qualified.split("."))


def _parameters(definition: _Definition) -> list[tuple[str, int | None]]:
    """(名前, 呼び出しの位置の添字)。キーワードのみの引数の添字は None。self と cls は除く。"""
    function = definition.node
    if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
        # 関数とメソッド以外は引数を持たない
        return []
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]
    return [(p.arg, index) for index, p in enumerate(positional)] + [
        (p.arg, None) for p in arguments.kwonlyargs
    ]


class _SiteCollector(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.stack: list[str] = []
        self.calls: list[_Site] = []
        self.attributes: list[_Site] = []
        self.bases: list[_Site] = []

    def _where(self) -> str:
        return ".".join(self.stack) or _MODULE_LEVEL

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in node.bases:
            if isinstance(base, ast.Name | ast.Attribute):
                name = base.id if isinstance(base, ast.Name) else base.attr
                where = ".".join([*self.stack, node.name])
                self.bases.append(
                    _Site(path=self.path, where=where, name=name, is_attribute=False, node=base)
                )
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
            self.calls.append(
                _Site(
                    path=self.path,
                    where=self._where(),
                    name=function.id,
                    is_attribute=False,
                    node=node,
                )
            )
        elif isinstance(function, ast.Attribute):
            self.calls.append(
                _Site(
                    path=self.path,
                    where=self._where(),
                    name=function.attr,
                    is_attribute=True,
                    node=node,
                )
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        value = node.value
        if isinstance(value, ast.Name | ast.Attribute):
            receiver = value.id if isinstance(value, ast.Name) else value.attr
            self.attributes.append(
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


@dataclass(frozen=True)
class _Sites:
    calls: tuple[_Site, ...]
    attributes: tuple[_Site, ...]
    bases: tuple[_Site, ...]


def _collect_sites(trees: Mapping[str, ast.Module]) -> _Sites:
    collectors = [_SiteCollector(path) for path in trees]
    for collector, module in zip(collectors, trees.values(), strict=True):
        collector.visit(module)
    return _Sites(
        tuple(site for collector in collectors for site in collector.calls),
        tuple(site for collector in collectors for site in collector.attributes),
        tuple(site for collector in collectors for site in collector.bases),
    )


def _inside(site: _Site, definition: _Definition) -> bool:
    return site.path == definition.path and (
        site.where == definition.qualified or site.where.startswith(definition.qualified + ".")
    )


def _sites_of(definition: _Definition, sites: _Sites) -> list[_Site]:
    match definition.kind:
        case PublicNameKind.FUNCTION:
            return [
                s
                for s in sites.calls
                if s.name == definition.qualified and not _inside(s, definition)
            ]
        case PublicNameKind.METHOD:
            method = definition.qualified.split(".")[1]
            return [
                s
                for s in sites.calls
                if s.is_attribute and s.name == method and not _inside(s, definition)
            ]
        case PublicNameKind.CLASS:
            return [s for s in [*sites.calls, *sites.bases] if s.name == definition.qualified]
        case _:
            value = definition.qualified.split(".")[1]
            return [
                s for s in sites.attributes if s.receiver == definition.owner and s.name == value
            ]


def _passes(site: _Site, name: str, index: int | None) -> bool:
    call = site.node
    if not isinstance(call, ast.Call):
        # 継承の指定は引数を渡さない
        return False
    if any(keyword.arg == name for keyword in call.keywords):
        return True
    if index is None or any(isinstance(arg, ast.Starred) for arg in call.args):
        # キーワードのみの引数は名前で渡す。星付きの引数は位置を数えられない
        return False
    return len(call.args) > index


def _callers_of(sites: list[_Site]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(f"{s.path} の {s.where}" for s in sites))


def _added_names(
    old: Mapping[tuple[str, str], _Definition],
    new: Mapping[tuple[str, str], _Definition],
    sites: _Sites,
) -> list[PublicName]:
    return [
        _name(definition.qualified, definition.kind, definition, _sites_of(definition, sites))
        for key, definition in new.items()
        if key not in old and _is_public(definition.qualified)
    ]


def _name(
    name: str, kind: PublicNameKind, definition: _Definition, matched: list[_Site]
) -> PublicName:
    return PublicName(name, kind, definition.path, definition.line, _callers_of(matched))


def _added_parameters(
    old: Mapping[tuple[str, str], _Definition],
    new: Mapping[tuple[str, str], _Definition],
    sites: _Sites,
) -> list[PublicName]:
    found = []
    for key, definition in new.items():
        if key not in old or definition.kind not in (
            PublicNameKind.FUNCTION,
            PublicNameKind.METHOD,
        ):
            continue
        if not _is_public(definition.qualified):
            continue
        known = {name for name, _ in _parameters(old[key])}
        for name, index in _parameters(definition):
            if name in known or name.startswith("_"):
                continue
            reaching = [s for s in _sites_of(definition, sites) if _passes(s, name, index)]
            found.append(
                _name(
                    f"{definition.qualified}.{name}",
                    PublicNameKind.PARAMETER,
                    definition,
                    reaching,
                )
            )
    return found


def _added_init_parameters(
    old: Mapping[tuple[str, str], _Definition],
    new: Mapping[tuple[str, str], _Definition],
    sites: _Sites,
) -> list[PublicName]:
    found = []
    for key, definition in new.items():
        owner = definition.owner
        if definition.kind is not PublicNameKind.METHOD or not definition.qualified.endswith(
            ".__init__"
        ):
            continue
        if owner is None or (definition.path, owner) not in old or not _is_public(owner):
            continue
        known = {name for name, _ in _parameters(old[key])} if key in old else set()
        for name, index in _parameters(definition):
            if name in known or name.startswith("_"):
                continue
            reaching = [
                s
                for s in _sites_of(new[(definition.path, owner)], sites)
                if _passes(s, name, index)
            ]
            found.append(
                _name(f"{owner}.__init__.{name}", PublicNameKind.PARAMETER, definition, reaching)
            )
    return found


def added_public_names(
    before: Mapping[str, str], after: Mapping[str, str], changed: Collection[str]
) -> list[PublicName]:
    """足した公開の名前の順は、足した名前、足した引数、足した `__init__` の引数。"""
    old = _definitions(_parse(before))
    after_trees = _parse(after)
    new = {k: d for k, d in _definitions(after_trees).items() if d.path in changed}
    sites = _collect_sites(after_trees)
    return [
        *_added_names(old, new, sites),
        *_added_parameters(old, new, sites),
        *_added_init_parameters(old, new, sites),
    ]
