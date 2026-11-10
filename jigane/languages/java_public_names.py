"""足した公開の名前と呼ぶ側の抽出（Java）。構文木（tree-sitter）だけから読み、実行も評価もしない。

公開の名前は、型（`class`、`interface`、`enum`、`record`、注釈の型）、メソッド、コンストラクタ、
`enum` の定数と `static final` の定数で、`private` が自分にも外側の型にも付かないもの。
修飾子の無いメンバは、`interface` では公開、それ以外ではパッケージ内の公開として数える。
名前はパッケージを含めず、入れ子の型は外側の型の名前を `.` でつなぐ（`Outer.Inner.method`）。
変更の前後の木を修飾名で比べ、後にだけある名前を拾う。
同じ修飾名の定義は、ファイルとオーバーロードをまたいで一つに合わせる。
関数の本体の中の宣言（局所の型、無名クラス、ラムダ）は定義にしない。
フィールド（定数以外）と注釈の型の要素は定義にしない。
呼ぶ側は変更の後の木の中で名前だけを照合して数え、型の解決はしない。
型は、名前を書いた箇所、継承と実装の並び、生成を呼ぶ側とする。
コンストラクタは、その型の生成を呼ぶ側とし、`super(...)` と `this(...)` は数えない。
定数は `型.値` のメンバ参照を数え、`switch` の裸の定数（`case RED:`）とメソッド参照は数えない。
非公開から公開へ変えただけの名前は、足した名前に数えない。
"""

from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass

from tree_sitter import Node

from jigane.languages.base import PublicName, PublicNameKind, Visibility
from jigane.languages.java_nodes import METHOD_NODES, TYPE_NODES, declared_visibility
from jigane.languages.node_text import text_or_empty
from jigane.languages.public_names_core import (
    Definition,
    ParseSource,
    Rules,
    Site,
    Tree,
    added_public_names,
    sites_of,
)

_CONSTRUCTORS = frozenset({"constructor_declaration", "compact_constructor_declaration"})
_FIELDS = frozenset({"field_declaration", "constant_declaration"})
_INTERFACES = frozenset({"interface_declaration", "annotation_type_declaration"})
_BODY_TYPES = frozenset({"class_body", "interface_body", "enum_body", "annotation_type_body"})
_TYPE_NAMES = frozenset({"type_identifier"})


@dataclass(frozen=True, kw_only=True)
class _Context:
    stack: tuple[str, ...] = ()
    # 外側までの可視性（型の外側が private なら中も非公開）
    cap: Visibility = Visibility.PUBLIC
    default: Visibility = Visibility.INTERNAL
    # 関数の本体の中。宣言は外から見えないので定義にしない
    local: bool = False
    where: str = ""

    def qualify(self, name: str) -> str:
        return ".".join([*self.stack, name])


def _is_constant(node: Node, *, in_interface: bool) -> bool:
    if node.type == "constant_declaration" or in_interface:
        return True
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    types = {c.type for c in modifiers.children} if modifiers is not None else set()
    return {"static", "final"} <= types


def _type_name(node: Node | None) -> str:
    """生成する型の最後の名前。読めない形は空。"""
    if node is None:
        return ""
    if node.type == "type_identifier":
        return text_or_empty(node)
    inner = [c for c in node.children if c.type in ("type_identifier", "scoped_type_identifier")]
    return _type_name(inner[-1]) if inner else ""


def _receiver_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "identifier":
        return text_or_empty(node)
    if node.type == "field_access":
        return text_or_empty(node.child_by_field_name("field"))
    return None


class _Walker:
    def __init__(self, path: str, tree: Tree) -> None:
        self.path = path
        self.tree = tree

    def _define(
        self,
        kind: PublicNameKind,
        qualified: str,
        node: Node,
        *,
        public: bool,
        constructor: bool = False,
    ) -> None:
        place = (self.path, node.start_point.row + 1)
        # オーバーロードとファイルをまたぐ同じ名前は一つの定義に合わせる
        self.tree.add(Definition(kind, qualified, [place], public, constructor))

    def walk(self, node: Node, context: _Context) -> None:
        kind = node.type
        if kind in TYPE_NODES and not context.local:
            self._type(node, context)
            return
        if kind == "method_invocation":
            self._invocation(node, context)
        elif kind == "object_creation_expression":
            created = _type_name(node.child_by_field_name("type"))
            if created:
                self.tree.creations.append(Site(self.path, context.where, created, None))
        elif kind == "field_access":
            self._access(node, context)
        elif kind == "method_reference":
            # 型の名前の出現には数えるが、メソッドの呼び出しには数えない
            self._record_receiver(node.children[0] if node.children else None, context)
        elif kind in _TYPE_NAMES:
            self._record_name(text_or_empty(node), context)
        for child in node.children:
            self.walk(child, context)

    def _invocation(self, node: Node, context: _Context) -> None:
        name = node.child_by_field_name("name")
        target = node.child_by_field_name("object")
        if name is not None:
            site = Site(self.path, context.where, text_or_empty(name), _receiver_name(target))
            self.tree.calls.append(site)
        self._record_receiver(target, context)

    def _access(self, node: Node, context: _Context) -> None:
        member = node.child_by_field_name("field")
        target = node.child_by_field_name("object")
        receiver = _receiver_name(target)
        if member is not None and receiver is not None:
            site = Site(self.path, context.where, text_or_empty(member), receiver)
            self.tree.references.append(site)
        self._record_receiver(target, context)

    def _record_name(self, name: str, context: _Context) -> None:
        self.tree.record_name(Site(self.path, context.where, name))

    def _record_receiver(self, receiver: Node | None, context: _Context) -> None:
        # `Foo.bar()` の `Foo` は式の中の識別子なので、型の名前の出現として別に拾う
        if receiver is not None and receiver.type == "identifier":
            self._record_name(text_or_empty(receiver), context)

    def _type(self, node: Node, context: _Context) -> None:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None or body is None:
            # 名前か本体の読めない型は定義にも所属にもできない
            for child in node.children:
                self.walk(child, context)
            return
        qualified = context.qualify(text_or_empty(name_node))
        visibility = declared_visibility(node, context.default).narrower(context.cap)
        self._define(
            PublicNameKind.CLASS, qualified, node, public=visibility is not Visibility.FILE
        )
        inner = _Context(
            stack=(*context.stack, text_or_empty(name_node)),
            cap=visibility,
            default=Visibility.PUBLIC if node.type in _INTERFACES else Visibility.INTERNAL,
            where=qualified,
        )
        for child in node.children:
            if child != body:
                # 継承と実装の並び、型引数、注釈は、型そのものの所属で数える
                self.walk(child, inner)
        in_interface = node.type in _INTERFACES
        for member in self._members(body):
            self._member(member, inner, in_interface=in_interface)

    @staticmethod
    def _members(body: Node) -> Iterator[Node]:
        for member in body.children:
            if member.type == "enum_body_declarations":
                # `;` より後の宣言は、この節点の子に並ぶ
                yield from member.children
            else:
                yield member

    def _member(self, member: Node, context: _Context, *, in_interface: bool) -> None:
        kind = member.type
        visibility = declared_visibility(member, context.default).narrower(context.cap)
        public = visibility is not Visibility.FILE
        if kind in TYPE_NODES:
            self.walk(member, context)
        elif kind in METHOD_NODES:
            self._method(member, context, public=public)
        elif kind == "enum_constant":
            name = member.child_by_field_name("name")
            if name is not None:
                self._define(
                    PublicNameKind.ENUM_VALUE,
                    context.qualify(text_or_empty(name)),
                    member,
                    public=context.cap is not Visibility.FILE,
                )
            self._walk_local(member, context, skip=name)
        elif kind in _FIELDS:
            if _is_constant(member, in_interface=in_interface):
                for declarator in member.children_by_field_name("declarator"):
                    name = declarator.child_by_field_name("name")
                    if name is not None:
                        self._define(
                            PublicNameKind.ENUM_VALUE,
                            context.qualify(text_or_empty(name)),
                            member,
                            public=public,
                        )
            self._walk_local(member, context)
        else:
            # 初期化子のブロックなど。宣言ではないので中の呼び出しだけ数える
            self._walk_local(member, context)

    def _method(self, node: Node, context: _Context, *, public: bool) -> None:
        owner = context.stack[-1] if context.stack else ""
        name = node.child_by_field_name("name")
        is_constructor = node.type in _CONSTRUCTORS
        if node.type == "annotation_type_element_declaration" or name is None:
            # 注釈の型の要素は定義にしない
            self._walk_local(node, context)
            return
        member = owner if is_constructor else text_or_empty(name)
        qualified = context.qualify(member)
        self._define(
            PublicNameKind.METHOD,
            qualified,
            node,
            public=public,
            constructor=member == owner,
        )
        inner = _Context(
            stack=context.stack,
            cap=context.cap,
            default=context.default,
            local=True,
            where=qualified,
        )
        for child in node.children:
            if child != name:
                self.walk(child, inner)

    def _walk_local(self, node: Node, context: _Context, *, skip: Node | None = None) -> None:
        inner = _Context(
            stack=context.stack,
            cap=context.cap,
            default=context.default,
            local=True,
            where=context.where,
        )
        for child in node.children:
            if child != skip:
                self.walk(child, inner)


def _walk(path: str, tree: Tree, root: Node) -> None:
    _Walker(path, tree).walk(root, _Context())


def _sites_of(definition: Definition, tree: Tree) -> list[Site]:
    # コンストラクタは生成を呼ぶ側。`super(...)` と `this(...)` は呼び出しの節点が別なので入らない
    return sites_of(
        definition,
        tree,
        constructor_sites=lambda t: t.creations,
        type_sites=lambda t, name: [*t.names.get(name, []), *t.creations],
    )


def added_public_names_of(
    parse_source: ParseSource[Node],
    before: Mapping[str, str],
    after: Mapping[str, str],
    changed: Collection[str],
) -> list[PublicName]:
    """足した名前を、定義の出現順に返す。"""
    return added_public_names(before, after, changed, Rules(parse_source, _walk, _sites_of))
