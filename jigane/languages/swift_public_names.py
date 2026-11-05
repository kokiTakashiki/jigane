"""足した公開の名前と呼ぶ側の抽出（Swift）。構文木（tree-sitter）だけから読み、実行も評価もしない。

公開の名前は、トップレベルの `func`、型（`struct`、`class`、`actor`、`enum`、`protocol`）、
型のメソッドと `init`、`enum` の `case`、引数で、`private` と `fileprivate` の修飾が自分にも
入れ子の外側の型にも付かないもの。引数は、呼ぶ側が渡すラベル（外部名）で数え、
外部名が `_` の引数は名前で渡せないので拾わない。
変更の前後の木を定義の位置（ファイルと所属を含めた名前）で比べ、後にだけある名前と、
前から在る関数、メソッド、`init` に足した引数を拾う。
呼ぶ側は変更の後の木の中で名前だけを照合して数え、型の解決はしない。
型は生成と準拠に加えて、型注記と式の中で名前を書いた箇所を呼ぶ側とする。
省略形の列挙の値（`.a`）は受け手の型が解けないので数えない。
`extension` の中のメンバは、拡張される型の名前を付けて数える。型そのものは足さない。
同じ名前のオーバーロードは一つの定義に合わせるので、足したオーバーロードは足した引数として出る。
非公開から公開へ変えただけの名前は、足した名前に数えない。
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field

from tree_sitter import Node

from jigane.languages.base import PublicName, PublicNameKind

type ParseSource = Callable[[str], Node]

_MODULE_LEVEL = "モジュール"
_TYPE_KEYWORDS = frozenset({"struct", "class", "actor", "enum"})
_FUNCTIONS = frozenset(
    {"function_declaration", "protocol_function_declaration", "init_declaration"}
)
_DECLARATIONS = _FUNCTIONS | {"class_declaration", "protocol_declaration"}
_PRIVATE = frozenset({b"private", b"fileprivate"})
_IDENTIFIERS = frozenset({"simple_identifier", "type_identifier"})
_DECLARED_NAMES = _IDENTIFIERS | {"pattern", "value_argument_label"}


@dataclass
class _Definition:
    kind: PublicNameKind
    path: str
    qualified: str
    line: int
    is_public: bool
    # 呼ぶ側が渡すラベルの集合（オーバーロードは合わせる）
    labels: set[str] = field(default_factory=set)
    owner: str | None = None


@dataclass(frozen=True)
class _Site:
    path: str
    where: str
    name: str
    receiver: str | None
    labels: frozenset[str]


@dataclass
class _Tree:
    definitions: dict[tuple[str, str], _Definition] = field(default_factory=dict)
    calls: list[_Site] = field(default_factory=list)
    references: list[_Site] = field(default_factory=list)
    inherits: list[_Site] = field(default_factory=list)
    # 識別子の名前ごとの出現。呼ぶ側を数える表は行ごとに引くので、名前で索引にする
    names: dict[str, list[_Site]] = field(default_factory=dict)


def _text(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""


def _is_private(node: Node) -> bool:
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return False
    return any(
        m.type == "visibility_modifier" and (m.text or b"") in _PRIVATE for m in modifiers.children
    )


def _labels(node: Node) -> set[str]:
    found = set()
    for parameter in node.children:
        if parameter.type != "parameter":
            continue
        external = parameter.child_by_field_name("external_name")
        label = _text(external) if external is not None else _text(parameter.children[0])
        if label and label != "_":
            found.add(label)
    return found


def _call_labels(call: Node) -> frozenset[str]:
    suffix = next((c for c in call.children if c.type == "call_suffix"), None)
    if suffix is None:
        return frozenset()
    found = set()
    for arguments in suffix.children:
        if arguments.type != "value_arguments":
            continue
        for argument in arguments.children:
            label = (
                argument.child_by_field_name("name") if argument.type == "value_argument" else None
            )
            if label is not None:
                found.add(_text(label.children[0]) if label.children else _text(label))
    return frozenset(found)


def _navigation_parts(node: Node) -> tuple[str | None, str | None]:
    """(左辺の最後の名前, 右辺の名前)。読めない形は None。"""
    suffix = node.child_by_field_name("suffix")
    name = (
        next((c for c in suffix.children if c.type == "simple_identifier"), None)
        if suffix
        else None
    )
    target = node.child_by_field_name("target")
    receiver = None
    if target is not None and target.type == "simple_identifier":
        receiver = _text(target)
    elif target is not None and target.type == "navigation_expression":
        receiver = _navigation_parts(target)[1]
    return receiver, _text(name) if name is not None else None


@dataclass(frozen=True, kw_only=True)
class _Context:
    stack: tuple[str, ...] = ()
    public: bool = True
    owner: str | None = None
    # 関数の本体の中。宣言は外から見えないので定義にしない
    local: bool = False

    @property
    def where(self) -> str:
        return ".".join(self.stack) or _MODULE_LEVEL

    def qualify(self, name: str) -> str:
        return ".".join([*self.stack, name])


class _Walker:
    def __init__(self, path: str, tree: _Tree) -> None:
        self.path = path
        self.tree = tree

    def _define(
        self,
        kind: PublicNameKind,
        qualified: str,
        node: Node,
        *,
        public: bool,
        owner: str | None,
        labels: set[str] | None = None,
    ) -> None:
        key = (self.path, qualified)
        existing = self.tree.definitions.get(key)
        if existing is not None:
            # オーバーロードは一つの定義に合わせる
            existing.labels |= labels or set()
            existing.is_public = existing.is_public or public
            return
        self.tree.definitions[key] = _Definition(
            kind,
            self.path,
            qualified,
            node.start_point.row + 1,
            public,
            set(labels or ()),
            owner,
        )

    def walk(self, node: Node, context: _Context) -> None:
        kind = node.type
        if kind in _DECLARATIONS and not context.local and self._declaration(node, context):
            return
        if kind == "enum_entry" and not context.local:
            for case in node.children_by_field_name("name"):
                self._define(
                    PublicNameKind.ENUM_VALUE,
                    context.qualify(_text(case)),
                    node,
                    public=context.public and not _is_private(node),
                    owner=context.owner,
                )
        elif kind == "call_expression":
            self._call(node, context.where)
        elif kind == "navigation_expression":
            receiver, member = _navigation_parts(node)
            if member is not None:
                site = _Site(self.path, context.where, member, receiver, frozenset())
                self.tree.references.append(site)
        elif kind in _IDENTIFIERS:
            name = _text(node)
            site = _Site(self.path, context.where, name, None, frozenset())
            self.tree.names.setdefault(name, []).append(site)
        self._walk_children(node, context)

    def _walk_children(self, node: Node, context: _Context) -> None:
        # 宣言の名前と引数のラベルは、型の名前を書いた箇所に数えない。
        # 文法は型注記の型にも name の欄を使うので、節点の種類で分ける
        fields = [
            *node.children_by_field_name("name"),
            *node.children_by_field_name("external_name"),
        ]
        declared = [field for field in fields if field.type in _DECLARED_NAMES]
        for child in node.children:
            if child not in declared:
                self.walk(child, context)

    def _declaration(self, node: Node, context: _Context) -> bool:
        """宣言を読み終えたら True。名前の読めない関数だけ False で、中は呼び出し側が辿る。"""
        if node.type in ("class_declaration", "protocol_declaration"):
            is_extension = node.type == "class_declaration" and not any(
                c.type in _TYPE_KEYWORDS for c in node.children
            )
            self._type(node, context, extension=is_extension)
            return True
        name = (
            "init" if node.type == "init_declaration" else _text(node.child_by_field_name("name"))
        )
        if not name:
            # 名前の読めない関数は定義にできない。中の呼び出しは辿る
            return False
        public = context.public and not _is_private(node)
        self._define(
            PublicNameKind.METHOD if context.owner else PublicNameKind.FUNCTION,
            context.qualify(name),
            node,
            public=public,
            owner=context.owner,
            labels=_labels(node),
        )
        inner = _Context(stack=(*context.stack, name), public=public, local=True)
        self._walk_children(node, inner)
        return True

    def _type(self, node: Node, context: _Context, *, extension: bool = False) -> None:
        name = _text(node.child_by_field_name("name"))
        if not name:
            # 名前の読めない型は定義にも所属にもできない
            return
        public = context.public and not _is_private(node)
        # 拡張される型は別の場所で定義済みなので、型の名前は足さず、メンバだけを所属付きで数える
        if not extension:
            self._define(
                PublicNameKind.CLASS, context.qualify(name), node, public=public, owner=None
            )
        inner = _Context(stack=(*context.stack, name), public=public, owner=name)
        for child in node.children:
            if child.type == "inheritance_specifier":
                base = child.child_by_field_name("inherits_from")
                names = [c for c in (base.children if base else []) if c.type == "type_identifier"]
                if names:
                    inherited = _Site(self.path, inner.where, _text(names[-1]), None, frozenset())
                    self.tree.inherits.append(inherited)
            elif child.type in ("class_body", "enum_class_body", "protocol_body"):
                for member in child.children:
                    self.walk(member, inner)

    def _call(self, node: Node, where: str) -> None:
        callee = node.children[0] if node.children else None
        if callee is None:
            return
        receiver = None
        if callee.type == "simple_identifier":
            name = _text(callee)
        elif callee.type == "navigation_expression":
            receiver, last = _navigation_parts(callee)
            name = last or ""
        else:
            # 呼ばれる名前の読めない形（式の結果の呼び出し）は拾わない
            return
        if name:
            self.tree.calls.append(_Site(self.path, where, name, receiver, _call_labels(node)))


def _parse_all(sources: Mapping[str, str], parse_source: ParseSource, tree: _Tree) -> None:
    for path, source in sources.items():
        try:
            root = parse_source(source)
            local = _Tree()
            _Walker(path, local).walk(root, _Context())
        except (RecursionError, MemoryError, ValueError):
            # 深く入れ子のファイルは木を辿れない。他のファイルの名前で表は成り立つので止めない
            continue
        tree.definitions.update(local.definitions)
        tree.calls += local.calls
        tree.references += local.references
        tree.inherits += local.inherits
        for name, sites in local.names.items():
            tree.names.setdefault(name, []).extend(sites)


def _inside(site: _Site, definition: _Definition) -> bool:
    return site.path == definition.path and (
        site.where == definition.qualified or site.where.startswith(definition.qualified + ".")
    )


def _sites_of(definition: _Definition, tree: _Tree) -> list[_Site]:
    last = definition.qualified.split(".")[-1]
    match definition.kind:
        case PublicNameKind.FUNCTION:
            return [s for s in tree.calls if s.name == last and not _inside(s, definition)]
        case PublicNameKind.METHOD if last == "init":
            owner = definition.qualified.split(".")[-2]
            return [s for s in tree.calls if s.name == owner and not _inside(s, definition)]
        case PublicNameKind.METHOD:
            return [s for s in tree.calls if s.name == last and not _inside(s, definition)]
        case PublicNameKind.CLASS:
            sites = [*tree.names.get(last, []), *tree.inherits]
            return [s for s in sites if s.name == last and not _inside(s, definition)]
        case _:
            return [
                s
                for s in tree.references
                if s.receiver == definition.qualified.split(".")[-2] and s.name == last
            ]


def _callers_of(sites: list[_Site]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(f"{s.path} の {s.where}" for s in sites))


def _name(
    qualified: str, kind: PublicNameKind, definition: _Definition, sites: list[_Site]
) -> PublicName:
    return PublicName(qualified, kind, definition.path, definition.line, _callers_of(sites))


def added_public_names_of(
    parse_source: ParseSource,
    before: Mapping[str, str],
    after: Mapping[str, str],
    changed: Collection[str],
) -> list[PublicName]:
    """足した名前、足した引数の順。"""
    old, new = _Tree(), _Tree()
    _parse_all(before, parse_source, old)
    _parse_all(after, parse_source, new)
    found: list[PublicName] = []
    for key, definition in new.definitions.items():
        if definition.path not in changed or not definition.is_public:
            continue
        previous = old.definitions.get(key)
        if previous is None:
            found.append(
                _name(
                    definition.qualified, definition.kind, definition, _sites_of(definition, new)
                )
            )
    for key, definition in new.definitions.items():
        previous = old.definitions.get(key)
        if previous is None or definition.path not in changed or not definition.is_public:
            continue
        if definition.kind not in (PublicNameKind.FUNCTION, PublicNameKind.METHOD):
            continue
        sites = _sites_of(definition, new)
        for label in sorted(definition.labels - previous.labels):
            reaching = [s for s in sites if label in s.labels]
            found.append(
                _name(
                    f"{definition.qualified}.{label}",
                    PublicNameKind.PARAMETER,
                    definition,
                    reaching,
                )
            )
    return found
