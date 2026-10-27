from collections.abc import Mapping
from pathlib import PurePosixPath

from tree_sitter import Node

from jigane.languages import calls_core, python_concurrency, type_names
from jigane.languages.base import (
    Argument,
    BodyBounds,
    Branch,
    BranchArm,
    CallParts,
    CallSite,
    CommandArgument,
    ConcurrencyStep,
    Declaration,
    DeclarationKind,
    DeferredSite,
    FilePathPattern,
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
    StoreAccess,
    TypeDecl,
    Visibility,
    import_nodes,
    leading_components,
    split_declaration,
)
from jigane.languages.calls_core import Captures
from jigane.languages.python_arguments import command_arguments as _command_arguments
from jigane.languages.python_file_paths import file_path_patterns as _file_path_patterns
from jigane.languages.python_regex import regex_scan as _regex_scan
from jigane.languages.python_stores import store_accesses as _store_accesses
from jigane.languages.queries import load_queries
from jigane.preconditions.required import required


def _is_private(name: str) -> bool:
    # __init__ のような特殊メソッドはクラスの使い方を決めるので公開とする
    is_special = name.startswith("__") and name.endswith("__")
    return name.startswith("_") and not is_special


def _text(node: Node) -> str:
    return required(node.text).decode()


def _exported_names(module: Node) -> set[str] | None:
    for statement in module.children:
        # この文法では代入が式文で包まれず、モジュールやブロックの直下に現れる
        assignment = statement
        if assignment.type != "assignment":
            continue
        left = assignment.child_by_field_name("left")
        right = assignment.child_by_field_name("right")
        if left is None or right is None or _text(left) != "__all__":
            continue
        if right.type not in ("list", "tuple"):
            continue
        items = right.named_children
        if all(item.type == "string" for item in items):
            return {_text(item).strip("'\"") for item in items}
    return None


def module_name(path: str) -> str:
    """ファイルのパスから、パッケージを含むモジュールの修飾名を作る。先頭の src は落とす。"""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    if parts[-1] == "__init__" and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(path: str) -> str:
    """ファイルが属するパッケージの修飾名。__init__.py はそのパッケージ自身。"""
    name = module_name(path)
    if PurePosixPath(path).stem == "__init__":
        return name
    return name.rpartition(".")[0]


def _from_base(module: Node, package: str) -> str | None:
    """from の後ろのモジュールを絶対の修飾名にする。パッケージの外へ出る相対 import は None。"""
    if module.type != "relative_import":
        return _text(module)
    prefix = next((c for c in module.children if c.type == "import_prefix"), None)
    dotted = next((c for c in module.children if c.type == "dotted_name"), None)
    levels = len(_text(prefix)) if prefix is not None else 1
    parts = package.split(".") if package else []
    if levels - 1 > len(parts):
        return None
    base = parts[: len(parts) - (levels - 1)]
    if dotted is not None:
        base.append(_text(dotted))
    return ".".join(base)


def _unwrap(node: Node) -> tuple[Node, list[str]]:
    """decorated_definition を、定義の節点とデコレータの原文に分ける。"""
    if node.type != "decorated_definition":
        return node, []
    decorators = [_text(c).lstrip("@").strip() for c in node.children if c.type == "decorator"]
    return required(node.child_by_field_name("definition")), decorators


def _parameters(function: Node, *, drop_first: bool) -> tuple[ParameterDecl, ...]:
    parameters = function.child_by_field_name("parameters")
    if parameters is None:
        return ()
    result: list[ParameterDecl] = []
    for child in parameters.named_children:
        if child.type == "identifier":
            result.append(ParameterDecl(_text(child), "", optional=False))
        elif child.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            name_node = child.child_by_field_name("name") or child.named_children[0]
            if name_node.type != "identifier":
                # 型注記付きの *args と **kwargs は、呼び出しの引数と一対一に対応しない
                continue
            annotation = child.child_by_field_name("type")
            has_default = child.child_by_field_name("value") is not None
            result.append(
                ParameterDecl(
                    _text(name_node).lstrip("*"),
                    _text(annotation) if annotation is not None else "",
                    has_default,
                )
            )
        else:
            # *args、**kwargs、区切りの / と * は呼び出しの引数と一対一に対応しない
            continue
    if drop_first and result and result[0].name in ("self", "cls"):
        result = result[1:]
    return tuple(result)


# 呼び出しの式の形をしているが、図の矢印にしない
_NOT_CALLS = frozenset({"super"})
# 本体の実行では動かない入れ物
_DEFERRED_SCOPES = frozenset({"function_definition", "class_definition", "lambda"})
# メソッドの受け手の型を指す仮引数の名前
_SELF_NAMES = frozenset({"self", "cls"})


def _arguments(call: Node) -> tuple[Argument, ...]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None or arguments.type != "argument_list":
        return ()
    result: list[Argument] = []
    for argument in arguments.named_children:
        if argument.type == "comment":
            continue
        if argument.type == "keyword_argument":
            name = required(argument.child_by_field_name("name"))
            value = required(argument.child_by_field_name("value"))
            result.append(Argument(value, _text(name)))
        else:
            result.append(Argument(argument, None))
    return tuple(result)


def _call_parts(call: Node) -> CallParts:
    function = call.child_by_field_name("function")
    arguments = _arguments(call)
    text = _text(call)
    if function is None:
        return CallParts(None, None, arguments, text)
    if function.type == "attribute":
        attribute = function.child_by_field_name("attribute")
        return CallParts(
            function.child_by_field_name("object"),
            _text(attribute) if attribute is not None else None,
            arguments,
            text,
        )
    if function.type == "identifier":
        return CallParts(None, _text(function), arguments, text)
    return CallParts(None, None, arguments, text)


def _call_site(captures: Captures) -> CallSite | None:
    node = captures["call"][0]
    callee = captures["callee"][0]
    receiver = captures["receiver"][0] if "receiver" in captures else None
    name = _text(callee)
    if name in _NOT_CALLS and receiver is None:
        return None
    # 生成かどうかは構文では決まらない。Foo() も is_creation は False のまま返す
    return CallSite(
        node,
        name,
        receiver,
        _arguments(node),
        is_creation=False,
        end=node.end_byte,
        line=callee.start_point.row + 1,
    )


def _pattern_names(node: Node) -> list[str]:
    """代入先やループ変数の並びに含まれる識別子の名前を、文書順に返す。"""
    if node.type == "identifier":
        return [_text(node)]
    if node.type in ("attribute", "subscript"):
        return []
    return [name for child in node.named_children for name in _pattern_names(child)]


def _lambda_parameter_names(node: Node) -> list[str]:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return []
    names: list[str] = []
    for parameter in parameters.named_children:
        if parameter.type == "default_parameter":
            name = parameter.child_by_field_name("name")
            names.extend(_pattern_names(name) if name is not None else [])
        else:
            names.extend(_pattern_names(parameter))
    return names


def _assignment_of(statement: Node) -> Node | None:
    # この文法では代入が式文で包まれることも、そのまま現れることもある
    if statement.type == "assignment":
        return statement
    if statement.type == "expression_statement" and statement.named_children:
        first = statement.named_children[0]
        return first if first.type == "assignment" else None
    return None


def _steps(block: Node) -> list[Step]:
    steps: list[Step] = []
    for statement in block.named_children:
        if statement.type == "return_statement":
            expression = statement.named_children[0] if statement.named_children else None
            member = expression is not None and expression.type == "attribute"
            steps.append(Return(_text(expression) if expression is not None and member else None))
        elif statement.type == "raise_statement":
            steps.append(Return(None))
        elif statement.type == "if_statement":
            steps.append(_if_chain([statement, *_later_clauses(statement)]))
        elif statement.type == "match_statement":
            steps.append(_match_branch(statement))
    return steps


def _later_clauses(statement: Node) -> list[Node]:
    return [c for c in statement.named_children if c.type in ("elif_clause", "else_clause")]


def _if_chain(clauses: list[Node]) -> Branch:
    """if か elif の節から後ろを一つの分岐にする。後ろの elif と else は偽の腕へ入れ子にする。"""
    head, rest = clauses[0], clauses[1:]
    condition = head.child_by_field_name("condition")
    consequence = head.child_by_field_name("consequence")
    arms = [BranchArm("yes", tuple(_steps(consequence)) if consequence else ())]
    text = _text(condition) if condition else "?"
    if not rest:
        return Branch(text, tuple(arms), "no")
    if rest[0].type == "elif_clause":
        arms.append(BranchArm("no", (_if_chain(rest),)))
    else:
        body = rest[0].child_by_field_name("body")
        arms.append(BranchArm("no", tuple(_steps(body)) if body else ()))
    return Branch(text, tuple(arms), None)


def _match_branch(statement: Node) -> Branch:
    subject = statement.child_by_field_name("subject")
    body = statement.child_by_field_name("body")
    arms: list[BranchArm] = []
    for clause in body.named_children if body else []:
        if clause.type != "case_clause":
            continue
        pattern = next((c for c in clause.named_children if c.type == "case_pattern"), None)
        block = next((c for c in clause.named_children if c.type == "block"), None)
        label = "case " + (_text(pattern) if pattern else "_")
        arms.append(BranchArm(label, tuple(_steps(block)) if block else ()))
    return Branch(f"match {_text(subject)}" if subject else "match", tuple(arms), None)


class _PythonRules(calls_core.RuleDefaults):
    """calls_core の三つの契約を満たす、Python の節点の名前の表と判定。"""

    deferred_bodies = _DEFERRED_SCOPES
    self_types: frozenset[str] = frozenset()
    super_types: frozenset[str] = frozenset()
    name_types = frozenset({"identifier"})
    self_names = _SELF_NAMES
    call_types = frozenset({"call"})
    super_call_names = _NOT_CALLS
    creation_types: frozenset[str] = frozenset()
    member_type = "attribute"
    member_base_field = "object"
    member_name_field = "attribute"
    lambda_types = frozenset({"lambda"})
    scope_types = frozenset({"function_definition", "class_definition"})

    def call_site(self, captures: Captures) -> CallSite | None:
        return _call_site(captures)

    def call_parts(self, call: Node) -> CallParts:
        return _call_parts(call)

    def arguments(self, node: Node) -> tuple[Argument, ...]:
        return _arguments(node)

    def function_name(self, node: Node) -> str | None:
        if node.type not in self.scope_types:
            return None
        name = node.child_by_field_name("name")
        return _text(name) if name is not None else None

    def lambda_parameter_names(self, node: Node) -> list[str]:
        return _lambda_parameter_names(node)

    def variables_of(self, node: Node) -> list[LocalVariable]:
        if node.type == "assignment":
            return _assignment_variables(node)
        if node.type == "for_statement":
            names = _pattern_names(required(node.child_by_field_name("left")))
            return [LocalVariable(name, None, None) for name in names]
        if node.type == "as_pattern_target":
            return [LocalVariable(name, None, None) for name in _pattern_names(node)]
        return []


def _assignment_variables(assignment: Node) -> list[LocalVariable]:
    left = required(assignment.child_by_field_name("left"))
    annotation = assignment.child_by_field_name("type")
    right = assignment.child_by_field_name("right")
    if left.type == "identifier":
        text = _text(annotation) if annotation is not None else None
        return [LocalVariable(_text(left), text, right)]
    # 分解の代入は、各名前の型が分からない
    return [LocalVariable(name, None, None) for name in _pattern_names(left)]


_RULES = _PythonRules()


class PythonAdapter:
    name = "python"
    grammar = "python"
    suffixes = frozenset({".py"})
    # Foo() が生成である。構文では関数の呼び出しと区別できないので、解決の側が型の名前で決める
    constructs_by_call = True
    positional_argument_labels = False
    entry_point_names = frozenset(("main",))
    constructor_display_name = None
    # docstring は文字列の式なので含めない
    comment_node_types = frozenset({"comment"})
    import_node_types = frozenset(
        {"import_statement", "import_from_statement", "future_import_statement"}
    )
    test_directory_names = ("tests", "test")
    test_file_patterns = ("test_*.py", "*_test.py", "conftest.py")
    # ロックファイルは生成物として扱う。人が判断すべき依存の変更はマニフェストに現れる
    build_file_patterns = (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements*.txt",
        "Pipfile",
    )

    def simple_type_name(self, raw: str) -> str:
        return type_names.python_simple_type_name(raw)

    def composition_type_names(self, _raw: str, /) -> list[str]:
        return []

    def is_external_type_name(self, name: str) -> bool:
        return type_names.capitalized_is_external_type(name)

    def anonymous_class_display(self, _qualified: str, /) -> str | None:
        # 無名クラスを型の名前で持たない言語
        return None

    def imports(self, root_node: Node, _source: bytes, /) -> list[Import]:
        found: list[Import] = []
        for node in import_nodes(self, root_node):
            if node.type == "import_statement":
                for name in node.children_by_field_name("name"):
                    # `import a.b as c` は取り込む先 a.b が name フィールドの中にある
                    target = (
                        name.child_by_field_name("name") if name.type == "aliased_import" else name
                    )
                    if target is not None:
                        found.append(Import(_text(target), target.start_point.row + 1))
            else:
                module = node.child_by_field_name("module_name")
                if module is not None:
                    found.append(Import(_text(module), module.start_point.row + 1))
                elif node.type == "future_import_statement":
                    found.append(Import("__future__", node.start_point.row + 1))
        return found

    def technology_of(self, module: str) -> str:
        return leading_components(module, 1)

    def imported_names(self, root_node: Node, _source: bytes, path: str, /) -> dict[str, str]:
        package = _package_of(path)
        table: dict[str, str] = {}
        for node in import_nodes(self, root_node):
            if node.type == "import_statement":
                for name in node.children_by_field_name("name"):
                    if name.type == "aliased_import":
                        target = name.child_by_field_name("name")
                        alias = name.child_by_field_name("alias")
                        if target is not None and alias is not None:
                            table[_text(alias)] = _text(target)
                    else:
                        # `import a.b` が束縛するのは先頭の a だけ
                        table[_text(name).split(".", 1)[0]] = _text(name).split(".", 1)[0]
            elif node.type == "import_from_statement":
                module = node.child_by_field_name("module_name")
                base = _from_base(module, package) if module is not None else None
                if base is None:
                    continue
                for name in node.children_by_field_name("name"):
                    target = (
                        name.child_by_field_name("name") if name.type == "aliased_import" else name
                    )
                    alias = (
                        name.child_by_field_name("alias")
                        if name.type == "aliased_import"
                        else None
                    )
                    if target is None:
                        continue
                    imported = _text(target)
                    bound = _text(alias) if alias is not None else imported
                    table[bound] = f"{base}.{imported}" if base else imported
        return table

    def command_arguments(
        self, root_node: Node, _source: bytes, path: str, /
    ) -> list[CommandArgument]:
        return _command_arguments(root_node, path)

    def file_path_patterns(
        self, root_node: Node, _source: bytes, path: str, /
    ) -> list[FilePathPattern]:
        return _file_path_patterns(root_node, path)

    def store_accesses(self, sources: Mapping[str, str]) -> list[StoreAccess]:
        return _store_accesses(sources)

    def regex_literals(self, root_node: Node, _source: bytes, path: str, /) -> RegexScan:
        return _regex_scan(root_node, path)

    def declarations(self, root_node: Node, _source: bytes, /) -> list[Declaration]:
        result: list[Declaration] = []
        self._collect(root_node, "", Visibility.PUBLIC, _exported_names(root_node), result)
        return result

    def call_sites(self, body_node: Node, _source: bytes, /) -> list[CallSite | DeferredSite]:
        return calls_core.call_sites(body_node, load_queries(self.grammar), _RULES)

    def receiver_of(self, expression: Node) -> ReceiverKind:
        return calls_core.receiver_of(expression, _RULES)

    def locals_of(self, body_node: Node) -> Locals:
        return calls_core.locals_of(body_node, _RULES)

    def _collect(
        self,
        container: Node,
        prefix: str,
        cap: Visibility,
        exported: set[str] | None,
        result: list[Declaration],
    ) -> None:
        in_class = container.type == "block"
        for statement in container.children:
            outer = statement
            definition = statement
            if statement.type == "decorated_definition":
                inner = statement.child_by_field_name("definition")
                inner = required(inner)
                definition = inner
            if definition.type in ("function_definition", "class_definition"):
                name_node = definition.child_by_field_name("name")
                name_node = required(name_node)
                name = _text(name_node)
                kind: DeclarationKind = (
                    "class"
                    if definition.type == "class_definition"
                    else ("method" if in_class else "function")
                )
                body = definition.child_by_field_name("body")
                body = required(body)
                colon = next(c for c in definition.children if c.type == ":")
                spans = split_declaration(outer, BodyBounds(colon, body))
            elif statement.type == "assignment":
                target = statement.child_by_field_name("left")
                if target is None or target.type != "identifier":
                    continue
                name = _text(target)
                kind = "variable"
                spans = split_declaration(statement, None)
            else:
                continue
            is_public = name in exported if exported is not None else not _is_private(name)
            visibility = (Visibility.PUBLIC if is_public else Visibility.FILE).narrower(cap)
            qualified = f"{prefix}{name}"
            result.append(
                Declaration(
                    qualified_name=qualified,
                    kind=kind,
                    visibility=visibility,
                    span=spans.span,
                    signature=spans.signature,
                    body=spans.body,
                )
            )
            if kind == "class":
                self._collect(required(body), f"{qualified}.", visibility, None, result)

    def type_members(
        self, root_node: Node, _source: bytes, path: str, /
    ) -> list[TypeDecl | MemberDecl]:
        module = module_name(path)
        result: list[TypeDecl | MemberDecl] = [
            TypeDecl(module, module.rsplit(".", 1)[-1], "module", (), path, 1, root_node)
        ]
        self._collect_members(root_node, module, module, path, result)
        return result

    def discriminator_branches(self, body_node: Node, _source: bytes, /) -> list[Step] | None:
        return _steps(body_node)

    def isolation_of(
        self, declaration_node: Node, _source: bytes, _context: IsolationContext, /
    ) -> Isolation:
        return python_concurrency.isolation_of(declaration_node)

    def is_async(self, member_node: Node) -> bool:
        return python_concurrency.is_async(member_node)

    def concurrency_steps(
        self, body: Node, source: bytes, _enclosing: Isolation, /
    ) -> list[ConcurrencyStep]:
        return python_concurrency.concurrency_steps(self, body, source)

    def is_ui_type(
        self, _type_name: str, _context: IsolationContext, _scope: Node | None = None, /
    ) -> bool:
        # asyncio に UI の型は無いので、UI の更新の札は出ない
        return False

    def is_main_thread(self, _domain: Isolation, /) -> bool:
        # イベントループが主スレッドで走るとは限らず、UI を触る前提も無い
        return False

    def sharing_of(self, _type_name: str, _context: IsolationContext, /) -> Sharing:
        # asyncio の値の共有は型の宣言から確かめられないので、札は出さない
        return Sharing.UNKNOWN

    def body_of(self, member_node: Node) -> Node | None:
        definition, _ = _unwrap(member_node)
        if definition.type != "function_definition":
            return None
        return definition.child_by_field_name("body")

    def _collect_members(
        self,
        container: Node,
        owner: str,
        module: str,
        path: str,
        result: list[TypeDecl | MemberDecl],
    ) -> None:
        in_class = owner != module and container.type == "block"
        seen_fields: set[str] = set()
        for statement in container.children:
            definition, decorators = _unwrap(statement)
            line = statement.start_point.row + 1
            if definition.type == "class_definition":
                self._collect_class(statement, definition, owner, module, path, result=result)
            elif definition.type == "function_definition":
                self._collect_function(
                    statement, definition, decorators, owner, result, in_class=in_class
                )
            elif (in_class or container.type == "module") and (
                assignment := _assignment_of(statement)
            ) is not None:
                target = assignment.child_by_field_name("left")
                if target is None or target.type != "identifier":
                    continue
                annotation = assignment.child_by_field_name("type")
                name = _text(target)
                seen_fields.add(name)
                result.append(
                    MemberDecl(
                        owner,
                        name,
                        "field" if in_class else "variable",
                        _text(annotation) if annotation is not None else None,
                        (),
                        line,
                        statement,
                    )
                )
            else:
                # 取り込みや式文は型のメンバにならないので飛ばす
                continue

    def _collect_class(
        self,
        outer: Node,
        node: Node,
        owner: str,
        module: str,
        path: str,
        *,
        result: list[TypeDecl | MemberDecl],
    ) -> None:
        name = _text(required(node.child_by_field_name("name")))
        qualified = f"{owner}.{name}"
        supers = node.child_by_field_name("superclasses")
        bases = tuple(
            _text(c)
            for c in (supers.named_children if supers is not None else [])
            if c.type != "keyword_argument"
        )
        result.append(
            TypeDecl(qualified, name, "class", bases, path, outer.start_point.row + 1, outer)
        )
        body = required(node.child_by_field_name("body"))
        self._collect_members(body, qualified, module, path, result)

    def _collect_function(
        self,
        outer: Node,
        node: Node,
        decorators: list[str],
        owner: str,
        result: list[TypeDecl | MemberDecl],
        *,
        in_class: bool,
    ) -> None:
        name = _text(required(node.child_by_field_name("name")))
        returns = node.child_by_field_name("return_type")
        annotation = _text(returns) if returns is not None else None
        line = outer.start_point.row + 1
        body = node.child_by_field_name("body")
        if any(d.endswith((".setter", ".deleter")) for d in decorators):
            # getter と同じ名前のプロパティを二重に数えない
            return
        is_static = "staticmethod" in decorators
        parameters = _parameters(node, drop_first=in_class and not is_static)
        kind: MemberKind = "method"
        member_name = name
        selector = None
        if in_class and name == "__init__":
            kind = "constructor"
            member_name = owner.rsplit(".", 1)[-1]
            selector = name
            annotation = None
        elif in_class and ("property" in decorators or "cached_property" in decorators):
            kind = "property"
            parameters = ()
        result.append(
            MemberDecl(
                owner, member_name, kind, annotation, parameters, line, outer, body, selector
            )
        )
        if kind == "constructor" and body is not None:
            self._collect_self_fields(body, owner, result)

    def _collect_self_fields(
        self, body: Node, owner: str, result: list[TypeDecl | MemberDecl]
    ) -> None:
        seen = {m.name for m in result if isinstance(m, MemberDecl) and m.owner == owner}
        stack = [body]
        while stack:
            node = stack.pop()
            for child in reversed(node.children):
                if child.type in ("function_definition", "class_definition", "lambda"):
                    continue
                stack.append(child)
            assignment = _assignment_of(node)
            if assignment is None:
                continue
            target = assignment.child_by_field_name("left")
            if target is None or target.type != "attribute":
                continue
            obj = target.child_by_field_name("object")
            attr = target.child_by_field_name("attribute")
            if obj is None or attr is None or _text(obj) != "self":
                continue
            name = _text(attr)
            if name in seen:
                continue
            seen.add(name)
            annotation = assignment.child_by_field_name("type")
            result.append(
                MemberDecl(
                    owner,
                    name,
                    "field",
                    _text(annotation) if annotation is not None else None,
                    (),
                    node.start_point.row + 1,
                    node,
                )
            )
