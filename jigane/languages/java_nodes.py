from tree_sitter import Node

from jigane.languages.base import TypeKind, Visibility

# 型の宣言の節点と種類の正本。注釈の型は interface として扱う。
# `java` と `java_concurrency` が引く。`java` が `java_concurrency` を import するので、
# 共有の表はどちらにも置かずここに置く
TYPE_KINDS: dict[str, TypeKind] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "interface",
}
TYPE_NODES = frozenset(TYPE_KINDS)
# 並行処理の走査が型の境界として見る宣言。注釈の型は走査の対象外なので除く
TYPE_NODES_WITHOUT_ANNOTATION = TYPE_NODES - {"annotation_type_declaration"}

# メソッド相当の宣言の節点の正本。注釈の型の要素は本体を持たないので、歩く側が含めても変わらない
METHOD_NODES = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "compact_constructor_declaration",
        "annotation_type_element_declaration",
    }
)

# メソッドの呼び出しの節点
METHOD_INVOCATION = "method_invocation"
# 計算した値とみなす式（二項、メソッドの呼び出し、括弧）。`jigane.flaky.checks` が引く
COMPUTED_EXPRESSIONS = frozenset(
    {"binary_expression", METHOD_INVOCATION, "parenthesized_expression"}
)


def declared_visibility(node: Node, default: Visibility) -> Visibility:
    # 可視性の修飾子が無いときは、呼び出し側が文脈から決めた既定をそのまま返す。
    # protected は同じパッケージの外の継承先にも見えるので、public と同じ側に置く
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return default
    types = {c.type for c in modifiers.children}
    if "private" in types:
        return Visibility.FILE
    if types & {"public", "protected"}:
        return Visibility.PUBLIC
    return default
