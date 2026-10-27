"""宣言の一覧だけを持つ Kotlin の言語アダプタ。外の配布から jigane に言語を足す例。"""

from importlib import metadata

from tree_sitter import Node

from jigane.languages.api import (
    PLUGIN_API_VERSION,
    BodyBounds,
    Declaration,
    DeclarationKind,
    Distribution,
    GrammarSpec,
    LanguagePlugin,
    Visibility,
    split_declaration,
)

DISTRIBUTION = "jigane-language-kotlin"

# パックの Kotlin の共有ライブラリの sha256。
# platform の名前は jigane の platform_key() と同じ表記。
# パックの版を上げたら、リリースの tarball から取り出して取り直す。
GRAMMAR_HASHES = {
    "linux-aarch64": "c9d4b6e4694dc849b1e97218acfa969ece44cc6e121a3e979e6c7d16436e5bb2",
    "linux-x86_64": "40abf2de70b5b67912e204c65d5d2b3e5135865d2f234db1f221d0c46162a4c1",
    "macos-arm64": "5466918c559e2bf8f305c4f7baf05394bead8cef2edf5b860d456eba00fb3568",
    "macos-x86_64": "a86ec946df0796df7cf71584464d627e121601b8f16ee9597122900b2cb99f13",
    "windows-aarch64": "5c8c15ef9176089a9d5ecd94337ab62da5f5ac0c430baadaa3379f2ef748dd72",
    "windows-x86_64": "35b72c258320712a5c5a6e9bcb83835b5ef5f365526e381b5faa00ecfecda88d",
}

_TYPE_NODES = frozenset({"class_declaration", "object_declaration", "companion_object"})
_BODY_NODES = frozenset({"class_body", "enum_class_body", "function_body"})
_VISIBILITY = {
    "private": Visibility.FILE,
    "internal": Visibility.INTERNAL,
    "protected": Visibility.PUBLIC,
    "public": Visibility.PUBLIC,
}


def _text(node: Node) -> str:
    return (node.text or b"").decode()


def _child(node: Node, *types: str) -> Node | None:
    return next((c for c in node.children if c.type in types), None)


def _name(node: Node) -> str | None:
    """宣言の名前。名前を持たない宣言（分解宣言、名前の無い companion object）では None。"""
    if node.type == "property_declaration":
        variable = _child(node, "variable_declaration")
        identifier = _child(variable, "simple_identifier") if variable is not None else None
    elif node.type == "companion_object":
        # 名前を書かない companion object は Companion という名前を持つ
        identifier = _child(node, "type_identifier")
        return _text(identifier) if identifier is not None else "Companion"
    elif node.type in _TYPE_NODES:
        identifier = _child(node, "type_identifier")
    else:
        identifier = _child(node, "simple_identifier")
    return _text(identifier) if identifier is not None else None


def _declared(node: Node) -> Visibility:
    modifiers = _child(node, "modifiers")
    if modifiers is None:
        return Visibility.PUBLIC
    for modifier in modifiers.children:
        if modifier.type == "visibility_modifier":
            return _VISIBILITY.get(_text(modifier), Visibility.PUBLIC)
    return Visibility.PUBLIC


def _declaration(
    node: Node, qualified: str, kind: DeclarationKind, visibility: Visibility
) -> Declaration:
    body = None if kind == "variable" else _child(node, *_BODY_NODES)
    spans = split_declaration(node, BodyBounds(body, body) if body is not None else None)
    return Declaration(qualified, kind, visibility, spans.span, spans.signature, spans.body)


class KotlinAdapter:
    name: str = "kotlin"
    grammar: str = "kotlin"
    suffixes: frozenset[str] = frozenset({".kt", ".kts"})
    comment_node_types: frozenset[str] = frozenset({"line_comment", "multiline_comment"})
    import_node_types: frozenset[str] = frozenset({"import_header"})
    test_directory_names: tuple[str, ...] = ("test", "androidTest")
    test_file_patterns: tuple[str, ...] = ("*Test.kt", "*Tests.kt", "*IT.kt")
    build_file_patterns: tuple[str, ...] = (
        "build.gradle.kts",
        "settings.gradle.kts",
        "build.gradle",
        "settings.gradle",
        "gradle.properties",
        "libs.versions.toml",
    )

    def declarations(self, root_node: Node, _source: bytes, /) -> list[Declaration]:
        result: list[Declaration] = []
        self._collect(root_node, "", Visibility.PUBLIC, result, in_type=False)
        return result

    def _collect(
        self,
        container: Node,
        prefix: str,
        cap: Visibility,
        result: list[Declaration],
        *,
        in_type: bool,
    ) -> None:
        for member in container.children:
            if member.type == "ERROR":
                # 文法が解釈しきれない箇所（本体の無い interface のメソッドなど）にも宣言は残る
                self._collect(member, prefix, cap, result, in_type=in_type)
                continue
            name = _name(member)
            if name is None:
                # 名前を持たない節点（分解宣言、記号、コメントなど）は宣言にしない
                continue
            visibility = _declared(member).narrower(cap)
            qualified = f"{prefix}{name}"
            if member.type in _TYPE_NODES:
                result.append(_declaration(member, qualified, "class", visibility))
                body = _child(member, "class_body", "enum_class_body")
                if body is not None:
                    self._collect(body, f"{qualified}.", visibility, result, in_type=True)
            elif member.type == "function_declaration":
                kind: DeclarationKind = "method" if in_type else "function"
                result.append(_declaration(member, qualified, kind, visibility))
            elif member.type == "property_declaration":
                result.append(_declaration(member, qualified, "variable", visibility))
            elif member.type == "enum_entry":
                result.append(_declaration(member, qualified, "variable", cap))
            else:
                # package、import、型の別名、初期化ブロックなど。宣言の一覧に載せない
                continue


def plugin() -> LanguagePlugin:
    distribution = metadata.distribution(DISTRIBUTION)
    return LanguagePlugin(
        origin=Distribution(distribution.name, distribution.version),
        api_version=PLUGIN_API_VERSION,
        adapter=KotlinAdapter(),
        label="Kotlin",
        grammar=GrammarSpec(
            pack_version=metadata.version("tree-sitter-language-pack"),
            hashes=GRAMMAR_HASHES,
        ),
        data=None,
    )
