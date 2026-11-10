from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import TypeKind, Visibility
from jigane.preconditions.required import required

TYPE_KINDS: dict[str, TypeKind] = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "record_declaration": "record",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "delegate_declaration": "delegate",
}

# 列挙型と delegate は、メンバーを持つ本体の走査の対象から外す。
TYPE_DECLARATIONS = frozenset(
    node_type for node_type, kind in TYPE_KINDS.items() if kind not in {"enum", "delegate"}
)

# 通常、逐語、raw の三種の文字列リテラル。補間文字列は含まない
STRING_LITERALS = frozenset({"string_literal", "verbatim_string_literal", "raw_string_literal"})

# メソッド、コンストラクタ、ローカル関数、アクセサの本体。呼び出せるものの芯で、
# 各モジュールはここへ足し引きして使う
CALLABLE_BODIES = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "accessor_declaration",
    }
)
LAMBDAS = frozenset({"lambda_expression", "anonymous_method_expression"})

NAMESPACES = frozenset({"namespace_declaration", "file_scoped_namespace_declaration"})
# #if の腕は、その中の宣言を子として抱える
PREPROCESSOR = frozenset({"preproc_if", "preproc_elif", "preproc_else"})
# メソッドの呼び出しの節点
INVOCATION = "invocation_expression"
# 計算した値とみなす式（二項、呼び出し、括弧）。`jigane.flaky.checks` が引く
COMPUTED_EXPRESSIONS = frozenset({"binary_expression", INVOCATION, "parenthesized_expression"})


def flatten(container: Node) -> Iterator[Node]:
    for child in container.children:
        if child.type in PREPROCESSOR:
            yield from flatten(child)
        else:
            yield child


def namespace_container(namespace: Node) -> Node:
    # ブロック形の namespace は body の中に宣言を持ち、ファイルスコープ形は自分自身が入れ物になる
    body = namespace.child_by_field_name("body")
    return body if body is not None else namespace


def modifiers(node: Node) -> set[str]:
    return {required(child.text).decode() for child in node.children if child.type == "modifier"}


def declared_visibility(node: Node, default: Visibility) -> Visibility:
    # 可視性の修飾子が無いときは、呼び出し側が文脈から決めた既定をそのまま返す。
    found = modifiers(node)
    # private protected は派生型からしか見えず、file はそのファイルの中でしか見えない
    if "private" in found or "file" in found:
        return Visibility.FILE
    # protected internal は protected を含むので、internal より先に判定する
    if found & {"public", "protected"}:
        return Visibility.PUBLIC
    if "internal" in found:
        return Visibility.INTERNAL
    return default
