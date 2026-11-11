from collections.abc import Iterator
from dataclasses import dataclass

from tree_sitter import Node

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
    """初期化子の名前（`init` キーワード）。関数の名前ではないので、読めない記述にあたらない。"""

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
