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

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Self, cast

from tree_sitter import Node

from jigane.languages.base import PublicName, PublicNameKind
from jigane.languages.node_text import text_or_empty
from jigane.languages.public_names_core import (
    AddedParameter,
    Definition,
    ParseSource,
    Rules,
    Site,
    Tree,
    added_public_names,
    sites_of,
)
from jigane.languages.swift_nodes import FUNCTION_NODES, TYPE_DECLARATIONS, TYPE_KEYWORDS

_MODULE_LEVEL = "モジュール"
_DECLARATIONS = FUNCTION_NODES | TYPE_DECLARATIONS
_PRIVATE = frozenset({b"private", b"fileprivate"})
_IDENTIFIERS = frozenset({"simple_identifier", "type_identifier"})
_DECLARED_NAMES = _IDENTIFIERS | {"pattern", "value_argument_label"}


@dataclass
class _Definition(Definition):
    # 呼ぶ側が渡すラベルの集合（オーバーロードは合わせる）
    labels: set[str] = field(default_factory=set)

    def merge(self, other: Self) -> None:
        super().merge(other)
        self.labels |= other.labels


@dataclass(frozen=True)
class _Site(Site):
    labels: frozenset[str] = frozenset()


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
        label = (
            text_or_empty(external)
            if external is not None
            else text_or_empty(parameter.children[0])
        )
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
                found.add(
                    text_or_empty(label.children[0]) if label.children else text_or_empty(label)
                )
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
        receiver = text_or_empty(target)
    elif target is not None and target.type == "navigation_expression":
        receiver = _navigation_parts(target)[1]
    return receiver, text_or_empty(name) if name is not None else None


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
        labels: set[str] | None = None,
        constructor: bool = False,
    ) -> None:
        place = (self.path, node.start_point.row + 1)
        # オーバーロードは一つの定義に合わせる
        self.tree.add(
            _Definition(
                kind,
                qualified,
                [place],
                public,
                is_constructor=constructor,
                labels=set(labels or ()),
            )
        )

    def walk(self, node: Node, context: _Context) -> None:
        kind = node.type
        if kind in _DECLARATIONS and not context.local and self._declaration(node, context):
            return
        if kind == "enum_entry" and not context.local:
            for case in node.children_by_field_name("name"):
                self._define(
                    PublicNameKind.ENUM_VALUE,
                    context.qualify(text_or_empty(case)),
                    node,
                    public=context.public and not _is_private(node),
                )
        elif kind == "call_expression":
            self._call(node, context.where)
        elif kind == "navigation_expression":
            receiver, member = _navigation_parts(node)
            if member is not None:
                site = _Site(self.path, context.where, member, receiver, frozenset())
                self.tree.references.append(site)
        elif kind in _IDENTIFIERS:
            self.tree.record_name(Site(self.path, context.where, text_or_empty(node)))
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
        if node.type in TYPE_DECLARATIONS:
            is_extension = node.type == "class_declaration" and not any(
                c.type in TYPE_KEYWORDS for c in node.children
            )
            self._type(node, context, extension=is_extension)
            return True
        name = (
            "init"
            if node.type == "init_declaration"
            else text_or_empty(node.child_by_field_name("name"))
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
            labels=_labels(node),
            constructor=node.type == "init_declaration",
        )
        inner = _Context(stack=(*context.stack, name), public=public, local=True)
        self._walk_children(node, inner)
        return True

    def _type(self, node: Node, context: _Context, *, extension: bool = False) -> None:
        name = text_or_empty(node.child_by_field_name("name"))
        if not name:
            # 名前の読めない型は定義にも所属にもできない
            return
        public = context.public and not _is_private(node)
        # 拡張される型は別の場所で定義済みなので、型の名前は足さず、メンバだけを所属付きで数える
        if not extension:
            self._define(PublicNameKind.CLASS, context.qualify(name), node, public=public)
        inner = _Context(stack=(*context.stack, name), public=public, owner=name)
        for child in node.children:
            if child.type == "inheritance_specifier":
                base = child.child_by_field_name("inherits_from")
                names = [c for c in (base.children if base else []) if c.type == "type_identifier"]
                if names:
                    inherited = _Site(
                        self.path, inner.where, text_or_empty(names[-1]), None, frozenset()
                    )
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
            name = text_or_empty(callee)
        elif callee.type == "navigation_expression":
            receiver, last = _navigation_parts(callee)
            name = last or ""
        else:
            # 呼ばれる名前の読めない形（式の結果の呼び出し）は拾わない
            return
        if name:
            self.tree.calls.append(_Site(self.path, where, name, receiver, _call_labels(node)))


def _key_of(definition: Definition) -> tuple[str, str]:
    # 別のファイルにある同名の定義は別の行として出す
    return (definition.places[0][0], definition.qualified)


def _walk(path: str, tree: Tree, root: Node) -> None:
    _Walker(path, tree).walk(root, _Context())


def _sites_of(definition: Definition, tree: Tree) -> list[Site]:
    # `init` は型の名前での呼び出し。型は継承の並びも使う側に数える
    return sites_of(
        definition,
        tree,
        constructor_sites=lambda t: t.calls,
        type_sites=lambda t, name: [*t.names.get(name, []), *t.inherits],
    )


def _added_labels(
    previous: Definition | None, definition: Definition, _old: Tree, new: Tree
) -> list[AddedParameter]:
    if (
        previous is None
        or not definition.is_public
        or definition.kind not in (PublicNameKind.FUNCTION, PublicNameKind.METHOD)
    ):
        # 前に無い定義は名前の行で出るので、引数の行は重ねない。非公開と引数を持たない種類も対象外
        return []
    # 木に入る定義は、この部品の歩き方が作った `_Definition` だけ
    added = cast("_Definition", definition).labels - cast("_Definition", previous).labels
    if not added:
        return []
    sites = _sites_of(definition, new)
    return [
        AddedParameter(label, tuple(s for s in sites if label in cast("_Site", s).labels))
        for label in sorted(added)
    ]


def added_public_names_of(
    parse_source: ParseSource[Node],
    before: Mapping[str, str],
    after: Mapping[str, str],
    changed: Collection[str],
) -> list[PublicName]:
    """足した名前、足した引数の順。"""
    return added_public_names(
        before,
        after,
        changed,
        Rules(parse_source, _walk, _sites_of, key_of=_key_of, added_parameters=(_added_labels,)),
    )
