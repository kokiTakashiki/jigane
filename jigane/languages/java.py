import re
from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages import calls_core, java_concurrency, type_names
from jigane.languages.base import (
    Argument,
    BodyBounds,
    Branch,
    BranchArm,
    CallParts,
    CallSite,
    ConcurrencyStep,
    Declaration,
    DeclarationKind,
    DeferredSite,
    Import,
    Isolation,
    IsolationContext,
    Locals,
    LocalVariable,
    MemberDecl,
    MemberKind,
    ParameterDecl,
    ReceiverKind,
    RegexScan,
    Return,
    Sharing,
    Step,
    TypeDecl,
    TypeKind,
    Visibility,
    import_nodes,
    leading_components,
    split_declaration,
)
from jigane.languages.calls_core import Captures
from jigane.languages.java_regex import regex_scan as _regex_scan
from jigane.languages.queries import load_queries
from jigane.preconditions.required import required

_TYPE_NODES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    }
)
_METHOD_NODES = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "compact_constructor_declaration",
        "annotation_type_element_declaration",
    }
)
_FIELD_NODES = frozenset({"field_declaration", "constant_declaration"})
_TYPE_KINDS: dict[str, TypeKind] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "interface",
}
# ラムダの本体と、無名クラスと局所クラスの本体。本体の呼び出しは遅延参照として別に報告する
_DEFERRED_BODIES = frozenset({"lambda_expression", "class_body"})
_COMMENTS = frozenset({"line_comment", "block_comment"})
# 無名クラスの識別の綴りと図の表示の綴りは、
# ここ（_anonymous_name と _ANONYMOUS_NAME）だけが決める。
# 収集は _anonymous_name、図の参加者名は anonymous_class_display を引く。
# 例 `TypeAdapters.URL.<anonymous1>.read`。同じ宣言の中の出現の順に 1 から振る
_ANONYMOUS_NAME = re.compile(r"^(?P<owner>.*)\.<anonymous(?P<ordinal>\d+)>$")


def _anonymous_name(ordinal: int) -> str:
    return f"<anonymous{ordinal}>"


def _text(node: Node) -> str:
    return required(node.text).decode()


def _statements(node: Node) -> list[Node]:
    # 波括弧の無い腕と枝は、文一つがそのまま本体になる
    return list(node.named_children) if node.type == "block" else [node]


def _member_text(expression: Node | None) -> str | None:
    # 値は Kind.RANGE の形のメンバ参照だけを残す。それ以外の式は値なしの終わりとして扱う
    return (
        _text(expression) if expression is not None and expression.type == "field_access" else None
    )


def _steps(statements: list[Node], *, is_value: bool) -> list[Step]:
    """文の並びから Return と Branch だけを残す。is_value は switch 式の腕の中であることを示す。"""
    steps: list[Step] = []
    for statement in statements:
        kind = statement.type
        if kind in ("return_statement", "yield_statement"):
            expression = statement.named_children[0] if statement.named_children else None
            if expression is not None and expression.type == "switch_expression":
                steps.append(_switch_branch(expression, is_value=True))
            else:
                steps.append(Return(_member_text(expression)))
        elif kind == "throw_statement":
            steps.append(Return(None))
        elif kind == "if_statement":
            steps.append(_if_branch(statement))
        elif kind == "switch_expression":
            steps.append(_switch_branch(statement, is_value=False))
        elif kind == "expression_statement" and is_value and statement.named_children:
            steps.append(Return(_member_text(statement.named_children[0])))
        else:
            # 他の文は分岐も終わりも作らない。ループや try の中の分岐は読まない
            continue
    return steps


def _if_branch(statement: Node) -> Branch:
    """if の偽の腕に、else if の連なりを入れ子にして入れる。"""
    condition = statement.child_by_field_name("condition")
    consequence = statement.child_by_field_name("consequence")
    alternative = statement.child_by_field_name("alternative")
    text = _text(condition)[1:-1].strip() if condition is not None else "?"
    arms = [
        BranchArm(
            "yes", tuple(_steps(_statements(consequence), is_value=False)) if consequence else ()
        )
    ]
    if alternative is None:
        return Branch(text, tuple(arms), "no")
    arms.append(BranchArm("no", tuple(_steps(_statements(alternative), is_value=False))))
    return Branch(text, tuple(arms), None)


def _switch_branch(statement: Node, *, is_value: bool) -> Branch:
    subject = next(
        (c for c in statement.named_children if c.type == "parenthesized_expression"), None
    )
    block = next((c for c in statement.named_children if c.type == "switch_block"), None)
    arms: list[BranchArm] = []
    labels: list[str] = []
    for clause in block.named_children if block is not None else []:
        label = next((c for c in clause.named_children if c.type == "switch_label"), None)
        if label is None:
            continue
        labels.append(_text(label).removeprefix("case ").strip())
        if clause.type == "switch_rule":
            body = clause.named_children[-1]
            nodes = [] if body is label else _statements(body)
        else:
            nodes = [c for c in clause.named_children if c is not label]
            if not nodes:
                # 本体が空のラベルは次のラベルへ落ちるので、次の腕の名前に束ねる
                continue
        name = ", ".join(labels) if labels != ["default"] else "default"
        arms.append(
            BranchArm(
                f"case {name}" if name != "default" else name,
                tuple(_steps(nodes, is_value=is_value)),
            )
        )
        labels = []
    subject_text = _text(subject)[1:-1].strip() if subject is not None else ""
    return Branch(f"switch {subject_text}".strip(), tuple(arms), None)


def _declared(node: Node, default: Visibility) -> Visibility:
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return default
    types = {c.type for c in modifiers.children}
    if "private" in types:
        return Visibility.FILE
    if types & {"public", "protected"}:
        return Visibility.PUBLIC
    return default


def _name(node: Node) -> str:
    name_node = node.child_by_field_name("name")
    name_node = required(name_node)
    return _text(name_node)


def _annotation(type_node: Node | None) -> str | None:
    # var は型を初期化式から決めるので、注記を持たない
    if type_node is None or _text(type_node) == "var":
        return None
    return _text(type_node)


def _arguments(node: Node, captured: Node | None = None) -> tuple[Argument, ...]:
    arguments = captured or node.child_by_field_name("arguments")
    if arguments is None:
        return ()
    # Java に名前付き引数は無い
    return tuple(Argument(a, None) for a in arguments.named_children if a.type not in _COMMENTS)


def _call_parts(call: Node) -> CallParts:
    name = call.child_by_field_name("name")
    return CallParts(
        call.child_by_field_name("object"),
        _text(name) if name is not None else None,
        _arguments(call),
        _text(call),
    )


def _call_site(captures: dict[str, list[Node]]) -> CallSite:
    node = captures["call"][0]
    callee = captures["callee"][0]
    receiver = captures["receiver"][0] if "receiver" in captures else None
    return CallSite(
        node,
        _text(callee),
        receiver,
        _arguments(node, captures["arguments"][0]),
        node.type == "object_creation_expression",
        node.end_byte,
        callee.start_point.row + 1,
    )


def _parameters(plist: Node | None) -> tuple[ParameterDecl, ...]:
    if plist is None:
        return ()
    result: list[ParameterDecl] = []
    for p in plist.named_children:
        if p.type == "formal_parameter":
            type_node = p.child_by_field_name("type")
            name_node = p.child_by_field_name("name")
            if type_node is not None and name_node is not None:
                result.append(ParameterDecl(_text(name_node), _text(type_node), optional=False))
        elif p.type == "spread_parameter":
            # 可変長の引数は 0 個でも呼べる
            declarator = next((c for c in p.children if c.type == "variable_declarator"), None)
            type_node = next(
                (c for c in p.named_children if c.type != "variable_declarator"), None
            )
            if declarator is not None and type_node is not None:
                name_node = required(declarator.child_by_field_name("name"))
                result.append(ParameterDecl(_text(name_node), _text(type_node), optional=True))
        else:
            # 受け手の仮引数 `Foo this` など。呼び出しの引数に対応しない
            continue
    return tuple(result)


def _bases(node: Node) -> tuple[str, ...]:
    bases: list[str] = []
    for child in node.children:
        if child.type == "superclass" and child.named_children:
            bases.append(_text(child.named_children[0]))
        elif child.type in ("super_interfaces", "extends_interfaces"):
            type_list = next((c for c in child.children if c.type == "type_list"), None)
            if type_list is not None:
                bases.extend(_text(t) for t in type_list.named_children)
    return tuple(bases)


def _members_of(body: Node) -> Iterator[Node]:
    for child in body.children:
        if child.type == "enum_body_declarations":
            yield from child.children
        else:
            yield child


def _anonymous_creations(node: Node) -> Iterator[Node]:
    """node の中の無名クラスの生成式を、最も外側のものだけ返す。内側は本体を辿り直して得る。"""
    for child in node.children:
        if child.type == "object_creation_expression" and _anonymous_body(child) is not None:
            yield child
        elif child.type in _TYPE_NODES:
            # 局所クラスの本体は、無名クラスの入口を持たない（宣言としても索引にも載せない）
            continue
        else:
            yield from _anonymous_creations(child)


def _anonymous_body(creation: Node) -> Node | None:
    return next((c for c in creation.children if c.type == "class_body"), None)


def _lambda_parameter_names(lambda_node: Node) -> list[str]:
    parameters = lambda_node.child_by_field_name("parameters")
    if parameters is None:
        return []
    if parameters.type == "identifier":
        return [_text(parameters)]
    names: list[str] = []
    for p in parameters.named_children:
        if p.type == "identifier":
            names.append(_text(p))
        elif p.type == "formal_parameter":
            names.append(_text(required(p.child_by_field_name("name"))))
        else:
            # 可変長の仮引数など。名前を束縛しても受け手の解決に影響しない
            continue
    return names


class _JavaRules(calls_core.RuleDefaults):
    """calls_core の三つの契約を満たす、Java の節点の名前の表と判定。"""

    deferred_bodies = _DEFERRED_BODIES
    self_types = frozenset({"this"})
    super_types = frozenset({"super"})
    name_types = frozenset({"identifier"})
    call_types = frozenset({"method_invocation"})
    creation_types = frozenset({"object_creation_expression"})
    member_type = "field_access"
    member_base_field = "object"
    member_name_field = "field"
    lambda_types = frozenset({"lambda_expression"})

    def call_site(self, captures: Captures) -> CallSite | None:
        return _call_site(captures)

    def method_group(self, captures: Captures) -> DeferredSite:
        node = captures["method_group"][0]
        return DeferredSite(node, _text(node.children[-1]), node.children[0], "method_group")

    def call_parts(self, call: Node) -> CallParts:
        return _call_parts(call)

    def arguments(self, node: Node) -> tuple[Argument, ...]:
        return _arguments(node)

    def lambda_parameter_names(self, node: Node) -> list[str]:
        return _lambda_parameter_names(node)

    def variables_of(self, node: Node) -> list[LocalVariable]:
        variables: list[LocalVariable] = []
        if node.type == "local_variable_declaration":
            type_node = node.child_by_field_name("type")
            for declarator in node.children_by_field_name("declarator"):
                name = _text(required(declarator.child_by_field_name("name")))
                value = declarator.child_by_field_name("value")
                variables.append(LocalVariable(name, _annotation(type_node), value))
        elif node.type in ("enhanced_for_statement", "resource", "catch_formal_parameter"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                # catch の型の並び（A | B）は一つの型に決まらないので、注記を持たない
                type_node = node.child_by_field_name("type") or next(
                    (c for c in node.children if c.type == "catch_type"), None
                )
                multi = type_node is not None and "|" in _text(type_node)
                annotation = None if multi else _annotation(type_node)
                value = node.child_by_field_name("value")
                variables.append(LocalVariable(_text(name_node), annotation, value))
        elif node.type == "instanceof_expression":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                right = node.child_by_field_name("right")
                variables.append(LocalVariable(_text(name_node), _annotation(right), None))
        return variables


_RULES = _JavaRules()


class JavaAdapter:
    name = "java"
    grammar = "java"
    suffixes = frozenset({".java"})
    constructs_by_call = False
    positional_argument_labels = False
    entry_point_names = frozenset(("main",))
    constructor_display_name = None
    comment_node_types = frozenset({"line_comment", "block_comment"})
    import_node_types = frozenset({"import_declaration"})
    test_directory_names = ("test",)
    test_file_patterns = ("*Test.java", "*Tests.java", "*IT.java")
    build_file_patterns = (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "libs.versions.toml",
    )

    def simple_type_name(self, raw: str) -> str:
        return type_names.generic_simple_type_name(raw)

    def composition_type_names(self, _raw: str, /) -> list[str]:
        return []

    def is_external_type_name(self, name: str) -> bool:
        return type_names.capitalized_is_external_type(name)

    def regex_literals(self, root_node: Node, _source: bytes, path: str, /) -> RegexScan:
        return _regex_scan(root_node, path)

    def imports(self, root_node: Node, _source: bytes, /) -> list[Import]:
        found: list[Import] = []
        for node in import_nodes(self, root_node):
            module = next(
                (c for c in node.children if c.type in ("scoped_identifier", "identifier")), None
            )
            if module is not None:
                found.append(Import(_text(module), module.start_point.row + 1))
        return found

    def technology_of(self, module: str) -> str:
        return leading_components(module, 2)

    def imported_names(self, _root_node: Node, _source: bytes, _path: str, /) -> dict[str, str]:
        # import は型の名前を束縛するが、索引は単純名で型を引くので、受け手の解決に使う表は持たない
        return {}

    def discriminator_branches(self, body_node: Node, _source: bytes, /) -> list[Step] | None:
        return _steps(_statements(body_node), is_value=False)

    def type_members(
        self, root_node: Node, _source: bytes, path: str, /
    ) -> list[TypeDecl | MemberDecl]:
        result: list[TypeDecl | MemberDecl] = []
        for node in root_node.children:
            if node.type in _TYPE_NODES:
                self._collect_type(node, "", path, result)
            else:
                # package、import、モジュール宣言は型を持たない
                continue
        return result

    def isolation_of(
        self, declaration_node: Node, _source: bytes, context: IsolationContext, /
    ) -> Isolation:
        return java_concurrency.isolation_of(declaration_node, context)

    def is_async(self, member_node: Node) -> bool:
        return java_concurrency.is_async(member_node)

    def concurrency_steps(
        self, body: Node, source: bytes, enclosing: Isolation
    ) -> list[ConcurrencyStep]:
        return java_concurrency.concurrency_steps(self, body, source, enclosing)

    def is_ui_type(
        self, type_name: str, context: IsolationContext, scope: Node | None = None
    ) -> bool:
        return java_concurrency.is_ui_type(type_name, context, scope)

    def is_main_thread(self, domain: Isolation) -> bool:
        return java_concurrency.is_main_thread(domain)

    def sharing_of(self, _type_name: str, _context: IsolationContext, /) -> Sharing:
        # Java の共有の札は別の段で実装するので、札は出さない
        return Sharing.UNKNOWN

    def body_of(self, member_node: Node) -> Node | None:
        return member_node.child_by_field_name("body")

    def call_sites(self, body_node: Node, _source: bytes, /) -> list[CallSite | DeferredSite]:
        return calls_core.call_sites(body_node, load_queries(self.grammar), _RULES)

    def receiver_of(self, expression: Node) -> ReceiverKind:
        return calls_core.receiver_of(expression, _RULES)

    def locals_of(self, body_node: Node) -> Locals:
        return calls_core.locals_of(body_node, _RULES)

    def _collect_type(
        self, node: Node, prefix: str, path: str, result: list[TypeDecl | MemberDecl]
    ) -> None:
        name = _name(node)
        qualified = f"{prefix}{name}"
        line = node.start_point.row + 1
        result.append(
            TypeDecl(qualified, name, _TYPE_KINDS[node.type], _bases(node), path, line, node)
        )
        # レコードの成分は、型のフィールドのように参照できる
        components = _parameters(node.child_by_field_name("parameters"))
        result.extend(
            MemberDecl(qualified, p.name, "parameter", p.type_annotation, (), line, node)
            for p in components
        )
        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in _members_of(body):
            self._collect_member(member, name, qualified, path, components, result=result)

    def _collect_member(
        self,
        node: Node,
        owner_name: str,
        owner: str,
        path: str,
        components: tuple[ParameterDecl, ...],
        *,
        result: list[TypeDecl | MemberDecl],
    ) -> None:
        line = node.start_point.row + 1

        def make(
            name: str,
            kind: MemberKind,
            type_node: Node | None,
            parameters: tuple[ParameterDecl, ...],
            body: Node | None = None,
        ) -> MemberDecl:
            annotation = _text(type_node) if type_node is not None else None
            return MemberDecl(owner, name, kind, annotation, parameters, line, node, body)

        if node.type in _TYPE_NODES:
            self._collect_type(node, f"{owner}.", path, result)
        elif node.type == "method_declaration":
            result.append(
                make(
                    _name(node),
                    "method",
                    node.child_by_field_name("type"),
                    _parameters(node.child_by_field_name("parameters")),
                    node.child_by_field_name("body"),
                )
            )
            self._collect_anonymous(node, f"{owner}.{_name(node)}", path, result)
        elif node.type == "constructor_declaration":
            result.append(
                make(
                    owner_name,
                    "constructor",
                    None,
                    _parameters(node.child_by_field_name("parameters")),
                    node.child_by_field_name("body"),
                )
            )
            self._collect_anonymous(node, f"{owner}.{_name(node)}", path, result)
        elif node.type == "compact_constructor_declaration":
            # 引数はレコードの成分と同じ
            result.append(
                make(owner_name, "constructor", None, components, node.child_by_field_name("body"))
            )
            self._collect_anonymous(node, f"{owner}.{_name(node)}", path, result)
        elif node.type in _FIELD_NODES:
            type_node = node.child_by_field_name("type")
            for declarator in node.children_by_field_name("declarator"):
                result.append(make(_name(declarator), "field", type_node, ()))
                self._collect_anonymous(declarator, f"{owner}.{_name(declarator)}", path, result)
        else:
            # 列挙子、初期化子、注釈の要素、記号など。受け手の解決の対象にしない
            return

    def _collect_anonymous(
        self, node: Node, qualified: str, path: str, result: list[TypeDecl | MemberDecl]
    ) -> None:
        """node の中の無名クラスを、`<qualified>.<anonymous>` という型にして、そのメンバを集める。

        同じ node の中の無名クラスは、出現の順の番号で別の名前になる。
        """
        for ordinal, creation in enumerate(_anonymous_creations(node), start=1):
            body = _anonymous_body(creation)
            type_node = creation.child_by_field_name("type")
            name = _anonymous_name(ordinal)
            owner = f"{qualified}.{name}"
            bases = (_text(type_node),) if type_node is not None else ()
            result.append(
                TypeDecl(owner, name, "class", bases, path, creation.start_point.row + 1, creation)
            )
            for member in _members_of(required(body)):
                self._collect_member(member, name, owner, path, (), result=result)

    def _collect_anonymous_declarations(
        self, node: Node, qualified: str, result: list[Declaration]
    ) -> None:
        for ordinal, creation in enumerate(_anonymous_creations(node), start=1):
            # 無名クラスのメンバは、外へ公開されない。可視性は FILE に抑える
            self._collect(
                required(_anonymous_body(creation)),
                f"{qualified}.{_anonymous_name(ordinal)}.",
                Visibility.FILE,
                Visibility.INTERNAL,
                result,
            )

    def anonymous_class_display(self, qualified: str) -> str | None:
        found = _ANONYMOUS_NAME.match(qualified)
        if found is None:
            return None
        enclosing = found["owner"].rsplit(".", 1)[-1]
        return f"{enclosing} の無名クラス {found['ordinal']}"

    def declarations(self, root_node: Node, _source: bytes, /) -> list[Declaration]:
        result: list[Declaration] = []
        self._collect(root_node, "", Visibility.PUBLIC, Visibility.INTERNAL, result)
        return result

    def _collect(
        self,
        container: Node,
        prefix: str,
        cap: Visibility,
        default: Visibility,
        result: list[Declaration],
    ) -> None:
        for member in container.children:
            if member.type == "enum_body_declarations":
                # セミコロンより後の宣言は、enum の本体の直下ではなくこの節点に入る
                self._collect(member, prefix, cap, default, result)
                continue
            # 修飾子の無い要素は、class では package-private、interface では public
            visibility = _declared(member, default).narrower(cap)
            if member.type in _TYPE_NODES:
                qualified = f"{prefix}{_name(member)}"
                result.append(self._declaration(member, qualified, "class", visibility))
                body = member.child_by_field_name("body")
                body = required(body)
                is_interface = member.type in (
                    "interface_declaration",
                    "annotation_type_declaration",
                )
                self._collect(
                    body,
                    f"{qualified}.",
                    visibility,
                    Visibility.PUBLIC if is_interface else Visibility.INTERNAL,
                    result,
                )
            elif member.type in _METHOD_NODES:
                qualified = f"{prefix}{_name(member)}"
                result.append(self._declaration(member, qualified, "method", visibility))
                self._collect_anonymous_declarations(member, qualified, result)
            elif member.type in _FIELD_NODES:
                # `int a, b;` は二つの宣言。範囲は共有する
                for declarator in member.children_by_field_name("declarator"):
                    qualified = f"{prefix}{_name(declarator)}"
                    result.append(self._declaration(member, qualified, "variable", visibility))
                    self._collect_anonymous_declarations(declarator, qualified, result)
            elif member.type == "enum_constant":
                # 定数ごとの本体（無名のサブクラス）は宣言にしない
                qualified = f"{prefix}{_name(member)}"
                result.append(self._declaration(member, qualified, "variable", cap))
            else:
                continue

    @staticmethod
    def _declaration(
        node: Node, qualified: str, kind: DeclarationKind, visibility: Visibility
    ) -> Declaration:
        body = node.child_by_field_name("body")
        has_body = kind != "variable" and node.type != "enum_constant"
        spans = split_declaration(
            node, BodyBounds(body, body) if has_body and body is not None else None
        )
        return Declaration(qualified, kind, visibility, spans.span, spans.signature, spans.body)
