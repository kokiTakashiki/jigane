"""足した公開の名前と呼ぶ側の抽出（C#）。構文木（tree-sitter）だけから読み、実行も評価もしない。

定義は、型（`class`、`struct`、`record`、`interface`、`enum`）、メソッド、コンストラクタ、
`enum` の列挙子と `const` で、プロパティ、フィールド、`event`、演算子、インデクサは含めない。
名前空間は修飾名に含めず、入れ子の型は外側の型の名前を `.` で連ねる。
公開は、可視性が `private`（と `file`）でなく、外側の型も非公開でないもの。
修飾のないメンバは `private`、修飾のないトップレベルの型は `internal`、
`interface` と `enum` のメンバは修飾がなくても公開とする。
同じ修飾名の定義は、ファイルとオーバーロードをまたいで一つに合わせる。
関数の本体の中の宣言（ローカル関数）は定義にしない。
呼ぶ側は変更の後の木の中で名前だけを照合して数え、型の解決はしない。
型は、名前を書いた箇所（継承の並びと生成を含む）を呼ぶ側とする。
型の名前を書かない `new()` は、変数とフィールドの宣言に型の名前があるときだけ数える。
メソッドグループと、`using static` で受けた列挙の値は数えない。
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from tree_sitter import Node

from jigane.languages.base import PublicName, PublicNameKind, Visibility
from jigane.languages.csharp_nodes import (
    NAMESPACES,
    TYPE_KINDS,
    declared_visibility,
    flatten,
    namespace_container,
)
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

_TOP_LEVEL = "名前空間"
_MEMBERS_PUBLIC_BY_DEFAULT = frozenset({"interface", "enum"})
# name の欄が宣言の名前になる節点。型の名前を書いた箇所に数えない
_DECLARING = frozenset(
    {
        *TYPE_KINDS,
        "method_declaration",
        "constructor_declaration",
        "destructor_declaration",
        "parameter",
        "variable_declarator",
        "enum_member_declaration",
        "property_declaration",
        "event_declaration",
        "local_function_statement",
        "type_parameter",
        "using_directive",
    }
)


@dataclass(frozen=True, kw_only=True)
class _Context:
    stack: tuple[str, ...] = ()
    where: str = _TOP_LEVEL
    cap: Visibility = Visibility.PUBLIC
    default: Visibility = Visibility.INTERNAL

    def qualify(self, name: str) -> str:
        return ".".join([*self.stack, name])

    def at(self, where: str) -> "_Context":
        return _Context(stack=self.stack, where=where, cap=self.cap, default=self.default)


def _type_name(node: Node | None) -> str | None:
    """型の注記が指す名前の最後の部分。名前でない形は None。"""
    if node is None:
        return None
    match node.type:
        case "identifier":
            return text_or_empty(node)
        case "generic_name":
            return text_or_empty(next((c for c in node.children if c.type == "identifier"), None))
        case "qualified_name":
            return _type_name(node.child_by_field_name("name"))
        case "nullable_type":
            return _type_name(node.named_children[0] if node.named_children else None)
        case _:
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
        visibility: Visibility,
        constructor: bool = False,
    ) -> None:
        place = (self.path, node.start_point.row + 1)
        # partial の型と型違いのオーバーロードは一つに合わせる
        self.tree.add(
            Definition(kind, qualified, [place], visibility is not Visibility.FILE, constructor)
        )

    def _visibility(self, node: Node, context: _Context) -> Visibility:
        return declared_visibility(node, context.default).narrower(context.cap)

    def walk(self, node: Node, context: _Context) -> None:
        if self._declaration(node, context):
            return
        self._walk_children(node, self._record(node, context))

    def _declaration(self, node: Node, context: _Context) -> bool:
        """宣言の中まで読み終えたら True。"""
        kind = node.type
        if kind in NAMESPACES:
            name = node.child_by_field_name("name")
            inner = _Context(stack=context.stack, where=context.where, cap=context.cap)
            for child in flatten(namespace_container(node)):
                if child != name:
                    self.walk(child, inner)
        elif kind in TYPE_KINDS and TYPE_KINDS[kind] != "delegate":
            self._type(node, context)
        elif kind in ("method_declaration", "constructor_declaration"):
            self._method(node, context)
        else:
            return False
        return True

    def _record(self, node: Node, context: _Context) -> _Context:
        """定義と呼ぶ側の記録を取り、子を辿る文脈を返す。"""
        kind = node.type
        if kind == "field_declaration":
            self._field(node, context)
        elif kind == "enum_member_declaration":
            self._enum_value(node, context)
        elif kind == "property_declaration":
            name = text_or_empty(node.child_by_field_name("name"))
            return context.at(context.qualify(name)) if context.stack and name else context
        elif kind == "invocation_expression":
            self._call(node, context.where)
        elif kind == "object_creation_expression":
            created = _type_name(node.child_by_field_name("type"))
            if created:
                self.tree.creations.append(Site(self.path, context.where, created))
        elif kind == "implicit_object_creation_expression":
            self._implicit_creation(node, context.where)
        elif kind == "member_access_expression":
            self._reference(node, context.where)
        elif kind == "identifier":
            self.tree.record_name(Site(self.path, context.where, text_or_empty(node)))
        return context

    def _walk_children(self, node: Node, context: _Context) -> None:
        skipped = []
        if node.type in _DECLARING or node.type == "member_access_expression":
            # 宣言の名前とメンバ名は、型の名前を書いた箇所に数えない
            name = node.child_by_field_name("name")
            if name is not None:
                skipped.append(name)
        for child in node.children:
            if child not in skipped:
                self.walk(child, context)

    def _type(self, node: Node, context: _Context) -> None:
        name = text_or_empty(node.child_by_field_name("name"))
        if not name:
            # 名前の読めない型は定義にも所属にもできない
            return
        visibility = self._visibility(node, context)
        qualified = context.qualify(name)
        self._define(PublicNameKind.CLASS, qualified, node, visibility=visibility)
        public_members = TYPE_KINDS[node.type] in _MEMBERS_PUBLIC_BY_DEFAULT
        inner = _Context(
            stack=(*context.stack, name),
            where=qualified,
            cap=visibility,
            default=Visibility.PUBLIC if public_members else Visibility.FILE,
        )
        self._walk_children(node, inner)

    def _method(self, node: Node, context: _Context) -> None:
        name = text_or_empty(node.child_by_field_name("name"))
        if not name or not context.stack:
            # 名前の読めないメソッドは定義にできない。中の呼び出しは辿る
            self._walk_children(node, context)
            return
        qualified = context.qualify(name)
        self._define(
            PublicNameKind.METHOD,
            qualified,
            node,
            visibility=self._visibility(node, context),
            constructor=node.type == "constructor_declaration",
        )
        self._walk_children(node, context.at(qualified))

    def _field(self, node: Node, context: _Context) -> None:
        if not any(c.type == "modifier" and c.text == b"const" for c in node.children):
            return
        visibility = self._visibility(node, context)
        for declaration in node.children:
            if declaration.type != "variable_declaration":
                continue
            for declarator in declaration.children:
                name = declarator.child_by_field_name("name")
                if declarator.type == "variable_declarator" and name is not None:
                    self._define(
                        PublicNameKind.ENUM_VALUE,
                        context.qualify(text_or_empty(name)),
                        node,
                        visibility=visibility,
                    )

    def _enum_value(self, node: Node, context: _Context) -> None:
        name = text_or_empty(node.child_by_field_name("name"))
        if name:
            self._define(
                PublicNameKind.ENUM_VALUE,
                context.qualify(name),
                node,
                visibility=self._visibility(node, context),
            )

    def _call(self, node: Node, where: str) -> None:
        function = node.child_by_field_name("function")
        receiver = None
        if function is not None and function.type == "member_access_expression":
            receiver = self._receiver(function)
            name = _type_name(function.child_by_field_name("name"))
        elif function is not None and function.type == "member_binding_expression":
            name = _type_name(function.child_by_field_name("name"))
        else:
            name = _type_name(function)
        if name:
            self.tree.calls.append(Site(self.path, where, name, receiver))

    def _receiver(self, access: Node) -> str | None:
        target = access.child_by_field_name("expression")
        if target is not None and target.type == "member_access_expression":
            return _type_name(target.child_by_field_name("name"))
        return _type_name(target)

    def _reference(self, node: Node, where: str) -> None:
        member = _type_name(node.child_by_field_name("name"))
        receiver = self._receiver(node)
        if member and receiver:
            self.tree.references.append(Site(self.path, where, member, receiver))

    def _implicit_creation(self, node: Node, where: str) -> None:
        # 型の名前を書かない new() は、変数とフィールドの宣言に型の名前があるときだけ数える
        declarator = node.parent
        declaration = declarator.parent if declarator is not None else None
        if declarator is None or declaration is None:
            return
        if declarator.type != "variable_declarator" or declaration.type != "variable_declaration":
            return
        name = _type_name(declaration.child_by_field_name("type"))
        if name:
            self.tree.creations.append(Site(self.path, where, name))


def _walk(path: str, tree: Tree, root: Node) -> None:
    _Walker(path, tree).walk(root, _Context())


def _sites_of(definition: Definition, tree: Tree) -> list[Site]:
    return sites_of(
        definition,
        tree,
        constructor_sites=lambda t: t.creations,
        type_sites=lambda t, name: t.names.get(name, []),
    )


def added_public_names_of(
    parse_source: ParseSource[Node],
    before: Mapping[str, str],
    after: Mapping[str, str],
    changed: Collection[str],
) -> list[PublicName]:
    return added_public_names(before, after, changed, Rules(parse_source, _walk, _sites_of))
