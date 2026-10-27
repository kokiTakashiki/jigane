"""呼び出しの抽出、受け手の判別、局所変数の走査の、言語に依らない骨組み。

言語ごとの違いは、節点の名前の表と小さな判定で、使う側ごとの契約（`CallSiteRules`、`ReceiverRules`、
`LocalsRules`）を満たす型が持つ。骨組みの各関数は、要る契約だけを受け取る。
"""

from collections.abc import Mapping
from typing import Protocol

from tree_sitter import Node, Query, QueryCursor

from jigane.languages.base import (
    Argument,
    Call,
    CallParts,
    CallSite,
    Creation,
    DeferredSite,
    Locals,
    LocalVariable,
    Member,
    Name,
    Paren,
    ReceiverKind,
    Self,
    Super,
    Unknown,
)
from jigane.preconditions.required import required

Captures = dict[str, list[Node]]


def text_of(node: Node) -> str:
    return required(node.text).decode()


def within(node: Node, container: Node) -> bool:
    return container.start_byte <= node.start_byte and node.end_byte <= container.end_byte


class CallSiteRules(Protocol):
    """query の捕捉から呼び出しと遅延参照を作る契約。"""

    # 本体の呼び出しを遅延参照として別に報告する節点の種類
    deferred_bodies: frozenset[str]

    def call_site(self, captures: Captures) -> CallSite | None:
        """呼び出しの捕捉から呼び出し一つを作る。呼び出しとして数えないものは None。"""
        ...

    def method_group(self, captures: Captures) -> DeferredSite:
        """メソッド群の捕捉から遅延参照を作る。"""
        ...


class ReceiverRules(Protocol):
    """受け手の式の節点を種別へ正規化する契約。"""

    self_types: frozenset[str]
    super_types: frozenset[str]
    name_types: frozenset[str]
    # 名前の文字列のうち、含む型自身を指すもの（Python の self、Swift の Self）
    self_names: frozenset[str]
    # 型引数つきの名前。先頭の子が名前
    generic_name_types: frozenset[str]
    call_types: frozenset[str]
    # 関数の位置が識別子のこの名前のとき、その呼び出しは基底の参照（Python の super()）
    super_call_names: frozenset[str]
    creation_types: frozenset[str]
    # 生成の式で、生成する型を持つ項目の名前
    creation_type_field: str
    member_type: str
    # メンバ参照の、基の式を持つ項目の名前
    member_base_field: str

    def wrapped(self, node: Node) -> Node | None:
        """値を包むだけの式（括弧など）の中身。包まない式では None。"""
        ...

    def member_name(self, node: Node) -> Node | None:
        """メンバ参照の名前の節点。取れなければ None。"""
        ...

    def creation_name(self, parts: CallParts) -> str | None:
        """呼び出しの式が型の生成なら、生成する型の名前。それ以外では None。"""
        ...

    def call_parts(self, call: Node) -> CallParts:
        """呼び出しの式の分解。"""
        ...

    def arguments(self, node: Node) -> tuple[Argument, ...]:
        """生成の式の引数。"""
        ...


class LocalsRules(Protocol):
    """局所の宣言の読み方の契約。"""

    lambda_types: frozenset[str]
    # 内側の本体を持つ節点の種類。中の局所は、この本体の名前を隠さないので潜らない
    scope_types: frozenset[str]

    def function_name(self, node: Node) -> str | None:
        """局所関数の宣言ならその名前。局所関数の無い言語と、それ以外の節点では None。"""
        ...

    def variables_of(self, node: Node) -> list[LocalVariable]:
        """節点が束縛する局所変数。宣言でなければ空。"""
        ...

    def lambda_parameter_names(self, node: Node) -> list[str]:
        """ラムダの仮引数の名前。"""
        ...


class RuleDefaults:
    """契約のうち、言語を問わず同じに済む項目の既定。各言語の規則の型が継承する。"""

    self_names: frozenset[str] = frozenset()
    generic_name_types: frozenset[str] = frozenset()
    super_call_names: frozenset[str] = frozenset()
    creation_type_field = "type"
    member_name_field = "name"
    scope_types: frozenset[str] = frozenset()

    def wrapped(self, node: Node) -> Node | None:
        if node.type == "parenthesized_expression" and node.named_children:
            return node.named_children[0]
        return None

    def member_name(self, node: Node) -> Node | None:
        return required(node.child_by_field_name(self.member_name_field))

    def creation_name(self, _parts: CallParts, /) -> str | None:
        # 呼び出しの式の形から生成と決まらない言語では、生成は creation_types の式だけが表す
        return None

    def function_name(self, _node: Node, /) -> str | None:
        # 局所関数を持たない言語の既定
        return None

    def method_group(self, captures: Captures) -> DeferredSite:
        node = captures["method_group"][0]
        receiver = captures["receiver"][0] if "receiver" in captures else None
        name_node = captures["callee"][0] if "callee" in captures else node
        return DeferredSite(node, text_of(name_node), receiver, "method_group")


def _inside_deferred(node: Node, stop: Node, rules: CallSiteRules) -> bool:
    current = node.parent
    while current is not None and current.id != stop.id:
        if current.type in rules.deferred_bodies:
            return True
        current = current.parent
    return False


def call_sites(
    body: Node, queries: Mapping[str, Query], rules: CallSiteRules
) -> list[CallSite | DeferredSite]:
    found: dict[int, CallSite] = {}
    for _, captures in QueryCursor(queries["calls"]).matches(body):
        site = rules.call_site(captures)
        if site is not None:
            found[captures["call"][0].id] = site
    ordered_calls = sorted(found.values(), key=lambda c: (c.end, c.line))
    inline = [c for c in ordered_calls if not _inside_deferred(c.node, body, rules)]
    deferred: list[tuple[int, DeferredSite]] = []
    for _, captures in QueryCursor(queries["lambdas"]).matches(body):
        node = captures["lambda"][0]
        if _inside_deferred(node, body, rules):
            # 入れ子のラムダは外側のラムダの calls に含まれる
            continue
        inner = tuple(c for c in ordered_calls if within(c.node, node))
        deferred.append((node.end_byte, DeferredSite(node, "", None, "lambda", inner)))
    seen: set[int] = set()
    for _, captures in QueryCursor(queries["method_groups"]).matches(body):
        node = captures["method_group"][0]
        if node.id in seen or _inside_deferred(node, body, rules):
            continue
        seen.add(node.id)
        deferred.append((node.end_byte, rules.method_group(captures)))
    deferred.sort(key=lambda pair: pair[0])
    sites: list[CallSite | DeferredSite] = [*inline, *(d for _, d in deferred)]
    return sorted(sites, key=lambda s: s.end)


def _name_receiver(expression: Node, rules: ReceiverRules) -> ReceiverKind | None:
    """名前そのものを表す式の受け手。名前の式でなければ None。"""
    kind = expression.type
    if kind in rules.self_types:
        return Self()
    if kind in rules.super_types:
        return Super()
    if kind in rules.name_types:
        text = text_of(expression)
        return Self() if text in rules.self_names else Name(text)
    if kind in rules.generic_name_types:
        return Name(text_of(required(expression.children[0])))
    return None


def _member_receiver(expression: Node, rules: ReceiverRules) -> ReceiverKind:
    """メンバ参照の受け手。基点か名前が取れなければ Unknown。"""
    base = expression.child_by_field_name(rules.member_base_field)
    name = rules.member_name(expression)
    if base is None or name is None:
        return Unknown()
    return Member(base, text_of(name))


def _call_receiver(expression: Node, rules: ReceiverRules) -> ReceiverKind:
    """呼び出しの式の受け手。super の呼び出しと型の生成を見分ける。"""
    function = expression.child_by_field_name("function")
    if (
        function is not None
        and function.type in rules.name_types
        and text_of(function) in rules.super_call_names
    ):
        return Super()
    parts = rules.call_parts(expression)
    created = rules.creation_name(parts)
    if created is not None:
        return Creation(created, parts.arguments, parts.text)
    return Call(parts)


def receiver_of(expression: Node, rules: ReceiverRules) -> ReceiverKind:
    wrapped = rules.wrapped(expression)
    if wrapped is not None:
        return Paren(wrapped)
    named = _name_receiver(expression, rules)
    if named is not None:
        return named
    kind = expression.type
    if kind == rules.member_type:
        return _member_receiver(expression, rules)
    if kind in rules.call_types:
        return _call_receiver(expression, rules)
    if kind in rules.creation_types:
        type_node = expression.child_by_field_name(rules.creation_type_field)
        return Creation(
            text_of(type_node) if type_node is not None else None,
            rules.arguments(expression),
            text_of(expression),
        )
    return Unknown()


def locals_of(body: Node, rules: LocalsRules) -> Locals:
    variables: list[LocalVariable] = []
    functions: set[str] = set()
    lambda_parameters: set[str] = set()
    _scan(body, rules, variables, functions, lambda_parameters)
    return Locals(tuple(variables), frozenset(functions), frozenset(lambda_parameters))


def _scan(
    node: Node,
    rules: LocalsRules,
    variables: list[LocalVariable],
    functions: set[str],
    lambda_parameters: set[str],
) -> None:
    for child in node.children:
        function = rules.function_name(child)
        if function is not None:
            functions.add(function)
        variables.extend(rules.variables_of(child))
        if child.type in rules.lambda_types:
            lambda_parameters.update(rules.lambda_parameter_names(child))
        if child.type in rules.scope_types:
            continue
        _scan(child, rules, variables, functions, lambda_parameters)
