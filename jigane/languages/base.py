from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from tree_sitter import Node, Tree

from jigane.languages.grammars import BuiltinGrammar, DeclaredGrammar, GrammarTarget, get_parser

DeclarationKind = Literal["class", "function", "method", "variable"]


class Visibility(StrEnum):
    # そのファイルの中でしか見えない
    FILE = "file"
    # ファイルの外から見えるが、モジュールの外へは出ない
    INTERNAL = "internal"
    # 言語の上でモジュールの外へ出る
    PUBLIC = "public"

    def narrower(self, other: "Visibility") -> "Visibility":
        return self if _RANK[self] <= _RANK[other] else other

    def wider(self, other: "Visibility") -> "Visibility":
        return self if _RANK[self] >= _RANK[other] else other


_RANK = {Visibility.FILE: 0, Visibility.INTERNAL: 1, Visibility.PUBLIC: 2}


@dataclass(frozen=True)
class Span:
    # 1 始まりで両端を含む
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Declaration:
    # 入れ子の宣言を "." でつなぐ。名前空間とパッケージは含めない
    qualified_name: str
    # class は型宣言すべて（struct、interface、enum、record など）を指す。
    # enum の要素とプロパティは variable
    kind: DeclarationKind
    # 囲む型の可視性より広くしない（Visibility.narrower で絞る）
    visibility: Visibility
    # 宣言全体。デコレータを含む。span、signature、body は split_declaration の返す値に従う
    span: Span
    # 宣言の先頭から本体の直前の行まで。本体が宣言と同じ行で始まるときはその行までを含む
    signature: Span
    # 本体の無い宣言と、本体がシグネチャと同じ行で始まる宣言では None
    body: Span | None

    @property
    def is_public(self) -> bool:
        # そのファイルの外から参照できる宣言か。INTERNAL を含む
        return self.visibility is not Visibility.FILE


@dataclass(frozen=True)
class DeclarationSpans:
    # split_declaration が返す全体、シグネチャ、本体の範囲。Declaration の同名の欄へ入れる
    span: Span
    signature: Span
    body: Span | None


@dataclass(frozen=True)
class BodyBounds:
    # 本体を開く節点と閉じる節点の組。本体を一つの節点で持つ言語は、その節点を両方に渡す
    opener: Node
    closer: Node


def span_of(first: Node, last: Node | None = None) -> Span:
    """first の先頭行から、last（無ければ first）の末尾行までの範囲。"""
    end = first if last is None else last
    return Span(first.start_point.row + 1, end.end_point.row + 1)


def split_declaration(node: Node, bounds: BodyBounds | None) -> DeclarationSpans:
    """宣言の節点を全体、シグネチャ、本体の範囲に分ける。

    bounds が None の宣言は本体を持たない。本体を一つの節点で持つ言語は、
    その節点を BodyBounds の opener と closer の両方に渡す。
    """
    whole = span_of(node)
    if bounds is None:
        return DeclarationSpans(whole, whole, None)
    opener_row = bounds.opener.start_point.row + 1
    closer_row = bounds.closer.end_point.row + 1
    if closer_row <= opener_row:
        return DeclarationSpans(whole, Span(whole.start_line, opener_row), None)
    return DeclarationSpans(
        whole, Span(whole.start_line, opener_row), Span(opener_row + 1, closer_row)
    )


TypeKind = Literal["class", "struct", "record", "interface", "enum", "delegate", "module"]
# variable はモジュール直下の変数。名前の解決と呼び出しの数え方に加えず、クラス図のために集める
MemberKind = Literal["method", "constructor", "field", "property", "parameter", "variable"]


@dataclass(frozen=True)
class ParameterDecl:
    name: str
    # 型注記の原文
    type_annotation: str
    optional: bool
    # 呼び出しで書く外部名。内部名と分かれる Swift だけが持ち、`_` は外部名なし。無ければ None
    external_name: str | None = None


class TypeScope(StrEnum):
    # 宣言したファイルの中でしか名前が通らない。同じ名前の型が別のファイルにあっても別の型
    FILE = "file"
    # 宣言したファイルの外からも名前が通る。同じ名前の宣言は同じ型
    MODULE = "module"


@dataclass(frozen=True)
class TypeDecl:
    qualified_name: str
    name: str
    kind: TypeKind
    # 基底の型とインターフェースの型注記の原文。宣言の順
    bases: tuple[str, ...]
    path: str
    # 宣言の先頭行。1 始まり
    line: int
    node: Node = field(compare=False, repr=False)
    scope: TypeScope = TypeScope.MODULE


@dataclass(frozen=True)
class MemberDecl:
    # 所有する型の修飾名
    owner: str
    name: str
    kind: MemberKind
    # フィールドとプロパティの型注記、メソッドの戻り値の型の原文。無ければ None
    type_annotation: str | None
    parameters: tuple[ParameterDecl, ...]
    line: int
    # 宣言全体の節点
    node: Node = field(compare=False, repr=False)
    # 本体の節点。本体を持たない宣言では None
    body: Node | None = field(default=None, compare=False, repr=False)
    # declarations() の修飾名が別の形（Swift の `foo(label:)`）のときの、所有する型を除いた名前
    selector: str | None = None


@dataclass(frozen=True)
class Argument:
    """引数一つ。名前付き引数ならその名前（コロンを除く）。"""

    expression: Node = field(compare=False, repr=False)
    name: str | None


@dataclass(frozen=True)
class CallSite:
    """本体の中の呼び出し一つ。受け手は節点のまま渡し、種別への正規化は receiver_of が行う。"""

    # 呼び出しの式全体の節点
    node: Node
    # 呼び出しの名前の原文。生成では生成する型の原文、型の無い生成では "new"
    name: str
    # 受け手の式。受け手の無い呼び出しと生成では None
    receiver: Node | None
    arguments: tuple[Argument, ...]
    is_creation: bool
    # 評価の順を決める、呼び出しの式の終わりのバイト位置
    end: int
    # 呼び出しの名前（無ければ式全体）の行。1 始まり
    line: int


@dataclass(frozen=True)
class DeferredSite:
    """その場では実行されず、あとで呼ばれる参照。ラムダとメソッド群。"""

    node: Node
    # メソッド群の名前の原文。ラムダでは空
    name: str
    # メソッド群の受け手の式。裸の名前では None
    receiver: Node | None
    kind: Literal["lambda", "method_group"]
    # ラムダの本体の全ての呼び出し（入れ子のラムダの中も含む）。評価の順
    calls: tuple[CallSite, ...] = ()

    @property
    def end(self) -> int:
        return self.node.end_byte


@dataclass(frozen=True)
class CallParts:
    """呼び出しの式の分解。receiver は受け手の式の節点で、受け手の無い呼び出しでは None。"""

    receiver: Node | None
    # 呼び出しの名前の原文。決まらなければ None
    name: str | None
    arguments: tuple[Argument, ...]
    # 呼び出しの式全体の原文
    text: str


@dataclass(frozen=True)
class Self:
    """this、self。"""


@dataclass(frozen=True)
class Super:
    """base、super。"""


@dataclass(frozen=True)
class Name:
    """識別子。ジェネリクスの型引数は含まない。"""

    text: str


@dataclass(frozen=True)
class Member:
    """メンバの参照 base.name。"""

    base: Node = field(compare=False, repr=False)
    name: str


@dataclass(frozen=True)
class Call:
    """呼び出しの式。値の型は呼び先の戻り値の型になる。"""

    parts: CallParts


@dataclass(frozen=True)
class Creation:
    """生成の式。type_text は型を省いた生成では None。"""

    type_text: str | None
    arguments: tuple[Argument, ...]
    # 生成の式全体の原文
    text: str


@dataclass(frozen=True)
class Paren:
    """括弧で包んだ式。"""

    inner: Node = field(compare=False, repr=False)


@dataclass(frozen=True)
class Unknown:
    """受け手の手がかりにならない式。"""


ReceiverKind = Self | Super | Name | Member | Call | Creation | Paren | Unknown


@dataclass(frozen=True)
class LocalVariable:
    name: str
    # 型注記の原文。型を推論させる宣言（var）と、型の無い宣言では None
    type_annotation: str | None
    # 初期化式の節点。無ければ None
    initializer: Node | None = field(compare=False, repr=False)


@dataclass(frozen=True)
class Locals:
    """本体の中の局所の宣言。variables は文書順で、同名は後のものが前のものを隠す。"""

    variables: tuple[LocalVariable, ...]
    functions: frozenset[str]
    lambda_parameters: frozenset[str]


@dataclass(frozen=True)
class Return:
    """値を返して流れが終わる文、または流れを終わらせる文（raise など）。"""

    # 返す式がメンバ参照 a.b の形ならその原文。それ以外の式と、値を返さない終わりでは None
    value: str | None


@dataclass(frozen=True)
class BranchArm:
    # 腕に入る条件の呼び名。if の真は "yes"、match の腕は "case パターン"
    label: str
    steps: tuple["Step", ...]


@dataclass(frozen=True)
class Branch:
    """条件分岐一つ。腕の中の Return と Branch だけを残し、他の文は落とす。"""

    # 条件の原文。match では "match 主語"
    condition: str
    arms: tuple[BranchArm, ...]
    # どの腕にも入らなかった流れが分岐の後へ抜けるときの、その辺の呼び名。抜けなければ None
    fallthrough_label: str | None


Step = Return | Branch


@runtime_checkable
class LanguageAdapter(Protocol):
    # 読み取り専用の property で宣言する。可変属性の宣言は不変（invariant）になり、
    # 固定長のタプルを持つクラス変数が tuple[str, ...] に適合しなくなる。
    @property
    def name(self) -> str: ...

    # tree-sitter の言語名
    @property
    def grammar(self) -> str: ...

    @property
    def suffixes(self) -> frozenset[str]: ...

    @property
    def comment_node_types(self) -> frozenset[str]: ...

    # import や using など、他のファイルの名前を取り込む文の節点の型
    @property
    def import_node_types(self) -> frozenset[str]: ...

    # basename またはディレクトリ名に対する fnmatch のパターン
    @property
    def test_directory_names(self) -> tuple[str, ...]: ...

    @property
    def test_file_patterns(self) -> tuple[str, ...]: ...

    @property
    def build_file_patterns(self) -> tuple[str, ...]: ...

    def declarations(self, root_node: Node, source: bytes, /) -> list[Declaration]: ...


@runtime_checkable
class SequenceAdapter(LanguageAdapter, Protocol):
    """シーケンス図の索引に宣言を渡せる言語アダプタ（4.2.10 項の口）。"""

    # 受け手の無い Foo() が、Foo が型なら生成を意味する言語か（Python、Swift）。
    # 生成を new で構文に区別する言語では False
    @property
    def constructs_by_call(self) -> bool: ...

    # 呼び出しの引数のラベルが仮引数の位置に結び付き、多重定義を選ぶ言語か（Swift）。
    # 名前付き引数の順序を入れ替えられる言語（C#、Python）では False
    @property
    def positional_argument_labels(self) -> bool: ...

    # コンストラクタの、図と題に出す名前。None なら宣言の名前
    # （C#、Java は型の名前、Python は __init__）。
    # 解決は宣言の名前（型の名前）で引くので、これは表示だけを変える（Swift の init）
    @property
    def constructor_display_name(self) -> str | None: ...

    def anonymous_class_display(self, qualified: str, /) -> str | None:
        """型の修飾名が無名クラスなら、図の参加者名を返す。そうでなければ None。

        無名クラスの識別の綴りは、宣言と索引を作るアダプタだけが知る。図はここを引く。
        """
        ...

    def simple_type_name(self, raw: str, /) -> str:
        """型注記の原文から、その言語の書き方（Null 許容、総称型、修飾）を落とした単純名を取る。"""
        ...

    def composition_type_names(self, raw: str, /) -> list[str]:
        """合成の型（Swift の `any A & B`）の成分の単純名。合成でなければ空。"""
        ...

    def is_external_type_name(self, name: str, /) -> bool:
        """索引に無い識別子 name を、外部の型の静的な呼び出しの受け手とみなしてよいか。"""
        ...

    # プログラムの入口のメンバ名（main など）。呼び手の有無によらず入口になる
    @property
    def entry_point_names(self) -> frozenset[str]: ...

    def type_members(
        self, root_node: Node, source: bytes, path: str, /
    ) -> list[TypeDecl | MemberDecl]:
        """型とメンバの宣言を、文書順（型の直後にそのメンバ、入れ子の型はメンバの位置）で返す。

        partial のように一つの型が複数の宣言に分かれても、併合せずそのまま返す。
        """
        ...

    def body_of(self, member_node: Node, /) -> Node | None:
        """メンバの宣言の節点から、呼び出しを探す本体の節点を返す。本体が無ければ None。"""
        ...

    def call_sites(self, body_node: Node, source: bytes, /) -> list[CallSite | DeferredSite]:
        """本体の呼び出しの箇所を評価の順（終わりの位置の昇順）に返す。

        ラムダの本体の中の呼び出しは CallSite にせず、DeferredSite.calls に入れる。
        """
        ...

    def receiver_of(self, expression: Node, /) -> ReceiverKind:
        """式を、言語に依らない受け手の種別に正規化する。"""
        ...

    def locals_of(self, body_node: Node, /) -> Locals:
        """本体の中の局所変数、局所の関数、ラムダの仮引数を返す。"""
        ...

    def discriminator_branches(self, body_node: Node, source: bytes, /) -> list[Step] | None:
        """本体の分岐と return を、言語に依らない形で文書順に返す。

        関数が列挙型の値を返すかの判定はせず、メンバ参照を返す return を Return.value に載せる。
        この言語がこの読み取りを持たなければ None。
        """
        ...

    def imported_names(self, root_node: Node, source: bytes, path: str, /) -> dict[str, str]:
        """ファイルの import が束縛する名前から、取り込んだ元の修飾名への表を返す。

        元はモジュールの修飾名か「モジュール.中の名前」。取り込みで名前を束縛しない言語は空の表を返す。
        """
        ...


@dataclass(frozen=True)
class ActorDomain:
    """actor の型一つの領域。"""

    type_name: str


@dataclass(frozen=True)
class GlobalActorDomain:
    """グローバル actor 一つの領域。MainActor もここで、名前が "MainActor" の値一つだけで表す。"""

    name: str


@dataclass(frozen=True)
class NonisolatedDomain:
    """どの actor にも隔離されない領域。注記が無い関数の既定でもある。"""


@dataclass(frozen=True)
class QueueDomain:
    """キュー一つの領域。expression はキューを指すソースの式の原文。"""

    expression: str


@dataclass(frozen=True)
class EventLoopDomain:
    """イベントループ一つの領域。async の本体が単一の直列の実行器で走る言語（asyncio）が使う。"""


@dataclass(frozen=True)
class UiThreadDomain:
    """UI スレッドの領域。UI の型のメンバが走る、単一の直列の実行器（WPF、WinForms、MAUI）。"""


# 隔離の領域一つ。等値で同じ領域かを比べる
Isolation = (
    ActorDomain
    | GlobalActorDomain
    | NonisolatedDomain
    | QueueDomain
    | EventLoopDomain
    | UiThreadDomain
)


class Sharing(StrEnum):
    """型の値を actor の境界の向こうへ渡したときの共有の性質。宣言から確かめられる範囲だけ。"""

    # 値型、actor、Sendable に適合した型、グローバル actor の型など、渡しても競合しない
    SAFE = "safe"
    # 型を解けない、Sendable の適合を確かめられない、言語が答えを持たない。札は出さない
    UNKNOWN = "unknown"
    # Sendable に適合しない参照型
    NOT_SENDABLE = "not_sendable"
    # 可変の項目（var）を持ち、適合の確認を回避している（または適合しない）参照型
    MUTABLE = "mutable"


@dataclass(frozen=True)
class Body:
    """新しい実行文脈の本体。走る領域と、その中の手順の列を組にする。"""

    isolation: Isolation
    steps: tuple["ConcurrencyStep", ...]


@dataclass(frozen=True)
class AwaitCall:
    """await に包まれた呼び出し一つ。"""

    call: CallSite
    line: int


@dataclass(frozen=True)
class SyncCall:
    """await に包まれない呼び出し一つ。async let の右辺の呼び出しは AwaitCall なので含まない。"""

    call: CallSite
    line: int


@dataclass(frozen=True)
class MemberAssignment:
    """`label.text = ...` のような、受け手のメンバへの代入一つ。UI の状態の書き換えの手がかり。"""

    # 受け手の式。`self.items` では self の式
    receiver: Node
    member: str
    line: int


class StructuredKind(StrEnum):
    """構造化された並行の意味。言語の構文名ではなく、待ち方で分ける。"""

    JOIN_ALL = "join_all"
    RACE = "race"
    SCOPE = "scope"


# 意味の値ごとの説明。図の凡例はここから組み立てる
STRUCTURED_KIND_MEANINGS: dict[StructuredKind, str] = {
    StructuredKind.JOIN_ALL: "全部の完了を待つ並列",
    StructuredKind.RACE: "最初の一つの完了を待つ競争",
    StructuredKind.SCOPE: "スコープが閉じるまで全部を待つ群",
}


class TaskKind(StrEnum):
    TASK = "task"
    DETACHED = "detached"


@dataclass(frozen=True)
class StructuredConcurrency:
    """構造化された並行。枝ごとに中の手順の列を持つ。"""

    kind: StructuredKind
    branches: tuple[Body, ...]
    line: int
    # par の見出しに出す、言語ごとの呼び名（`async let`、`gather`、`WhenAll` など）
    label: str
    # 枝へ入る矢印に書く名前。None なら label を使う
    branch_label: str | None = None


@dataclass(frozen=True)
class UnstructuredTask:
    """構造化されない Task。"""

    kind: TaskKind
    body: Body
    line: int
    # 戻り値を保持している（後で取り消せる）。既定は捨てている扱いで、
    # 保持を見分けない言語はそのまま
    handle_kept: bool = False


@dataclass(frozen=True)
class QueueDispatch:
    """キューへの投げ込み。body.isolation は QueueDomain。label は矢印に書く投げ込みの名前。"""

    body: Body
    line: int
    label: str = "async"


@dataclass(frozen=True)
class BlockingWait:
    """スレッドを塞ぐ待ち合わせ。name は `sem.wait`、`Thread.sleep` のような呼び出しの名前。"""

    name: str
    line: int


@dataclass(frozen=True)
class ContinueOn:
    """この手順より後の、同じ本体の手順が走る領域の切り替え。外の本体や枝へは効かない。

    reason は図の注記に出す、切り替わる理由の言語の語（`ConfigureAwait(false) の後の継続`）。
    """

    domain: Isolation
    reason: str
    line: int


ConcurrencyStep = (
    AwaitCall
    | SyncCall
    | MemberAssignment
    | StructuredConcurrency
    | UnstructuredTask
    | QueueDispatch
    | BlockingWait
    | ContinueOn
)


@dataclass(frozen=True)
class TypeSyntax:
    """型の宣言一つ（拡張を含む）の節点と、その節点を含むファイルのバイト列。"""

    node: Node
    source: bytes


class IsolationContext(Protocol):
    """隔離の解決に要る、宣言ノードのファイルの外の情報。

    別のファイルの拡張や、上位の型の宣言に由来する隔離を解くために、型名から宣言と上位の型を引く。
    同じ単純名の型が複数あるときは、どれを返すかは実装が決める。
    """

    def type_syntaxes(self, type_name: str, /) -> Sequence[TypeSyntax]:
        """型名の宣言と拡張を、宣言の順に返す。文脈に無い型は空。"""
        ...

    def supertype_names(self, type_name: str, /) -> Sequence[str]:
        """型の上位の型（継承と準拠）の単純名を返す。文脈に無い型は空。"""
        ...


@runtime_checkable
class ConcurrencyAdapter(SequenceAdapter, Protocol):
    """並行処理の図に隔離の領域と並行の手順を渡せる言語アダプタ。"""

    def isolation_of(
        self, declaration_node: Node, source: bytes, context: IsolationContext, /
    ) -> Isolation:
        """型や関数の宣言が属する領域を返す。注記が無いときの既定の領域も、この言語の規則で解く。

        別のファイルの拡張や上位の型から引き継ぐ隔離は、context で引いて解く。
        """
        ...

    def is_async(self, member_node: Node, /) -> bool:
        """メンバの宣言が async か。"""
        ...

    def concurrency_steps(
        self, body: Node, source: bytes, enclosing: Isolation, /
    ) -> list[ConcurrencyStep]:
        """本体の並行の手順を評価の順に返す。

        enclosing は本体を持つ関数の領域。新しい実行文脈の本体の領域は、
        この言語の継承の規則で解く。
        """
        ...

    def is_ui_type(
        self, type_name: str, context: IsolationContext, scope: Node | None = None, /
    ) -> bool:
        """型が UI の型か。型自身か、context で辿れる上位の型が、この言語の UI の型なら真。

        scope は type_name が書かれたファイルの節点（任意の節点でよい）。名前だけでは UI の型と
        決められない言語は、そのファイルの import で確かめる。
        None なら名前だけで決められるものに限る。

        UI の型は外部のフレームワークの型が多く、索引に宣言が無い。名前の規則もこの言語が持つ。
        """
        ...

    def is_main_thread(self, domain: Isolation, /) -> bool:
        """領域が UI を触ってよい主スレッドか。この言語の主スレッドのキューの綴りも含む。"""
        ...

    def sharing_of(self, type_name: str, context: IsolationContext, /) -> Sharing:
        """型の値を領域の境界の向こうへ渡したときの共有の性質。

        型の宣言（種類、Sendable に相当する適合、可変の項目）から確かめられるときだけ
        NOT_SENDABLE か MUTABLE を返す。型を解けない、適合を確かめられない、
        この言語に答えが無いときは UNKNOWN で、呼び側は札を出さない。
        """
        ...


@dataclass(frozen=True)
class Import:
    """import の文一つが取り込むモジュール。"""

    # モジュールの名前の原文。Python の相対の import は先頭の "." の連なりを残す
    module: str
    # 1 始まり
    line: int


@runtime_checkable
class MarkupBindingAdapter(LanguageAdapter, Protocol):
    """対になるマークアップのファイルがメソッドを呼ぶ言語アダプタ（XAML のイベント結線）。"""

    def markup_path(self, path: str, /) -> str | None:
        """ソースのパスに対になるマークアップのパス。対になるものが無い名前なら None。"""
        ...

    def markup_handlers(self, markup: str, /) -> frozenset[str]:
        """マークアップが呼ぶ名前の候補。メソッドかどうかはソース側と突き合わせて決める。"""
        ...


@runtime_checkable
class ImportAdapter(LanguageAdapter, Protocol):
    """import の文からモジュールの名前を取り出せる言語アダプタ（技術選定の口）。"""

    def imports(self, root_node: Node, source: bytes, /) -> list[Import]:
        """import の文が取り込むモジュールを、文書順に返す。一つの文が複数を取り込めば全て返す。"""
        ...

    def technology_of(self, module: str, /) -> str:
        """モジュールの名前を、技術の単位に束ねた名前にする。相対の import には呼ばない。"""
        ...


@dataclass(frozen=True)
class CommandArgument:
    """副コマンドの引数一つ。値の欄は、ソースの原文のまま取る。評価はしない。"""

    # 副コマンドの名前。最上位の parser の引数では空
    command: str
    # `--region`、`-r` のような名前の並び。位置引数では名前一つ
    names: tuple[str, ...]
    positional: bool
    # `dest=` の原文。書かれていなければ None
    dest: str | None
    # `default=` の原文。書かれていなければ None
    default: str | None
    # `required=` の原文。書かれていなければ None
    required_text: str | None
    # 必ず渡す引数か。`required=True` の引数と、nargs が `?`、`*` でない位置引数
    required: bool
    # `help=` の原文
    help: str | None
    action: str | None
    type: str | None
    choices: str | None
    path: str
    # 1 始まり
    line: int
    # `add_argument_group`、`add_mutually_exclusive_group` の変数の名前。群に属さなければ None
    group: str | None = None
    # 群が排他か（`add_mutually_exclusive_group`）
    exclusive: bool = False


@runtime_checkable
class CommandArgumentAdapter(LanguageAdapter, Protocol):
    """副コマンドの引数を静的に読める言語アダプタ。"""

    def command_arguments(
        self, root_node: Node, source: bytes, path: str, /
    ) -> list[CommandArgument]:
        """副コマンドの引数を文書順に返す。コマンドの定義が無い言語と、追えない形は返さない。"""
        ...


class FileUsage(StrEnum):
    """ファイルの名前の式が、ファイルを書くのか読むのか。"""

    WRITE = "write"
    READ = "read"
    # 読み書きの呼び出しに渡らず、`Path(...)` や `/` で組まれただけの式
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FilePathPattern:
    """ファイルの名前の組み立て一つ。式は原文のまま取り、評価しない。"""

    # 式を含む関数の修飾名。モジュール直下では空
    function: str
    # 組み立ての原文（f 文字列、`Path(...)`、`/` の式）。変数を一段辿れたときは代入の右辺の原文
    expression: str
    usage: FileUsage
    path: str
    # 1 始まり
    line: int


@runtime_checkable
class FilePathAdapter(LanguageAdapter, Protocol):
    """書き出す、または読むファイルの名前の組み立てを静的に読める言語アダプタ。"""

    def file_path_patterns(
        self, root_node: Node, source: bytes, path: str, /
    ) -> list[FilePathPattern]:
        """ファイルの名前の組み立てを文書順に返す。追えない形は返さない。"""
        ...


class StoreFormat(StrEnum):
    """保存物の形式。値は表に出す綴り。"""

    CSV = "CSV"
    JSON = "JSON"


class StoreRole(StrEnum):
    """保存物に対する関数の役割。"""

    WRITE = "write"
    READ = "read"


@dataclass(frozen=True)
class StoreSide:
    """保存物の書く側、または読む側の関数一つ。値は読まず、鍵の集合だけを持つ。"""

    role: StoreRole
    # 関数の修飾名（メソッドは `型.名前`）
    function: str
    path: str
    kind: StoreFormat
    keys: frozenset[str]
    # 読む側が作る型の名前。書く側は空
    types: frozenset[str]
    # 鍵の出どころの語彙の名前（列挙型、定数群）。ファイル名で束ねられない保存物を束ねる手がかり
    labels: frozenset[str]


@dataclass(frozen=True)
class StoreAccess:
    """一つの関数が保存物に触れる形。役割ごとに高々一つの側を持ち、一つも触れない関数は作れない。"""

    path: str
    # 関数が使う JSON、CSV、TSV のファイル名（定数を解いた文字列）
    names: frozenset[str]
    # 関数が to_json と from_json の組を成す型のメソッドなら、その型の名前
    pair_owner: str | None
    sides: tuple[StoreSide, ...]

    def __post_init__(self) -> None:
        roles = [side.role for side in self.sides]
        if not roles or len(set(roles)) != len(roles):
            raise ValueError(f"側の組は空でなく、役割の重複もない必要があります: {roles}")


@runtime_checkable
class StoreAccessReader(LanguageAdapter, Protocol):
    """保存物の書く側と読む側の鍵を、複数のファイルにまたがって静的に読める言語アダプタ。"""

    def store_accesses(self, sources: Mapping[str, str], /) -> list[StoreAccess]:
        """相対パスからソースへの対応を受け取り、関数ごとの保存物への触れ方を返す。

        定数や列挙型の解決と import の追跡は、渡した全ファイルの範囲で行う。
        """
        ...


class RegexDialect(StrEnum):
    """正規表現の方言。共通部分の構文解析は同じで、方言に固有の書き方の読み分けだけに使う。"""

    PYTHON = "python"
    JAVA = "java"
    DOTNET = "dotnet"
    SWIFT = "swift"


class RegexUse(StrEnum):
    """正規表現の書き場所の用途。アダプタが呼び出しごとに決める。"""

    MATCH = "match"
    REPLACE = "replace"
    SPLIT = "split"
    # 正規表現の値を作るだけ、または呼び出しから用途が分からないもの
    COMPILE = "compile"


@dataclass(frozen=True)
class RegexLiteral:
    """正規表現の書き場所一つ。正規表現は文字列の定数を解いた値で、言語の文字列の書き方は外してある。"""

    # 正規表現の本文。Python の raw 文字列や Swift の `#/.../#` の囲みは外し、
    # 言語のエスケープは解く
    pattern: str
    dialect: RegexDialect
    # 書き場所の呼び名（`re.sub`、`Pattern.compile`、`Regex`、`/.../`）。表示にだけ使う
    call: str
    use: RegexUse
    path: str
    # 1 始まり
    line: int


@dataclass(frozen=True)
class RegexComposed:
    """値が文字列の定数でない正規表現の書き場所。本文が分からないので数えない。"""

    call: str
    use: RegexUse
    path: str
    # 1 始まり
    line: int


@dataclass(frozen=True)
class RegexScan:
    """一つのファイルの正規表現の書き場所。どちらも文書順。"""

    literals: tuple[RegexLiteral, ...]
    composed: tuple[RegexComposed, ...]


@runtime_checkable
class RegexAdapter(LanguageAdapter, Protocol):
    """正規表現の書き場所を静的に読める言語アダプタ。"""

    def regex_literals(self, root_node: Node, source: bytes, path: str, /) -> RegexScan:
        """正規表現を受け取る呼び出しと正規表現の値を文書順に拾う。

        受け取る呼び出しの最初の引数が文字列の定数（定数どうしの連結を含む）なら literals に、
        それ以外の式なら composed に入れる。正規表現と関わらない文字列は拾わない。
        """
        ...


def import_nodes(adapter: LanguageAdapter, root: Node) -> list[Node]:
    """import の文の節点を文書順に返す。入れ子の文の中へは降りない。"""
    found: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in adapter.import_node_types:
            found.append(node)
        else:
            stack.extend(reversed(node.children))
    return found


def leading_components(module: str, count: int) -> str:
    """ドットで区切ったモジュールの名前の先頭 count 個。足りなければあるだけ。"""
    return ".".join(module.split(".")[:count])


def grammar_target(adapter: LanguageAdapter) -> GrammarTarget:
    """文法の層へ渡す照合の対象。外の言語の対象は、登録の層が包んだアダプタの属性に持つ。"""
    attached = getattr(adapter, "grammar_target", None)
    if isinstance(attached, BuiltinGrammar | DeclaredGrammar):
        return attached
    # 属性を持たないアダプタは組み込みで、組み込みの記録が照合の根拠になる
    return BuiltinGrammar(adapter.grammar)


def parse(adapter: LanguageAdapter, source: bytes) -> Tree:
    return get_parser(grammar_target(adapter)).parse(source)


def collect_patterns(adapters: Sequence[LanguageAdapter], attribute: str) -> tuple[str, ...]:
    return tuple(pattern for adapter in adapters for pattern in getattr(adapter, attribute))


def leaves_and_comments(
    adapter: LanguageAdapter, root: Node
) -> Iterator[tuple[Node, tuple[str, ...]]]:
    """葉とコメントの節点を、文書順に（節点, 祖先の型の列）で返す。コメントの中へは降りない。"""
    stack: list[tuple[Node, tuple[str, ...]]] = [(root, ())]
    while stack:
        node, ancestors = stack.pop()
        if node.child_count == 0 or node.type in adapter.comment_node_types:
            yield node, ancestors
        else:
            stack.extend((c, (*ancestors, node.type)) for c in reversed(node.children))


def comment_nodes(adapter: LanguageAdapter, root: Node) -> list[Node]:
    """構文木のコメントの節点を、文書順に返す。コメントの判定はアダプタだけが持つ。"""
    return [
        node
        for node, _ in leaves_and_comments(adapter, root)
        if node.type in adapter.comment_node_types
    ]


def import_rows(adapter: LanguageAdapter, root: Node) -> set[int]:
    """import の文が占める行の番号（1 始まり）。複数行の文は全行を含む。"""
    rows: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in adapter.import_node_types:
            rows.update(range(node.start_point.row + 1, node.end_point.row + 2))
        else:
            stack.extend(node.children)
    return rows
