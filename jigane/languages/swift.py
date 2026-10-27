from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages import calls_core, swift_concurrency, swift_regex, type_names
from jigane.languages.base import (
    Argument,
    BodyBounds,
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
    Sharing,
    Step,
    TypeDecl,
    TypeKind,
    TypeScope,
    Visibility,
    import_nodes,
    leading_components,
    split_declaration,
)
from jigane.languages.calls_core import Captures
from jigane.languages.queries import load_queries
from jigane.preconditions.required import required

_ASSIGNMENT_MIN_PARTS = 2  # 「=」と初期値

_TYPE_DECLARATIONS = frozenset({"class_declaration", "protocol_declaration"})
_FILE_LEVEL = frozenset({"private", "fileprivate"})
_PUBLIC_LEVEL = frozenset({"public", "open", "package"})
_LAMBDAS = frozenset({"lambda_literal"})
_SELECTORS = frozenset({"selector_expression"})


def _text(node: Node) -> str:
    return required(node.text).decode()


def _declared(node: Node) -> Visibility | None:
    # アクセス修飾子が無ければ None を返し、呼び出し側の既定の可視性にする
    for child in node.children:
        if child.type != "modifiers":
            continue
        for modifier in child.children:
            if modifier.type != "visibility_modifier":
                continue
            word = _text(modifier)
            # private(set) のような setter だけの制限は、読み出しの可視性を変えない
            if "(" in word:
                continue
            if word in _FILE_LEVEL:
                return Visibility.FILE
            if word in _PUBLIC_LEVEL:
                return Visibility.PUBLIC
            return Visibility.INTERNAL
    return None


def _parameter_label(parameter: Node) -> str:
    external = parameter.child_by_field_name("external_name")
    if external is not None:
        return _text(external)
    return _text(next(c for c in parameter.children if c.type == "simple_identifier"))


def _parameters(node: Node) -> list[Node]:
    return [c for c in node.children if c.type == "parameter"]


def _selector(name: str, node: Node, *, default_label: str | None = None) -> str:
    labels = ""
    for parameter in _parameters(node):
        if default_label is not None and parameter.child_by_field_name("external_name") is None:
            # サブスクリプトの引数は、外部名を書かないかぎりラベルを持たない
            labels += f"{default_label}:"
        else:
            labels += f"{_parameter_label(parameter)}:"
    return f"{name}({labels})"


def _bound_identifiers(node: Node) -> Iterator[Node]:
    if (
        node.type == "simple_identifier"
        and node.parent is not None
        and node.parent.type == "pattern"
    ):
        yield node
    for child in node.children:
        yield from _bound_identifiers(child)


_TYPE_KINDS: dict[str, TypeKind] = {
    "class": "class",
    "actor": "class",
    "struct": "struct",
    "enum": "enum",
    "extension": "class",
}


def _type_annotation_of(node: Node) -> str | None:
    annotation = next((c for c in node.children if c.type == "type_annotation"), None)
    if annotation is None:
        return None
    type_node = annotation.child_by_field_name("name")
    return _text(type_node) if type_node is not None else None


def _return_type_of(node: Node) -> str | None:
    # 関数の name フィールドは、関数名の識別子に続けて戻り値の型の節点を持つ
    names = node.children_by_field_name("name")
    return _text(names[1]) if len(names) > 1 else None


def _parameter_declarations(node: Node) -> tuple[ParameterDecl, ...]:
    found: list[ParameterDecl] = []
    for parameter in _parameters(node):
        names = parameter.children_by_field_name("name")
        external = parameter.child_by_field_name("external_name")
        following = parameter.next_sibling
        found.append(
            ParameterDecl(
                _text(names[0]),
                _text(names[-1]) if len(names) > 1 else "",
                # 既定値は仮引数の節点の外にあり、`=` が仮引数の直後に付く
                following is not None and following.type == "=",
                _text(external) if external is not None else None,
            )
        )
    return tuple(found)


def _bases_of(node: Node) -> tuple[str, ...]:
    return tuple(
        _text(required(c.child_by_field_name("inherits_from")))
        for c in node.children
        if c.type == "inheritance_specifier"
    )


def _navigation_name(navigation: Node) -> Node | None:
    suffix = navigation.child_by_field_name("suffix")
    return suffix.child_by_field_name("suffix") if suffix is not None else None


def _label_text(label: Node | None) -> str | None:
    return _text(label).rstrip(":").strip() if label is not None else None


def _arguments(suffix: Node | None) -> tuple[Argument, ...]:
    """call_suffix の引数。末尾のクロージャは、ラベルがあればそれを名前にして、最後に並べる。"""
    if suffix is None:
        return ()
    result: list[Argument] = []
    pending: str | None = None
    for child in suffix.children:
        if child.type == "value_arguments":
            for argument in child.children:
                value = argument.child_by_field_name("value")
                if argument.type == "value_argument" and value is not None:
                    result.append(
                        Argument(value, _label_text(argument.child_by_field_name("name")))
                    )
        elif child.type == "simple_identifier":
            pending = _text(child)
        elif child.type == "lambda_literal":
            result.append(Argument(child, pending))
            pending = None
    return tuple(result)


def _call_suffix(call: Node) -> Node | None:
    return next((c for c in call.children if c.type == "call_suffix"), None)


def _is_type_initializer(receiver: Node | None, name: str) -> bool:
    # self.init() と super.init() は別の初期化子へ委ねる呼び出しで、生成ではない
    return (
        name == "init"
        and receiver is not None
        and receiver.type not in ("self_expression", "super_expression")
    )


def _inside_selector(node: Node) -> bool:
    current = node.parent
    while current is not None:
        if current.type in _SELECTORS:
            return True
        current = current.parent
    return False


def _is_call_argument(closure: Node) -> bool:
    """クロージャが呼び出しの末尾のクロージャか、引数に渡したクロージャか。"""
    parent = closure.parent
    if parent is None:
        return False
    if parent.type == "call_suffix":
        return True
    return parent.type == "value_argument"


def _call_site(captures: Captures) -> CallSite | None:
    node = captures["call"][0]
    if _inside_selector(node):
        # #selector(...) の中の Foo.go(_:) は呼び出しではなく関数の参照
        return None
    callee = captures["callee"][0]
    receiver = captures["receiver"][0] if "receiver" in captures else None
    arguments = _arguments(captures["arguments"][0])
    if node.type == "constructor_expression":
        # Array<Int>() のようなジェネリクス付きの型の生成は、専用の節点になる
        return CallSite(
            node,
            _text(callee),
            None,
            arguments,
            is_creation=True,
            end=node.end_byte,
            line=callee.start_point.row + 1,
        )
    name = _text(callee)
    creation = _is_type_initializer(receiver, name)
    if creation:
        # 受け手の式が生成する型の名前になる
        name = _text(required(receiver))
        receiver = None
    return CallSite(
        node, name, receiver, arguments, creation, node.end_byte, callee.start_point.row + 1
    )


def _call_parts(call: Node) -> CallParts:
    function = call.children[0]
    arguments = _arguments(_call_suffix(call))
    text = _text(call)
    if function.type == "navigation_expression":
        name = _navigation_name(function)
        return CallParts(
            function.child_by_field_name("target"),
            _text(name) if name is not None else None,
            arguments,
            text,
        )
    if function.type == "simple_identifier":
        return CallParts(None, _text(function), arguments, text)
    return CallParts(None, None, arguments, text)


# 実行の前後を飾るだけで、値の型を変えない式
_TRANSPARENT = frozenset({"await_expression", "try_expression"})


def _wrapped(node: Node) -> Node | None:
    """値を包むだけの式（a!、(a)、try、await）の中身。包まない式では None。"""
    if node.type == "postfix_expression":
        return node.child_by_field_name("target")
    if node.type in _TRANSPARENT:
        return node.child_by_field_name("expr")
    if node.type == "tuple_expression" and len(node.children_by_field_name("value")) == 1:
        return node.children_by_field_name("value")[0]
    return None


def _pattern_identifiers(node: Node) -> Iterator[Node]:
    """パターンが束縛する名前。列挙子の名前（`.a(let e)` の a）は束縛ではないので除く。"""
    for position, child in enumerate(node.children):
        if child.type == "simple_identifier":
            after_dot = position > 0 and node.children[position - 1].type == "."
            if node.field_name_for_child(position) == "bound_identifier" or (
                node.type == "pattern" and not after_dot
            ):
                yield child
        elif child.type != "lambda_literal":
            yield from _pattern_identifiers(child)


def _descendants(node: Node, kind: str) -> Iterator[Node]:
    """node の下にある kind の節点。入れ子のクロージャの中は含めない。"""
    for child in node.children:
        if child.type == kind:
            yield child
        elif child.type != "lambda_literal":
            yield from _descendants(child, kind)


_CONDITIONAL_STATEMENTS = frozenset({"if_statement", "guard_statement", "while_statement"})


def _binding_pairs(node: Node) -> list[tuple[Node, str | None, Node | None]]:
    """if let / guard let / while let の条件列にある束縛を、(名前, 型注記, 初期化式) で返す。

    条件は節点の平らな子として並び、束縛は「名前 [型注記] = 式」の順に現れる。
    """
    pairs: list[tuple[Node, str | None, Node | None]] = []
    children = node.children
    for position, child in enumerate(children):
        if child.type != "simple_identifier" or node.field_name_for_child(position) != (
            "bound_identifier"
        ):
            continue
        annotation: str | None = None
        initializer: Node | None = None
        following = children[position + 1 :]
        if following and following[0].type == "type_annotation":
            annotation = _type_annotation_text(following[0])
            following = following[1:]
        if len(following) >= _ASSIGNMENT_MIN_PARTS and following[0].type == "=":
            initializer = following[1]
        pairs.append((child, annotation, initializer))
    return pairs


def _type_annotation_text(annotation: Node) -> str | None:
    type_node = annotation.child_by_field_name("name")
    return _text(type_node) if type_node is not None else None


def _typed_parameters(node: Node) -> list[LocalVariable]:
    return [
        LocalVariable(_text(names[0]), _text(names[-1]), None)
        for parameter in _parameters(node)
        if len(names := parameter.children_by_field_name("name")) > 1
    ]


class _SwiftRules(calls_core.RuleDefaults):
    """calls_core の三つの契約を満たす、Swift の節点の名前の表と判定。"""

    deferred_bodies = _LAMBDAS
    self_types = frozenset({"self_expression"})
    super_types = frozenset({"super_expression"})
    name_types = frozenset({"simple_identifier"})
    # 型の中の Self は、含む型自身
    self_names = frozenset({"Self"})
    call_types = frozenset({"call_expression"})
    creation_types = frozenset({"constructor_expression"})
    creation_type_field = "constructed_type"
    member_type = "navigation_expression"
    member_base_field = "target"
    lambda_types = _LAMBDAS

    def call_site(self, captures: Captures) -> CallSite | None:
        return _call_site(captures)

    def wrapped(self, node: Node) -> Node | None:
        return _wrapped(node)

    def member_name(self, node: Node) -> Node | None:
        return _navigation_name(node)

    def creation_name(self, parts: CallParts) -> str | None:
        if parts.name is not None and _is_type_initializer(parts.receiver, parts.name):
            return _text(required(parts.receiver))
        return None

    def call_parts(self, call: Node) -> CallParts:
        return _call_parts(call)

    def arguments(self, node: Node) -> tuple[Argument, ...]:
        return _arguments(next((c for c in node.children if c.type == "constructor_suffix"), None))

    def function_name(self, node: Node) -> str | None:
        return (
            SwiftAdapter.function_declaration_name(node)
            if node.type == "function_declaration"
            else None
        )

    def lambda_parameter_names(self, node: Node) -> list[str]:
        return [
            _text(names[0])
            for parameter in _descendants(node, "lambda_parameter")
            if len(names := parameter.children_by_field_name("name")) <= 1
        ]

    def variables_of(self, node: Node) -> list[LocalVariable]:
        variables: list[LocalVariable] = []
        kind = node.type
        if kind == "property_declaration":
            names = [
                identifier
                for name_node in node.children_by_field_name("name")
                for identifier in _bound_identifiers(name_node)
            ]
            # 複数の名前を束縛する let (a, b) = ... では、型注記も初期化式も各名前に対応しない
            single = len(names) == 1
            annotation = _type_annotation_of(node) if single else None
            initializer = node.child_by_field_name("value") if single else None
            variables.extend(LocalVariable(_text(name), annotation, initializer) for name in names)
        elif kind == "for_statement":
            variables.extend(_for_variables(node))
        elif kind in _CONDITIONAL_STATEMENTS:
            variables.extend(
                LocalVariable(_text(name), annotation, initializer)
                for name, annotation, initializer in _binding_pairs(node)
            )
        elif kind == "switch_pattern":
            # case .a(let e) の e は型を書かないので、同名のフィールドを隠すためだけに記録する
            variables.extend(
                LocalVariable(_text(name), None, None) for name in _pattern_identifiers(node)
            )
        elif kind == "function_declaration":
            variables.extend(_typed_parameters(node))
        elif kind in _LAMBDAS:
            variables.extend(
                LocalVariable(_text(names[0]), _text(names[-1]), None)
                for parameter in _descendants(node, "lambda_parameter")
                if len(names := parameter.children_by_field_name("name")) > 1
            )
        return variables


def _for_variables(node: Node) -> list[LocalVariable]:
    pattern = node.child_by_field_name("item")
    if pattern is None:
        return []
    # for case let k as Foo in ... は、キャストの型が k の型になる
    cast = next((c for c in pattern.children if c.type == "as"), None)
    cast_type = pattern.child_by_field_name("name") if cast is not None else None
    annotation = _text(cast_type) if cast_type is not None else None
    return [LocalVariable(_text(name), annotation, None) for name in _pattern_identifiers(pattern)]


_RULES = _SwiftRules()


class SwiftAdapter:
    name = "swift"
    grammar = "swift"
    suffixes = frozenset({".swift"})
    constructs_by_call = True
    positional_argument_labels = True
    entry_point_names: frozenset[str] = frozenset()
    # 宣言の名前は型の名前（解決が引く）。図と題には、Swift の書き方どおり init と出す
    constructor_display_name = "init"
    comment_node_types = frozenset({"comment", "multiline_comment"})
    import_node_types = frozenset({"import_declaration"})
    test_directory_names = ("Tests", "*Tests")
    test_file_patterns = ("*Tests.swift", "*Test.swift")
    build_file_patterns = (
        "Package.swift",
        "*.pbxproj",
        "*.xcconfig",
        "Podfile",
        "Cartfile",
        "project.yml",
    )

    def simple_type_name(self, raw: str) -> str:
        return type_names.swift_simple_type_name(raw)

    def composition_type_names(self, raw: str) -> list[str]:
        return type_names.swift_composition_type_names(raw)

    def is_external_type_name(self, name: str) -> bool:
        return type_names.capitalized_is_external_type(name)

    def anonymous_class_display(self, _qualified: str, /) -> str | None:
        # 無名クラスを型の名前で持たない言語
        return None

    def imports(self, root_node: Node, _source: bytes, /) -> list[Import]:
        found: list[Import] = []
        for node in import_nodes(self, root_node):
            module = next((c for c in node.children if c.type == "identifier"), None)
            if module is not None:
                found.append(Import(_text(module), module.start_point.row + 1))
        return found

    def technology_of(self, module: str) -> str:
        # import struct Foo.Bar は Foo モジュールの Bar を取り込むので、最上位の名前で束ねる
        return leading_components(module, 1)

    def declarations(self, root_node: Node, _source: bytes, /) -> list[Declaration]:
        result: list[Declaration] = []
        self._collect(
            root_node, "", Visibility.PUBLIC, Visibility.INTERNAL, result, in_protocol=False
        )
        return result

    def _collect(
        self,
        container: Node,
        prefix: str,
        cap: Visibility,
        default: Visibility,
        result: list[Declaration],
        *,
        in_protocol: bool,
    ) -> None:
        for statement in container.children:
            declared = _declared(statement)
            # 入れ物の可視性より広くはならない
            visibility = (default if declared is None else declared).narrower(cap)
            if statement.type in _TYPE_DECLARATIONS:
                self._type(statement, prefix, declared, visibility, cap, result=result)
            else:
                self._member(statement, prefix, visibility, cap, result, in_protocol=in_protocol)

    def _member(
        self,
        statement: Node,
        prefix: str,
        visibility: Visibility,
        cap: Visibility,
        result: list[Declaration],
        *,
        in_protocol: bool,
    ) -> None:
        """型以外の宣言を一つ、種類ごとに result へ足す。"""
        if statement.type == "function_declaration":
            name = self.function_declaration_name(statement)
            self._add(
                result,
                statement,
                f"{prefix}{_selector(name, statement)}",
                "method" if prefix else "function",
                visibility,
                body=statement.child_by_field_name("body"),
            )
        elif statement.type == "protocol_function_declaration":
            name = self.function_declaration_name(statement)
            self._add(
                result,
                statement,
                f"{prefix}{_selector(name, statement)}",
                "method",
                visibility,
                body=None,
            )
        elif statement.type == "init_declaration":
            brace = None if in_protocol else statement.child_by_field_name("body")
            self._add(
                result,
                statement,
                f"{prefix}{_selector('init', statement)}",
                "method",
                visibility,
                body=brace,
            )
        elif statement.type == "deinit_declaration":
            self._add(
                result,
                statement,
                f"{prefix}deinit",
                "method",
                visibility,
                body=statement.child_by_field_name("body"),
            )
        elif statement.type == "subscript_declaration":
            brace = None if in_protocol else self._computed(statement)
            self._add(
                result,
                statement,
                f"{prefix}{_selector('subscript', statement, default_label='_')}",
                "method",
                visibility,
                body=brace,
            )
        elif statement.type in (
            "property_declaration",
            "protocol_property_declaration",
        ):
            self._properties(statement, prefix, visibility, result, in_protocol=in_protocol)
        elif statement.type == "enum_entry":
            # case は修飾子を持てず、enum と同じ可視性になる
            self._enum_entries(statement, prefix, cap, result)
        else:
            # 型の別名、ディレクティブ、コメントなど、宣言として扱わないもの
            return

    def _properties(
        self,
        statement: Node,
        prefix: str,
        visibility: Visibility,
        result: list[Declaration],
        *,
        in_protocol: bool,
    ) -> None:
        """プロパティ宣言が束縛する名前ごとに、変数の宣言を足す。"""
        brace = None if in_protocol else self._computed(statement)
        for name_node in statement.children_by_field_name("name"):
            for identifier in _bound_identifiers(name_node):
                self._add(
                    result,
                    statement,
                    f"{prefix}{_text(identifier)}",
                    "variable",
                    visibility,
                    body=brace,
                )

    def _enum_entries(
        self,
        statement: Node,
        prefix: str,
        visibility: Visibility,
        result: list[Declaration],
    ) -> None:
        """enum の case 宣言が並べる名前ごとに、変数の宣言を足す。"""
        for name_node in statement.children_by_field_name("name"):
            self._add(
                result,
                statement,
                f"{prefix}{_text(name_node)}",
                "variable",
                visibility,
                body=None,
            )

    def _type(
        self,
        node: Node,
        prefix: str,
        declared: Visibility | None,
        visibility: Visibility,
        cap: Visibility,
        *,
        result: list[Declaration],
    ) -> None:
        kind_node = node.child_by_field_name("declaration_kind")
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        kind_node = required(kind_node)
        name_node = required(name_node)
        body = required(body)
        name = "".join(_text(name_node).split())
        qualified = f"{prefix}{name}"
        in_protocol = node.type == "protocol_declaration"
        if _text(kind_node) == "extension":
            # extension 自体は宣言にせず、要素を拡張した型の名前の下に置く
            # 修飾子の無い extension は拡張先の可視性を狭めず、要素の既定だけが internal になる
            if declared is None:
                self._collect(
                    body, f"{qualified}.", cap, Visibility.INTERNAL, result, in_protocol=False
                )
            else:
                self._collect(
                    body, f"{qualified}.", visibility, visibility, result, in_protocol=False
                )
            return
        self._add(result, node, qualified, "class", visibility, body=body)
        # protocol の要素は修飾子を持てず、protocol と同じ可視性になる
        member_default = visibility if in_protocol else Visibility.INTERNAL
        self._collect(
            body, f"{qualified}.", visibility, member_default, result, in_protocol=in_protocol
        )

    @staticmethod
    def function_declaration_name(node: Node) -> str:
        keyword = next(c for c in node.children if c.type == "func")
        following = keyword.next_sibling
        following = required(following)
        return _text(following)

    def _computed(self, node: Node) -> Node | None:
        for child in node.children:
            if child.type == "computed_property":
                return child
        return node.child_by_field_name("computed_value")

    @staticmethod
    def _add(
        result: list[Declaration],
        node: Node,
        qualified_name: str,
        kind: DeclarationKind,
        visibility: Visibility,
        *,
        body: Node | None,
    ) -> None:
        spans = split_declaration(node, BodyBounds(body, body) if body is not None else None)
        result.append(
            Declaration(
                qualified_name=qualified_name,
                kind=kind,
                visibility=visibility,
                span=spans.span,
                signature=spans.signature,
                body=spans.body,
            )
        )

    def imported_names(self, _root_node: Node, _source: bytes, _path: str, /) -> dict[str, str]:
        # import は名前を束縛しないので、受け手の無い名前の解決に使う表は持たない
        return {}

    def type_members(
        self, root_node: Node, _source: bytes, path: str, /
    ) -> list[TypeDecl | MemberDecl]:
        result: list[TypeDecl | MemberDecl] = []
        self._collect_types(root_node, "", path, result)
        return result

    def discriminator_branches(self, _body_node: Node, _source: bytes, /) -> list[Step] | None:
        # 分岐の読み取りは Python だけが持つ。判別の流れ図は省いた件数に数える。
        return None

    def body_of(self, member_node: Node) -> Node | None:
        if member_node.type in ("property_declaration", "protocol_property_declaration"):
            computed = self._computed(member_node)
            if computed is None:
                return None
            getter = next((c for c in computed.children if c.type == "computed_getter"), None)
            if getter is not None:
                return getter
            # get を書かない計算プロパティは、波括弧の中の文が getter の本体になる
            return computed if any(c.type == "statements" for c in computed.children) else None
        return member_node.child_by_field_name("body")

    def call_sites(self, body_node: Node, _source: bytes, /) -> list[CallSite | DeferredSite]:
        sites = calls_core.call_sites(body_node, load_queries(self.grammar), _RULES)
        flattened: list[CallSite | DeferredSite] = []
        for site in sites:
            if (
                isinstance(site, DeferredSite)
                and site.kind == "lambda"
                and _is_call_argument(site.node)
            ):
                # Button の action や .onAppear のクロージャは、書いた宣言の呼び出しとして図に出す
                flattened.extend(site.calls)
            else:
                flattened.append(site)
        return sorted(flattened, key=lambda s: s.end)

    def isolation_of(
        self, declaration_node: Node, _source: bytes, context: IsolationContext, /
    ) -> Isolation:
        return swift_concurrency.isolation_of(declaration_node, context)

    def is_async(self, member_node: Node) -> bool:
        return swift_concurrency.is_async(member_node)

    def concurrency_steps(
        self, body: Node, source: bytes, enclosing: Isolation
    ) -> list[ConcurrencyStep]:
        return swift_concurrency.concurrency_steps(self, body, source, enclosing)

    def is_ui_type(
        self, type_name: str, context: IsolationContext, scope: Node | None = None
    ) -> bool:
        return swift_concurrency.is_ui_type(type_name, context, scope)

    def is_main_thread(self, domain: Isolation) -> bool:
        return swift_concurrency.is_main_thread(domain)

    def sharing_of(self, type_name: str, context: IsolationContext) -> Sharing:
        return swift_concurrency.sharing_of(type_name, context)

    def receiver_of(self, expression: Node) -> ReceiverKind:
        return calls_core.receiver_of(expression, _RULES)

    def regex_literals(self, root_node: Node, _source: bytes, path: str, /) -> RegexScan:
        return swift_regex.regex_scan(root_node, path)

    def locals_of(self, body_node: Node) -> Locals:
        return calls_core.locals_of(body_node, _RULES)

    def _collect_types(
        self, container: Node, prefix: str, path: str, result: list[TypeDecl | MemberDecl]
    ) -> None:
        for statement in container.children:
            if statement.type in _TYPE_DECLARATIONS:
                self._collect_type(statement, prefix, path, result)
            else:
                # 最上位の関数、import、型の別名などは型に属さず、メンバにならない
                continue

    def _collect_type(
        self, node: Node, prefix: str, path: str, result: list[TypeDecl | MemberDecl]
    ) -> None:
        name = "".join(_text(required(node.child_by_field_name("name"))).split())
        qualified = f"{prefix}{name}"
        simple = name.rsplit(".", 1)[-1]
        is_extension = False
        if node.type == "protocol_declaration":
            kind: TypeKind = "interface"
        else:
            keyword = _text(required(node.child_by_field_name("declaration_kind")))
            kind = _TYPE_KINDS[keyword]
            is_extension = keyword == "extension"
        line = node.start_point.row + 1
        # extension は型を宣言せず、private や fileprivate は拡張の要素の可視性にだけ効く
        closed = not is_extension and _declared(node) is Visibility.FILE
        scope = TypeScope.FILE if closed else TypeScope.MODULE
        result.append(TypeDecl(qualified, simple, kind, _bases_of(node), path, line, node, scope))
        body = required(node.child_by_field_name("body"))
        for member in body.children:
            self._collect_member(member, simple, qualified, path, result)

    def _collect_member(
        self,
        node: Node,
        owner_name: str,
        owner: str,
        path: str,
        result: list[TypeDecl | MemberDecl],
    ) -> None:
        line = node.start_point.row + 1
        if node.type in _TYPE_DECLARATIONS:
            self._collect_type(node, f"{owner}.", path, result)
        elif node.type in ("function_declaration", "protocol_function_declaration"):
            name = self.function_declaration_name(node)
            result.append(
                MemberDecl(
                    owner,
                    name,
                    "method",
                    _return_type_of(node),
                    _parameter_declarations(node),
                    line,
                    node,
                    self.body_of(node),
                    _selector(name, node),
                )
            )
        elif node.type == "init_declaration":
            # 生成の解決は型の単純名で引くので、C# と同じくコンストラクタの名前は型の名前にする
            result.append(
                MemberDecl(
                    owner,
                    owner_name,
                    "constructor",
                    None,
                    _parameter_declarations(node),
                    line,
                    node,
                    self.body_of(node),
                    _selector("init", node),
                )
            )
        elif node.type in ("property_declaration", "protocol_property_declaration"):
            annotation = _type_annotation_of(node)
            is_property = (
                node.type == "protocol_property_declaration" or self._computed(node) is not None
            )
            member_kind: MemberKind = "property" if is_property else "field"
            body = self.body_of(node)
            result.extend(
                MemberDecl(owner, _text(identifier), member_kind, annotation, (), line, node, body)
                for name_node in node.children_by_field_name("name")
                for identifier in _bound_identifiers(name_node)
            )
        else:
            # enum の case、サブスクリプト、deinit、型の別名は、受け手の解決の対象にしない
            return
