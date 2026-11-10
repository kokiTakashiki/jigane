"""tree-sitter の節点の文字列を取る補助。"""

from tree_sitter import Node

from jigane.preconditions.required import required


def text_of(node: Node) -> str:
    """節点の文字列を返す。text が無い節点は不正として止める。"""
    return required(node.text).decode()


def text_or_empty(node: Node | None) -> str:
    """節点の文字列を返す。節点が None、または text が無いときは空文字列。"""
    return (node.text or b"").decode() if node is not None else ""
