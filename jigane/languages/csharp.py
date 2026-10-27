from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages import calls_core, csharp_concurrency, csharp_regex, csharp_xaml, type_names
from jigane.languages.base import (
    Argument,
    BodyBounds,
    CallParts,
    CallSite,
    ConcurrencyStep,
    Declaration,
    DeclarationKind,
    DeclarationSpans,
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
    span_of,
    split_declaration,
)
from jigane.languages.calls_core import Captures
from jigane.languages.queries import load_queries
from jigane.preconditions.required import required

TYPE_KINDS: dict[str, TypeKind] = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "record_declaration": "record",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "delegate_declaration": "delegate",
}
_METHODS = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "destructor_declaration",
        "operator_declaration",
        "conversion_operator_declaration",
        "indexer_declaration",
    }
)
_PROPERTIES = frozenset({"property_declaration", "event_declaration"})
_FIELDS = frozenset({"field_declaration", "event_field_declaration"})
NAMESPACES = frozenset({"namespace_declaration", "file_scoped_namespace_declaration"})
# #if の腕は、その中の宣言を子として抱える
PREPROCESSOR = frozenset({"preproc_if", "preproc_elif", "preproc_else"})
# 入れ物の要素が修飾子を省いたときに public になる入れ物
_MEMBERS_PUBLIC_BY_DEFAULT = frozenset({"interface_declaration", "enum_declaration"})


_LAMBDAS = frozenset({"lambda_expression", "anonymous_method_expression"})
# 呼び出しの式の形をしているが、メソッドの呼び出しではない
_NOT_CALLS = frozenset({"nameof"})


def _text(node: Node) -> str:
    return required(node.text).decode()


def _modifiers(node: Node) -> set[str]:
    return {_text(child) for child in node.children if child.type == "modifier"}


def _declared(modifiers: set[str], default: Visibility) -> Visibility:
    # private protected は派生型からしか見えず、file はそのファイルの中でしか見えない
    if "private" in modifiers or "file" in modifiers:
        return Visibility.FILE
    # protected internal は protected を含むので、internal より先に判定する
    if modifiers & {"public", "protected"}:
        return Visibility.PUBLIC
    if "internal" in modifiers:
        return Visibility.INTERNAL
    return default


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


def _name(node: Node) -> str:
    if node.type == "indexer_declaration":
        return "this[]"
    if node.type == "destructor_declaration":
        return f"~{_text(_identifier(node))}"
    if node.type == "operator_declaration":
        operator = node.child_by_field_name("operator")
        operator = required(operator)
        return f"operator {_text(operator)}"
    if node.type == "conversion_operator_declaration":
        target = node.child_by_field_name("type")
        target = required(target)
        return f"operator {_text(target)}"
    return _text(_identifier(node))


def _identifier(node: Node) -> Node:
    named = node.child_by_field_name("name")
    if named is not None:
        return named
    # コンストラクタと列挙子は name フィールドを持たない
    return next(child for child in node.children if child.type == "identifier")


def _opener_and_closer(node: Node) -> tuple[Node, Node] | None:
    # シグネチャの最終行を決める記号と、本体の最終行を決める節点を返す
    for child in node.children:
        if child.type in ("block", "declaration_list", "enum_member_declaration_list"):
            opener = child.children[0]
            return opener, child
        if child.type == "arrow_expression_clause":
            arrow = next(c for c in child.children if c.type == "=>")
            return arrow, node
        if child.type == "accessor_list":
            accessors = [c for c in child.children if c.type == "accessor_declaration"]
            has_code = any(
                c.type in ("block", "arrow_expression_clause")
                for accessor in accessors
                for c in accessor.children
            )
            if has_code:
                return child.children[0], child
            # 自動実装のアクセサに本体は無い
            return None
    return None


def _split(node: Node) -> DeclarationSpans:
    found = _opener_and_closer(node)
    return split_declaration(node, BodyBounds(*found) if found is not None else None)


def _bases(node: Node) -> tuple[str, ...]:
    base_list = next((c for c in node.children if c.type == "base_list"), None)
    if base_list is None:
        return ()
    return tuple(_text(c.child_by_field_name("type") or c) for c in base_list.named_children)


def _parameters(plist: Node | None) -> tuple[ParameterDecl, ...]:
    if plist is None:
        return ()
    result: list[ParameterDecl] = []
    for p in plist.named_children:
        type_node = p.child_by_field_name("type")
        name_node = p.child_by_field_name("name")
        if p.type != "parameter" or type_node is None or name_node is None:
            continue
        # 既定値は名前の後ろに、型でも名前でもない式として付く
        optional = any(c.type == "=" for c in p.children)
        result.append(ParameterDecl(_text(name_node), _text(type_node), optional))
    return tuple(result)


def _annotation(type_node: Node | None) -> str | None:
    # var は型を初期化式から決めるので、注記を持たない
    if type_node is None or type_node.type == "implicit_type":
        return None
    return _text(type_node)


def _lambda_parameter_names(lambda_node: Node) -> list[str]:
    parameters = lambda_node.child_by_field_name("parameters")
    if parameters is None:
        return []
    if parameters.type in ("identifier", "implicit_parameter"):
        return [_text(parameters)]
    names: list[str] = []
    for p in parameters.named_children:
        name = p.child_by_field_name("name") or (p if p.type == "identifier" else None)
        if name is not None:
            names.append(_text(name))
    return names


def _arguments(node: Node, captured: Node | None = None) -> tuple[Argument, ...]:
    arguments = captured or node.child_by_field_name("arguments")
    if arguments is None:
        arguments = next((c for c in node.children if c.type == "argument_list"), None)
    if arguments is None:
        return ()
    result: list[Argument] = []
    for argument in arguments.named_children:
        if not argument.named_children:
            continue
        label = argument.child_by_field_name("name") or next(
            (c for c in argument.children if c.type == "name_colon"), None
        )
        name = _text(label).rstrip(":").strip() if label is not None else None
        result.append(Argument(argument.named_children[-1], name))
    return tuple(result)


def _call_parts(call: Node) -> CallParts:
    function = call.child_by_field_name("function")
    arguments = _arguments(call)
    text = _text(call)
    if function is None:
        return CallParts(None, None, arguments, text)
    if function.type == "member_access_expression":
        name = function.child_by_field_name("name")
        return CallParts(
            function.child_by_field_name("expression"),
            _text(name) if name is not None else None,
            arguments,
            text,
        )
    if function.type == "conditional_access_expression":
        binding = next(
            (c for c in function.named_children if c.type == "member_binding_expression"), None
        )
        name = binding.child_by_field_name("name") if binding is not None else None
        return CallParts(
            function.child_by_field_name("condition"),
            _text(name) if name is not None else None,
            arguments,
            text,
        )
    if function.type == "generic_name":
        return CallParts(None, _text(function.children[0]), arguments, text)
    return CallParts(None, _text(function), arguments, text)


def _call_site(captures: dict[str, list[Node]]) -> CallSite | None:
    node = captures["call"][0]
    callee = captures["callee"][0] if "callee" in captures else None
    receiver = captures["receiver"][0] if "receiver" in captures else None
    arguments = _arguments(node, captures["arguments"][0] if "arguments" in captures else None)
    creation = node.type != "invocation_expression"
    if creation:
        name = _text(callee) if callee is not None else "new"
    else:
        name = _text(required(callee))
        if name in _NOT_CALLS and receiver is None:
            return None
    located = callee or node
    return CallSite(
        node, name, receiver, arguments, creation, node.end_byte, located.start_point.row + 1
    )


class _CSharpRules(calls_core.RuleDefaults):
    """calls_core の三つの契約を満たす、C# の節点の名前の表と判定。"""

    deferred_bodies = _LAMBDAS
    self_types = frozenset({"this_expression", "this"})
    super_types = frozenset({"base_expression", "base"})
    name_types = frozenset({"identifier"})
    generic_name_types = frozenset({"generic_name"})
    call_types = frozenset({"invocation_expression"})
    creation_types = frozenset(
        {"object_creation_expression", "implicit_object_creation_expression"}
    )
    member_type = "member_access_expression"
    member_base_field = "expression"
    lambda_types = _LAMBDAS

    def call_site(self, captures: Captures) -> CallSite | None:
        return _call_site(captures)

    def call_parts(self, call: Node) -> CallParts:
        return _call_parts(call)

    def arguments(self, node: Node) -> tuple[Argument, ...]:
        return _arguments(node)

    def function_name(self, node: Node) -> str | None:
        if node.type != "local_function_statement":
            return None
        return _text(required(node.child_by_field_name("name")))

    def lambda_parameter_names(self, node: Node) -> list[str]:
        return _lambda_parameter_names(node)

    def variables_of(self, node: Node) -> list[LocalVariable]:
        variables: list[LocalVariable] = []
        if node.type == "variable_declaration":
            type_node = node.child_by_field_name("type")
            for declarator in node.children:
                if declarator.type != "variable_declarator":
                    continue
                name = _text(declarator.child_by_field_name("name") or declarator.children[0])
                init = declarator.named_children[-1] if declarator.named_child_count > 1 else None
                variables.append(LocalVariable(name, _annotation(type_node), init))
        elif node.type in ("foreach_statement", "declaration_expression", "declaration_pattern"):
            name_node = node.child_by_field_name("name") or node.child_by_field_name("left")
            if name_node is not None:
                annotation = _annotation(node.child_by_field_name("type"))
                variables.append(LocalVariable(_text(name_node), annotation, None))
        return variables


_RULES = _CSharpRules()


class CSharpAdapter:
    name = "csharp"
    grammar = "csharp"
    suffixes = frozenset({".cs"})
    constructs_by_call = False
    positional_argument_labels = False
    entry_point_names = frozenset(("Main",))
    constructor_display_name = None
    comment_node_types = frozenset({"comment"})
    # using 文（using_statement）は含めない。破棄の構文であって取り込みではない
    import_node_types = frozenset({"using_directive"})
    test_directory_names = ("*.Tests", "*.UnitTests", "*.IntegrationTests", "*Tests")
    test_file_patterns = ("*Tests.cs", "*Test.cs")
    build_file_patterns = (
        "*.csproj",
        "*.sln",
        "*.slnx",
        "Directory.Build.props",
        "Directory.Build.targets",
        "Directory.Packages.props",
        "NuGet.config",
        "nuget.config",
        "global.json",
    )

    def simple_type_name(self, raw: str) -> str:
        return type_names.generic_simple_type_name(raw)

    def composition_type_names(self, _raw: str, /) -> list[str]:
        return []

    def is_external_type_name(self, name: str) -> bool:
        return type_names.capitalized_is_external_type(name)

    def anonymous_class_display(self, _qualified: str, /) -> str | None:
        # 無名クラスを型の名前で持たない言語
        return None

    def markup_path(self, path: str) -> str | None:
        return csharp_xaml.paired_xaml_path(path)

    def markup_handlers(self, markup: str) -> frozenset[str]:
        return csharp_xaml.handler_names(markup)

    def imported_names(self, _root_node: Node, _source: bytes, _path: str, /) -> dict[str, str]:
        # using は名前を束縛しないので、受け手の無い名前の解決に使う表は持たない
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

    def isolation_of(
        self, declaration_node: Node, _source: bytes, context: IsolationContext, /
    ) -> Isolation:
        return csharp_concurrency.isolation_of(declaration_node, context)

    def is_async(self, member_node: Node) -> bool:
        return csharp_concurrency.is_async(member_node)

    def concurrency_steps(
        self, body: Node, source: bytes, enclosing: Isolation
    ) -> list[ConcurrencyStep]:
        return csharp_concurrency.concurrency_steps(self, body, source, enclosing)

    def is_ui_type(
        self, type_name: str, context: IsolationContext, scope: Node | None = None
    ) -> bool:
        return csharp_concurrency.is_ui_type(type_name, context, scope)

    def is_main_thread(self, domain: Isolation) -> bool:
        return csharp_concurrency.is_main_thread(domain)

    def sharing_of(self, _type_name: str, _context: IsolationContext, /) -> Sharing:
        # C# の共有の札は別の段で実装するので、札は出さない
        return Sharing.UNKNOWN

    def body_of(self, member_node: Node) -> Node | None:
        return member_node.child_by_field_name("body") or next(
            (c for c in member_node.children if c.type == "arrow_expression_clause"), None
        )

    def call_sites(self, body_node: Node, _source: bytes, /) -> list[CallSite | DeferredSite]:
        return calls_core.call_sites(body_node, load_queries(self.grammar), _RULES)

    def receiver_of(self, expression: Node) -> ReceiverKind:
        return calls_core.receiver_of(expression, _RULES)

    def regex_literals(self, root_node: Node, _source: bytes, path: str, /) -> RegexScan:
        return csharp_regex.regex_scan(root_node, path)

    def locals_of(self, body_node: Node) -> Locals:
        return calls_core.locals_of(body_node, _RULES)

    def _collect_types(
        self, container: Node, prefix: str, path: str, result: list[TypeDecl | MemberDecl]
    ) -> None:
        for node in flatten(container):
            if node.type in NAMESPACES:
                self._collect_types(namespace_container(node), prefix, path, result)
            elif node.type in TYPE_KINDS:
                self._collect_type(node, prefix, path, result)
            else:
                # 名前空間でも型でもない最上位の要素（using、グローバル文）は宣言を持たない
                continue

    def _collect_type(
        self, node: Node, prefix: str, path: str, result: list[TypeDecl | MemberDecl]
    ) -> None:
        name = _text(required(node.child_by_field_name("name")))
        qualified = f"{prefix}{name}"
        line = node.start_point.row + 1
        scope = TypeScope.FILE if "file" in _modifiers(node) else TypeScope.MODULE
        result.append(
            TypeDecl(qualified, name, TYPE_KINDS[node.type], _bases(node), path, line, node, scope)
        )
        # プライマリコンストラクタの引数は、型のフィールドのように参照できる
        primary = next((c for c in node.children if c.type == "parameter_list"), None)
        result.extend(
            MemberDecl(
                qualified, parameter.name, "parameter", parameter.type_annotation, (), line, node
            )
            for parameter in _parameters(primary)
        )
        body = next((c for c in node.children if c.type == "declaration_list"), None)
        if body is None:
            return
        for member in flatten(body):
            self._collect_member(member, name, qualified, path, result)

    def _collect_member(
        self,
        node: Node,
        owner_name: str,
        owner: str,
        path: str,
        result: list[TypeDecl | MemberDecl],
    ) -> None:
        line = node.start_point.row + 1

        def make(
            name: str,
            kind: MemberKind,
            type_node: Node | None,
            parameters: tuple[ParameterDecl, ...],
            body: Node | None,
        ) -> MemberDecl:
            annotation = _text(type_node) if type_node is not None else None
            return MemberDecl(owner, name, kind, annotation, parameters, line, node, body)

        if node.type in TYPE_KINDS:
            self._collect_type(node, f"{owner}.", path, result)
        elif node.type == "method_declaration":
            result.append(
                make(
                    _text(required(node.child_by_field_name("name"))),
                    "method",
                    node.child_by_field_name("returns"),
                    _parameters(node.child_by_field_name("parameters")),
                    node.child_by_field_name("body"),
                )
            )
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
        elif node.type == "property_declaration":
            result.append(
                make(
                    _text(required(node.child_by_field_name("name"))),
                    "property",
                    node.child_by_field_name("type"),
                    (),
                    None,
                )
            )
        elif node.type == "field_declaration":
            declaration = next(c for c in node.children if c.type == "variable_declaration")
            type_node = declaration.child_by_field_name("type")
            for declarator in declaration.children:
                if declarator.type == "variable_declarator":
                    name_node = declarator.child_by_field_name("name") or declarator.children[0]
                    result.append(make(_text(name_node), "field", type_node, (), None))
        else:
            # 演算子、インデクサ、イベント、列挙子など。受け手の解決の対象にしない
            return

    def imports(self, root_node: Node, _source: bytes, /) -> list[Import]:
        found: list[Import] = []
        for node in import_nodes(self, root_node):
            # 別名の宣言 `using A = X.Y;` では、別名は name フィールドで、取り込む先は最後の名前
            targets = [
                c
                for i, c in enumerate(node.children)
                if c.type in ("qualified_name", "identifier")
                and node.field_name_for_child(i) != "name"
            ]
            if targets:
                found.append(Import(_text(targets[-1]), targets[-1].start_point.row + 1))
        return found

    def technology_of(self, module: str) -> str:
        # 名前空間は会社名と製品名の二つで一つの単位になることが多い
        return leading_components(module, 2)

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
        for node in flatten(container):
            if node.type in NAMESPACES:
                # 名前空間は修飾名に含めず、型の入れ物として潜るだけにする
                self._collect(
                    namespace_container(node),
                    prefix,
                    cap,
                    Visibility.INTERNAL,
                    result,
                )
            elif node.type in TYPE_KINDS:
                name = _name(node)
                visibility = _declared(_modifiers(node), default).narrower(cap)
                spans = _split(node)
                qualified = f"{prefix}{name}"
                result.append(
                    Declaration(
                        qualified, "class", visibility, spans.span, spans.signature, spans.body
                    )
                )
                members = next(
                    (
                        c
                        for c in node.children
                        if c.type in ("declaration_list", "enum_member_declaration_list")
                    ),
                    None,
                )
                if members is not None:
                    self._collect(
                        members,
                        f"{qualified}.",
                        visibility,
                        (
                            Visibility.PUBLIC
                            if node.type in _MEMBERS_PUBLIC_BY_DEFAULT
                            else Visibility.FILE
                        ),
                        result,
                    )
            elif node.type in _METHODS or node.type in _PROPERTIES:
                kind: DeclarationKind = "method" if node.type in _METHODS else "variable"
                spans = _split(node)
                result.append(
                    Declaration(
                        f"{prefix}{_name(node)}",
                        kind,
                        _declared(_modifiers(node), default).narrower(cap),
                        spans.span,
                        spans.signature,
                        spans.body,
                    )
                )
            elif node.type in _FIELDS:
                visibility = _declared(_modifiers(node), default).narrower(cap)
                declaration = next(c for c in node.children if c.type == "variable_declaration")
                for declarator in declaration.children:
                    if declarator.type != "variable_declarator":
                        continue
                    result.append(
                        Declaration(
                            f"{prefix}{_text(_identifier(declarator))}",
                            "variable",
                            visibility,
                            span_of(node),
                            span_of(node),
                            None,
                        )
                    )
            elif node.type == "enum_member_declaration":
                result.append(
                    Declaration(
                        f"{prefix}{_text(_identifier(node))}",
                        "variable",
                        cap,
                        span_of(node),
                        span_of(node),
                        None,
                    )
                )
            else:
                # 注釈、using、グローバル文、記号など。宣言ではない
                continue
