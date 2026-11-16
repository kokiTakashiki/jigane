from collections.abc import Iterator
from dataclasses import dataclass

from tree_sitter import Node

from jigane.languages.file_paths_core import text
from jigane.languages.value_set_core import plain_string, walk_nodes

# 型を宣言する節点。`extension` も `class_declaration` で表されるので、
# 区別が要る側は TYPE_KEYWORDS で見分ける。
# `swift`、`swift_concurrency`、`swift_public_names` が引く
TYPE_DECLARATIONS = frozenset({"class_declaration", "protocol_declaration"})
# `class_declaration` が型の宣言であって `extension` でないことを示すキーワード。
# `swift_public_names` と `swift_type_fields` が引く
TYPE_KEYWORDS = frozenset({"struct", "class", "actor", "enum"})
# 関数、プロトコルの要件、初期化子の宣言。`swift_public_names` が引く
FUNCTION_NODES = frozenset(
    {"function_declaration", "protocol_function_declaration", "init_declaration"}
)
# `func` で宣言する関数。プロトコルの要件を含み、初期化子は含まない
NAMED_FUNCTION_NODES = FUNCTION_NODES - {"init_declaration"}
# 本体を持つ関数と初期化子の宣言。プロトコルの要件（protocol_function_declaration）は含まない。
# `swift_regex`、`swift_stores`、`swift_file_paths` が引く
DECLARED_FUNCTION_NODES = frozenset({"function_declaration", "init_declaration"})
# 一行、複数行、raw の三種の文字列リテラル。`swift_regex` と `jigane.flaky.checks` が引く
STRING_LITERALS = frozenset(
    {"line_string_literal", "multi_line_string_literal", "raw_string_literal"}
)


def string_literal(node: Node) -> str | None:
    # 補間やエスケープを含まない一行の文字列リテラルの本文だけを返す。
    # 複数行と raw の文字列は、呼ぶ側がまだ読まないので None
    if node.type != "line_string_literal":
        return None
    return plain_string(node, {"line_str_text"})


@dataclass(frozen=True)
class IdentifierName:
    """識別子で書かれた関数の名前。"""

    node: Node


@dataclass(frozen=True)
class OperatorName:
    """演算子で書かれた関数の名前。識別子でないので、名前として読まない記述にあたる。"""

    node: Node


@dataclass(frozen=True)
class InitName:
    """初期化子の名前（`init` キーワード）。関数の名前ではないので、読めない記述にあたらない。

    `swift` と `swift_stores` が、関数と同じ単位の名前として `node` を読む。
    `OperatorName` と型を分けるのは、`operator_function_nodes` が演算子だけを数えるため。
    """

    node: Node


# 名前を取れなければ None
FunctionName = IdentifierName | OperatorName | InitName | None


def function_name_of(node: Node) -> FunctionName:
    """関数と初期化子の宣言から、名前の節点を種類ごとの型で引く。

    `init_declaration` は `func` を持たないので `name` フィールドを名前の節点とする。
    """
    if node.type == "init_declaration":
        name_node = node.child_by_field_name("name")
    else:
        keyword = next((c for c in node.children if c.type == "func"), None)
        name_node = keyword.next_sibling if keyword is not None else None
    if name_node is None:
        return None
    if node.type == "init_declaration":
        return InitName(name_node)
    if name_node.type == "simple_identifier":
        return IdentifierName(name_node)
    return OperatorName(name_node)


def operator_function_nodes(root: Node) -> Iterator[Node]:
    """名前を取れない演算子関数の宣言の節点。初期化子は `func` の名前を持たないので数えない。"""
    for node in walk_nodes(root):
        if node.type in NAMED_FUNCTION_NODES and isinstance(function_name_of(node), OperatorName):
            yield node


class SwiftCallSite:
    """`呼び出し先(引数...)` の呼び出し先の名前、受け手の原文、ラベル付きの引数。

    `swift_file_paths` と `swift_store_*` の三つの道が引く。
    """

    def __init__(self, node: Node) -> None:
        self.node = node
        self.name = ""
        self.receiver: Node | None = None
        self.arguments: dict[str, Node] = {}
        callee = node.children[0] if node.children else None
        suffix = next((c for c in node.children if c.type == "call_suffix"), None)
        if callee is None or suffix is None:
            return
        if callee.type == "simple_identifier":
            self.name = text(callee)
        elif callee.type == "navigation_expression":
            member = callee.child_by_field_name("suffix")
            member_name = member.child_by_field_name("suffix") if member is not None else None
            if member_name is not None:
                self.name = text(member_name)
            self.receiver = callee.child_by_field_name("target")
        for argument in (
            c for v in suffix.children if v.type == "value_arguments" for c in v.children
        ):
            if argument.type != "value_argument":
                continue
            label = argument.child_by_field_name("name")
            value = argument.child_by_field_name("value")
            # ラベルの無い引数は、どのメソッドも位置で読まないので捨てる
            if label is not None and value is not None:
                self.arguments[text(label)] = value
