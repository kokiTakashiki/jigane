import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from tree_sitter import Node

from jigane.languages import concurrency_core as core
from jigane.languages.base import (
    ActorDomain,
    AwaitCall,
    BlockingWait,
    Body,
    CallSite,
    ConcurrencyStep,
    GlobalActorDomain,
    Isolation,
    IsolationContext,
    MemberAssignment,
    NonisolatedDomain,
    QueueDispatch,
    QueueDomain,
    Sharing,
    StructuredConcurrency,
    StructuredKind,
    SyncCall,
    TaskKind,
    TypeSyntax,
    UnstructuredTask,
)
from jigane.languages.concurrency_core import (
    CallSources,
    Extractor,
    WalkRules,
    line_of,
    text_of,
)

_TYPE_DECLARATIONS = frozenset({"class_declaration", "protocol_declaration"})
_TASK_GROUPS = frozenset(
    {
        "withTaskGroup",
        "withThrowingTaskGroup",
        "withDiscardingTaskGroup",
        "withThrowingDiscardingTaskGroup",
    }
)
_ADD_TASK = frozenset({"addTask", "addTaskUnlessCancelled"})
_SLEEPS = frozenset({"usleep", "sleep"})
# 別の本体になる宣言。手順を取るために降りない
_SKIPPED = frozenset({"function_declaration", "class_declaration", "protocol_declaration"})
_COMMENTS = frozenset({"comment", "multiline_comment"})
# 索引に宣言の無い外部の型の名前の表。UIKit のクラスとデリゲートのプロトコル、
# SwiftUI の View と App と Scene が @MainActor なため、上位に持つと MainActor を引き継ぐ。
# UI の型として扱うのは、そのうち UIKit と View（App と Scene は画面の部品ではない）。
_UIKIT_TYPE = re.compile(r"UI[A-Z]\w*")
_SWIFTUI_VIEW = "View"
_SWIFTUI_MAIN_ACTOR = frozenset({_SWIFTUI_VIEW, "App", "Scene"})


def isolation_of(node: Node, context: IsolationContext) -> Isolation:
    """宣言の領域。

    自分の注記が最優先。無ければ所属する型の領域に従う。型の領域は、actor の型は actor、
    型の宣言に付いた注記、上位のクラスやプロトコルが持つグローバル actor、の順に解く。
    拡張のメンバは拡張自身の注記を先に見て、無ければ型の宣言の注記に進む。どれも無ければ隔離されない領域。
    """
    own = _annotation(node)
    if own is not None:
        return own
    if node.type in _TYPE_DECLARATIONS:
        return _type_isolation(node, context)
    # メンバは自分の注記が無ければ、所属する型の領域に従う。入れ子の型には届かせない
    owner = node.parent.parent if node.parent is not None else None
    if owner is not None and owner.type == "class_declaration":
        return isolation_of(owner, context)
    return NonisolatedDomain()


def _type_name(node: Node) -> str:
    name = node.child_by_field_name("name")
    return "".join(text_of(name).split()).rsplit(".", 1)[-1] if name is not None else ""


def _declaration_kind(node: Node) -> str:
    kind = node.child_by_field_name("declaration_kind")
    return text_of(kind) if kind is not None else ""


def _type_isolation(node: Node, context: IsolationContext) -> Isolation:
    """型の宣言か拡張の領域。node 自身の注記は呼び出し側が見終えている。"""
    name = _type_name(node)
    if _declaration_kind(node) == "actor":
        return ActorDomain(name)
    if _declaration_kind(node) == "extension":
        # 拡張に付いた注記はその拡張のメンバだけに効く。別の拡張の注記は拾わず、型の宣言まで遡る
        for syntax in context.type_syntaxes(name):
            if _declaration_kind(syntax.node) == "extension":
                continue
            declared = _annotation(syntax.node)
            if declared is not None:
                return declared
            if _declaration_kind(syntax.node) == "actor":
                return ActorDomain(name)
    return _inherited(name, context, set())


def _inherited(name: str, context: IsolationContext, seen: set[str]) -> Isolation:
    """上位のクラスと準拠したプロトコルから引き継ぐグローバル actor。無ければ隔離されない領域。"""
    seen.add(name)
    for base in context.supertype_names(name):
        if base in seen:
            continue
        if _UIKIT_TYPE.fullmatch(base) or base in _SWIFTUI_MAIN_ACTOR:
            return GlobalActorDomain("MainActor")
        for syntax in context.type_syntaxes(base):
            if _declaration_kind(syntax.node) == "extension":
                continue
            declared = _annotation(syntax.node)
            # actor は継承も準拠先にもならないので、引き継げるのはグローバル actor だけ
            if isinstance(declared, GlobalActorDomain):
                return declared
        deeper = _inherited(base, context, seen)
        if isinstance(deeper, GlobalActorDomain):
            return deeper
    return NonisolatedDomain()


def is_ui_type(type_name: str, context: IsolationContext, scope: Node | None = None) -> bool:
    """型自身か上位の型が UIKit の型か SwiftUI の View なら UI の型。上位は context で辿る。"""
    return core.is_ui_type(type_name, context, _RULES, scope)


class _Sendability(Enum):
    CHECKED = auto()
    UNCHECKED = auto()
    NONE = auto()
    UNKNOWN = auto()


def _bases_in(syntax: Node) -> list[tuple[str, bool]]:
    """宣言か拡張の継承リストの (単純名, `@unchecked` が付くか)。"""
    found = []
    previous: Node | None = None
    for child in syntax.children:
        if child.type == "inheritance_specifier":
            inherited = child.child_by_field_name("inherits_from")
            name = text_of(inherited).split("<")[0].strip() if inherited is not None else ""
            unchecked = previous is not None and "unchecked" in text_of(previous)
            found.append((name.rsplit(".", 1)[-1], unchecked))
        previous = child if child.type == "attribute" else None
    return found


def _sendability(name: str, context: IsolationContext, seen: set[str]) -> _Sendability:
    """型が Sendable に適合するか。継承と準拠を辿り、宣言を見られない上位が残れば UNKNOWN。"""
    seen.add(name)
    syntaxes = context.type_syntaxes(name)
    if not syntaxes:
        return _Sendability.UNKNOWN
    unchecked = False
    unresolved = False
    for syntax in syntaxes:
        for base, is_unchecked in _bases_in(syntax.node):
            if base == "Sendable":
                if not is_unchecked:
                    return _Sendability.CHECKED
                unchecked = True
            elif base not in seen:
                found = _sendability(base, context, seen)
                if found is _Sendability.CHECKED:
                    return found
                unchecked = unchecked or found is _Sendability.UNCHECKED
                unresolved = unresolved or found is _Sendability.UNKNOWN
    if unchecked:
        return _Sendability.UNCHECKED
    return _Sendability.UNKNOWN if unresolved else _Sendability.NONE


def _has_stored_var(syntax: Node) -> bool:
    body = syntax.child_by_field_name("body")
    if body is None:
        return False
    for member in body.children:
        if member.type != "property_declaration":
            continue
        binding = next((c for c in member.children if c.type == "value_binding_pattern"), None)
        computed = any(c.type == "computed_property" for c in member.children)
        if binding is not None and text_of(binding).startswith("var") and not computed:
            return True
    return False


def _non_class_sharing(declaration: Node) -> Sharing:
    """class 以外の宣言の共有の性質。"""
    # protocol は具体の型が決まらず、struct、enum、actor は渡しても競合しない
    return Sharing.UNKNOWN if declaration.type == "protocol_declaration" else Sharing.SAFE


def _class_sharing(
    type_name: str, syntaxes: Sequence[TypeSyntax], declaration: Node, context: IsolationContext
) -> Sharing:
    """class の共有の性質。グローバル actor への隔離と Sendable への適合、var の有無から決める。"""
    if isinstance(isolation_of(declaration, context), GlobalActorDomain):
        return Sharing.SAFE
    sendability = _sendability(type_name, context, set())
    if sendability is _Sendability.UNKNOWN:
        return Sharing.UNKNOWN
    if sendability is _Sendability.CHECKED:
        return Sharing.SAFE
    if any(_has_stored_var(s.node) for s in syntaxes):
        return Sharing.MUTABLE
    # `@unchecked Sendable` で var が無ければ、適合を宣言した作者の保証に任せる
    return Sharing.SAFE if sendability is _Sendability.UNCHECKED else Sharing.NOT_SENDABLE


def sharing_of(type_name: str, context: IsolationContext) -> Sharing:
    """型の値を actor の境界の向こうへ渡したときの共有の性質。宣言から確かめられるものだけ答える。

    class だけが対象。struct、enum、actor、グローバル actor に隔離された class、
    Sendable に適合した class は SAFE。型が索引に無いか、上位の宣言を見られず適合を決められない
    ときは UNKNOWN。
    """
    syntaxes = context.type_syntaxes(type_name)
    declarations = [s.node for s in syntaxes if _declaration_kind(s.node) != "extension"]
    if not declarations:
        return Sharing.UNKNOWN
    if _declaration_kind(declarations[0]) != "class":
        return _non_class_sharing(declarations[0])
    return _class_sharing(type_name, syntaxes, declarations[0], context)


def _is_main_thread(domain: Isolation) -> bool:
    """MainActor と、主スレッドのキュー（`DispatchQueue.main` と、先頭を省いた `main`）。"""
    match domain:
        case GlobalActorDomain(name="MainActor"):
            return True
        case QueueDomain(expression=expression):
            compact = expression.replace(" ", "")
            return compact == "main" or compact.endswith(".main")
        case _:
            return False


def _attribute_domain(attribute: Node) -> GlobalActorDomain | None:
    user_type = next((c for c in attribute.children if c.type == "user_type"), None)
    if user_type is None:
        return None
    name = text_of(user_type)
    return GlobalActorDomain(name) if name.endswith("Actor") else None


def _annotation(node: Node) -> Isolation | None:
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return None
    for modifier in modifiers.children:
        if modifier.type == "member_modifier" and text_of(modifier).startswith("nonisolated"):
            return NonisolatedDomain()
        if modifier.type == "attribute":
            domain = _attribute_domain(modifier)
            if domain is not None:
                return domain
    return None


def _closure_annotation(closure: Node) -> Isolation | None:
    """`{ @MainActor in ... }` のように閉包が自分で持つ注記。無ければ None。"""
    for child in closure.children:
        if child.type == "attribute":
            domain = _attribute_domain(child)
            if domain is not None:
                return domain
    return None


def is_async(node: Node) -> bool:
    if any(c.type == "async" for c in node.children):
        return True
    computed = next((c for c in node.children if c.type == "computed_property"), None)
    return computed is not None and any(
        c.type == "computed_getter" and any(g.type == "async" for g in c.children)
        for c in computed.children
    )


def _is_async_let(node: Node) -> bool:
    return node.type == "property_declaration" and any(c.type == "async" for c in node.children)


def _lambda_of(call: Node) -> Node | None:
    suffix = next((c for c in call.children if c.type == "call_suffix"), None)
    if suffix is None:
        return None
    return next((c for c in suffix.children if c.type == "lambda_literal"), None)


def _callee(call: Node) -> tuple[Node | None, str | None]:
    """呼び出しの (受け手, 名前)。受け手の無い呼び出しの受け手は None。"""
    function = call.children[0]
    if function.type == "simple_identifier":
        return None, text_of(function)
    if function.type == "navigation_expression":
        suffix = function.child_by_field_name("suffix")
        name = suffix.child_by_field_name("suffix") if suffix is not None else None
        return function.child_by_field_name("target"), text_of(name) if name is not None else None
    return None, None


def _declared_semaphores(root: Node) -> frozenset[str]:
    found: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "property_declaration":
            continue
        value = node.child_by_field_name("value")
        if value is None or value.type != "call_expression":
            continue
        _, name = _callee(value)
        if name == "DispatchSemaphore":
            found.update(text_of(pattern) for pattern in node.children_by_field_name("name"))
    return frozenset(found)


def _is_main_actor_run(expression: Node) -> bool:
    if expression.type != "call_expression":
        return False
    receiver, name = _callee(expression)
    return receiver is not None and text_of(receiver) == "MainActor" and name == "run"


def _lambdas_in(node: Node) -> list[Node]:
    found: list[Node] = []
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        if current.type == "lambda_literal":
            found.append(current)
        else:
            stack.extend(reversed(current.children))
    return found


@dataclass
class _State:
    semaphores: frozenset[str]
    # 0 より大きい間は、await に包まれない呼び出しと代入を手順にしない。
    # 別の経路が既に手順にした呼び出しと、MainActor で走ると分かっている閉包の中身が対象
    quiet: int = 0


def _walk(x: Extractor[_State], node: Node) -> list[ConcurrencyStep]:
    steps: list[ConcurrencyStep] = []
    children = node.children
    position = 0
    while position < len(children):
        child = children[position]
        if not _is_async_let(child):
            steps.extend(x.visit(child))
            position += 1
            continue
        # 続けて書いた async let を一つにまとめる。コメントは飛ばし、他の文が来たら切る
        group = [child]
        position += 1
        while position < len(children):
            following = children[position]
            if following.type in _COMMENTS:
                position += 1
            elif _is_async_let(following):
                group.append(following)
                position += 1
            else:
                break
        steps.append(_async_lets(x, group))
    return steps


def _async_lets(x: Extractor[_State], group: list[Node]) -> StructuredConcurrency:
    branches: list[Body] = []
    for declaration in group:
        value = declaration.child_by_field_name("value")
        # 子タスクは囲む領域を引き継がない
        child = x.derive(NonisolatedDomain())
        steps = tuple(_branch(child, value)) if value is not None else ()
        branches.append(Body(NonisolatedDomain(), steps))
    return StructuredConcurrency(
        StructuredKind.JOIN_ALL, tuple(branches), line_of(group[0]), "async let"
    )


def _branch(x: Extractor[_State], value: Node) -> list[ConcurrencyStep]:
    # await を書かない右辺の呼び出しも、子タスクの中で待たれる
    if value.type == "await_expression":
        return x.visit(value)
    calls: list[ConcurrencyStep] = [AwaitCall(c, c.line) for c in _inline_calls(x, value)]
    # 同じ呼び出しを SyncCall として二重に数えない
    x.state.quiet += 1
    try:
        return [*calls, *x.visit(value)]
    finally:
        x.state.quiet -= 1


def _inline_calls(x: Extractor[_State], node: Node) -> list[CallSite]:
    """node の中の呼び出しを評価の順（引数が先、呼び出し自身が後）に並べる。"""
    found: list[CallSite] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.id in x.sites:
            found.append(x.sites[current.id])
        stack.extend(current.children)
    found.reverse()
    return found


def _sync_call(x: Extractor[_State], node: Node) -> list[ConcurrencyStep]:
    if x.state.quiet:
        return []
    # 引数と受け手の連なりの中の呼び出しは、walk が先に手順にしている
    site = x.sites.get(node.id)
    return [SyncCall(site, site.line)] if site is not None else []


def _assignment(x: Extractor[_State], node: Node) -> list[ConcurrencyStep]:
    if x.state.quiet:
        return []
    target = node.child_by_field_name("target")
    navigation = target.children[0] if target is not None and target.children else None
    if navigation is None or navigation.type != "navigation_expression":
        return []
    receiver = navigation.child_by_field_name("target")
    suffix = navigation.child_by_field_name("suffix")
    name = suffix.child_by_field_name("suffix") if suffix is not None else None
    if receiver is None or name is None:
        return []
    return [MemberAssignment(receiver, text_of(name), line_of(node))]


def _await(x: Extractor[_State], node: Node) -> list[ConcurrencyStep]:
    expression = node.child_by_field_name("expr")
    if expression is not None and expression.type == "call_expression":
        _, name = _callee(expression)
        if name in _TASK_GROUPS:
            return _task_group(x, expression)
    steps: list[ConcurrencyStep] = [AwaitCall(c, line_of(node)) for c in _inline_calls(x, node)]
    # MainActor.run の閉包は MainActor で走る。囲む領域の呼び出しとして数えると誤って札が付く
    on_main = expression is not None and _is_main_actor_run(expression)
    x.state.quiet += on_main
    try:
        # 閉包の中の手順は、await の呼び出しの後ろに足す
        for lambda_node in _lambdas_in(node):
            steps.extend(x.walk(lambda_node))
    finally:
        x.state.quiet -= on_main
    return steps


def _task_group(x: Extractor[_State], call: Node) -> list[ConcurrencyStep]:
    body = _lambda_of(call)
    branches: list[Body] = []
    stack = list(reversed(body.children)) if body is not None else []
    while stack:
        current = stack.pop()
        if current.type == "call_expression":
            _, name = _callee(current)
            added = _lambda_of(current)
            if name in _ADD_TASK and added is not None:
                isolation = _closure_annotation(added) or NonisolatedDomain()
                branches.append(Body(isolation, tuple(x.lambda_steps(added, isolation))))
                continue
        stack.extend(reversed(current.children))
    return [
        StructuredConcurrency(
            StructuredKind.SCOPE, tuple(branches), line_of(call), "タスクグループ", "addTask"
        )
    ]


def _special(x: Extractor[_State], call: Node) -> list[ConcurrencyStep] | None:
    receiver, name = _callee(call)
    closure = _lambda_of(call)
    line = line_of(call)
    if receiver is None and name in _TASK_GROUPS:
        return _task_group(x, call)
    if receiver is None and name == "Task" and closure is not None:
        # Task { } は囲む本体の領域を引き継ぐ
        isolation = _closure_annotation(closure) or x.domain
        body = Body(isolation, tuple(x.lambda_steps(closure, isolation)))
        return [UnstructuredTask(TaskKind.TASK, body, line)]
    if (
        receiver is not None
        and name == "detached"
        and text_of(receiver) == "Task"
        and closure is not None
    ):
        isolation = _closure_annotation(closure) or NonisolatedDomain()
        body = Body(isolation, tuple(x.lambda_steps(closure, isolation)))
        return [UnstructuredTask(TaskKind.DETACHED, body, line)]
    if receiver is not None and name == "async" and closure is not None:
        queue = QueueDomain(text_of(receiver))
        return [QueueDispatch(Body(queue, tuple(x.lambda_steps(closure, queue))), line)]
    if _blocks(x.state, receiver, name):
        label = f"{text_of(receiver)}.{name}" if receiver is not None else str(name)
        return [BlockingWait(label, line)]
    return None


def _blocks(state: _State, receiver: Node | None, name: str | None) -> bool:
    if receiver is None:
        return name in _SLEEPS
    target = text_of(receiver)
    if name == "sleep":
        return target == "Thread"
    if name == "wait":
        # 受け手の型は構文から分からないので、名前に semaphore を含むか、
        # 本体の中で DispatchSemaphore を束縛した名前かで見分ける
        return "semaphore" in target.lower() or target in state.semaphores
    return False


class _Rules(WalkRules[_State]):
    skipped = _SKIPPED

    def is_ui_name(self, name: str, _scope: Node | None, /) -> bool:
        return _UIKIT_TYPE.match(name) is not None or name == _SWIFTUI_VIEW

    def is_main_thread(self, domain: Isolation) -> bool:
        return _is_main_thread(domain)

    def lambda_body(self, node: Node) -> Node | None:
        return node if node.type == "lambda_literal" else None

    def make_state(self, body: Node) -> _State:
        return _State(_declared_semaphores(body))

    def walk(self, extractor: Extractor[_State], node: Node) -> list[ConcurrencyStep] | None:
        return _walk(extractor, node)

    def visit(self, extractor: Extractor[_State], node: Node) -> list[ConcurrencyStep] | None:
        if node.type == "await_expression":
            return _await(extractor, node)
        if node.type == "call_expression":
            special = _special(extractor, node)
            if special is not None:
                return special
            return [*extractor.walk(node), *_sync_call(extractor, node)]
        if node.type == "assignment":
            return [*extractor.walk(node), *_assignment(extractor, node)]
        return None


_RULES = _Rules()


def is_main_thread(domain: Isolation) -> bool:
    return core.is_main_thread(domain, _RULES)


def concurrency_steps(
    adapter: CallSources, body: Node, source: bytes, enclosing: Isolation
) -> list[ConcurrencyStep]:
    return core.concurrency_steps(_RULES, adapter, body, source, enclosing)
