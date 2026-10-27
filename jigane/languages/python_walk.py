"""Python の構文木を読む二つのモジュールが共有する足場。"""

from tree_sitter import Node

from jigane.preconditions.required import required


def enclosing_function(node: Node) -> Node | None:
    parent = node.parent
    while parent is not None and parent.type != "function_definition":
        parent = parent.parent
    return parent


def call_arguments(call: Node) -> tuple[list[Node], dict[str, Node]]:
    """呼び出しの位置引数と、名前付き引数。展開（`*`、`**`）とコメントは含めない。"""
    positional: list[Node] = []
    keywords: dict[str, Node] = {}
    arguments = call.child_by_field_name("arguments")
    for child in arguments.named_children if arguments is not None else ():
        if child.type == "keyword_argument":
            name = child.child_by_field_name("name")
            value = child.child_by_field_name("value")
            if name is not None and value is not None:
                keywords[required(name.text).decode()] = value
        elif child.type not in ("comment", "dictionary_splat", "list_splat"):
            positional.append(child)
    return positional, keywords
