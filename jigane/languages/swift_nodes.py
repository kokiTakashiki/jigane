from tree_sitter import Node

from jigane.languages.value_set_core import plain_string

# 型を宣言する節点。`extension` も `class_declaration` で表されるので、
# 区別が要る側は TYPE_KEYWORDS で見分ける。
# `swift`、`swift_concurrency`、`swift_public_names` が引く
TYPE_DECLARATIONS = frozenset({"class_declaration", "protocol_declaration"})
# `class_declaration` が型の宣言であって `extension` でないことを示すキーワード。
# `swift_public_names` と `swift_type_fields` が引く
TYPE_KEYWORDS = frozenset({"struct", "class", "actor", "enum"})
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
