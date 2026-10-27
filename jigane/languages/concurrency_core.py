"""並行処理の図の抽出器の、言語に依らない骨格。

言語ごとの判定は、使う側ごとの契約（`UiTypeRules`、`DomainRules`、`WalkRules`）を満たす型が持つ。
骨格の各関数は、要る契約だけを受け取る。
"""

from typing import Protocol

from tree_sitter import Node

from jigane.languages.base import (
    CallSite,
    ConcurrencyStep,
    DeferredSite,
    Isolation,
    IsolationContext,
    NonisolatedDomain,
    UiThreadDomain,
)

UI_THREAD = UiThreadDomain()
_TYPE_DEPTH_LIMIT = 32


class CallSources(Protocol):
    def call_sites(self, body_node: Node, source: bytes) -> list[CallSite | DeferredSite]: ...


def text_of(node: Node) -> str:
    return (node.text or b"").decode()


def line_of(node: Node) -> int:
    return node.start_point.row + 1


class UiTypeRules(Protocol):
    """型の名前から UI の型かを決める契約。"""

    def is_ui_name(self, name: str, scope: Node | None) -> bool:
        """型の名前が、この言語の UI の型の表に当たるか。

        scope は名前が書かれたファイルの節点。名前だけでは紛れる言語が、import で確かめるのに使う。
        書かれたファイルが分からないときは None。
        """
        ...


class MainThreadRules(Protocol):
    """領域が主スレッドかを決める契約。"""

    def is_main_thread(self, domain: Isolation) -> bool:
        """領域が、この言語で UI を触ってよい主スレッドか。"""
        ...


class DomainRules(UiTypeRules, MainThreadRules, Protocol):
    """UI の型の宣言から領域を決める契約。型の宣言の節点の読み方を持つ。"""

    # 型の宣言の節点の種類。UI の型の継承から領域を決めるときの外枠
    type_declarations: frozenset[str]

    def type_name(self, node: Node | None) -> str | None:
        """型の宣言の名前の節点から、単純名を取り出す。"""
        ...

    def declared_bases(self, type_node: Node) -> list[str]:
        """型の宣言に書かれた上位の型の綴り。"""
        ...


class WalkRules[State](Protocol):
    """手順の木の歩き方の契約。State は、入れ子の Extractor が走査の間に共有する状態の型。"""

    # 別の本体になる宣言。降りずに飛ばす
    skipped: frozenset[str]

    def lambda_body(self, node: Node, /) -> Node | None:
        """ラムダなら本体の節点。ラムダでなければ（参照などは呼び先を決められない）None。"""
        ...

    def make_state(self, body: Node, /) -> State:
        """本体の節点から、走査の状態を作る。"""
        ...

    def walk(self, _extractor: "Extractor[State]", _node: Node, /) -> list[ConcurrencyStep] | None:
        """子の並びを手順にする方法を差し替える言語が使う。None は既定の走査。"""
        return None

    def visit(
        self, _extractor: "Extractor[State]", _node: Node, /
    ) -> list[ConcurrencyStep] | None:
        """その言語の節点の読み方。None は既定の走査（子へ降りる）。"""
        return None


class StatelessWalk(WalkRules[None]):
    """言語固有の状態を持たない言語の歩き方の既定。"""

    def make_state(self, _body: Node, /) -> None:
        return None


def is_ui_thread(domain: Isolation) -> bool:
    """UI スレッドを専用の領域で表す言語の、主スレッドの判定。"""
    return isinstance(domain, UiThreadDomain)


def is_main_thread(domain: Isolation, rules: MainThreadRules) -> bool:
    """領域が主スレッドか。言語ごとの差は rules が持ち、アダプタの判定はここから引く。"""
    return rules.is_main_thread(domain)


def is_ui_type(
    type_name: str,
    context: IsolationContext,
    rules: UiTypeRules,
    scope: Node | None = None,
) -> bool:
    """型自身か上位の型が UI の型なら真。上位は context で辿る。

    scope は type_name が書かれたファイルの節点。
    上位の型は、それを宣言した型のファイルに書かれている。
    """
    pending: list[tuple[str, Node | None]] = [(type_name, scope)]
    seen = set[str]()
    while pending and len(seen) < _TYPE_DEPTH_LIMIT:
        name, written_in = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        if rules.is_ui_name(name, written_in):
            return True
        syntaxes = context.type_syntaxes(name)
        declared_in = syntaxes[0].node if syntaxes else None
        pending.extend((base, declared_in) for base in context.supertype_names(name))
    return False


def _enclosing_type(node: Node, rules: DomainRules) -> Node | None:
    current: Node | None = node
    while current is not None:
        if current.type in rules.type_declarations:
            return current
        current = current.parent
    return None


def isolation_of(node: Node, context: IsolationContext, rules: DomainRules) -> Isolation:
    """UI の型の宣言とそのメンバは UI スレッド。それ以外は隔離されない領域。"""
    declaration = _enclosing_type(node, rules)
    if declaration is None:
        return NonisolatedDomain()
    name = rules.type_name(declaration.child_by_field_name("name"))
    # 索引に無い外部の上位の型は context から引けないので、宣言の基底の型の綴りでも判定する
    if any(
        is_ui_type(base, context, rules, declaration) for base in rules.declared_bases(declaration)
    ):
        return UI_THREAD
    if name is not None and is_ui_type(name, context, rules, declaration):
        return UI_THREAD
    return NonisolatedDomain()


def site_map(adapter: CallSources, body: Node, source: bytes) -> dict[int, CallSite]:
    sites: dict[int, CallSite] = {}
    for site in adapter.call_sites(body, source):
        if isinstance(site, CallSite):
            sites[site.node.id] = site
        else:
            sites.update({inner.node.id: inner for inner in site.calls})
    return sites


class Extractor[State]:
    def __init__(
        self,
        rules: WalkRules[State],
        sites: dict[int, CallSite],
        domain: Isolation,
        state: State,
    ) -> None:
        self.rules = rules
        self.sites = sites
        self.domain = domain
        self.state = state
        # 枝にまとめた呼び出し。通常の呼び出しとして二重に数えない
        self.consumed: set[int] = set()

    def walk(self, node: Node) -> list[ConcurrencyStep]:
        custom = self.rules.walk(self, node)
        if custom is not None:
            return custom
        steps: list[ConcurrencyStep] = []
        for child in node.children:
            steps.extend(self.visit(child))
        return steps

    def visit(self, node: Node) -> list[ConcurrencyStep]:
        if node.type in self.rules.skipped:
            return []
        handled = self.rules.visit(self, node)
        return handled if handled is not None else self.walk(node)

    def derive(self, domain: Isolation) -> "Extractor[State]":
        """別の領域で走る本体のための走査器。呼び出しの表と言語の状態は共有する。"""
        return Extractor(self.rules, self.sites, domain, self.state)

    def lambda_steps(self, argument: Node, domain: Isolation) -> list[ConcurrencyStep]:
        """渡されたラムダの本体の手順。参照は呼び先を決められないので手順にしない。"""
        body = self.rules.lambda_body(argument)
        if body is None:
            return []
        # 本体が文の並び（block）でも式でも、visit が兄弟の走査か節点の読みかを選ぶ
        return self.derive(domain).visit(body)


def concurrency_steps[State](
    rules: WalkRules[State],
    adapter: CallSources,
    body: Node,
    source: bytes,
    enclosing: Isolation,
) -> list[ConcurrencyStep]:
    sites = site_map(adapter, body, source)
    return Extractor(rules, sites, enclosing, rules.make_state(body)).walk(body)
