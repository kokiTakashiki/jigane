"""本体の呼び出しを評価の順に取り出し、受け手の型を 5.1 節の規則 1〜9 で解決する。"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from tree_sitter import Node

from jigane.languages.base import (
    Argument,
    CallParts,
    CallSite,
    Creation,
    DeferredSite,
    LocalVariable,
    Name,
    Paren,
    Self,
    Super,
)
from jigane.languages.base import (
    Call as CallKind,
)
from jigane.languages.base import (
    Member as MemberRef,
)
from jigane.preconditions.required import required
from jigane.sequence.index import (
    CompositeType,
    Index,
    Member,
    MemberKind,
    TypeInfo,
    TypeKey,
    components_of,
    first_component,
    text_of,
)

_CALLABLE_KINDS: tuple[MemberKind, ...] = ("method", "constructor")
_VALUE_KINDS: tuple[MemberKind, ...] = ("field", "property", "parameter")
_EXPRESSION_DEPTH = 12


@dataclass(frozen=True)
class Resolved:
    """受け手の型が索引にあった。member は型と基底の型から見つけた呼び先。無ければ None。"""

    type: TypeKey
    member: Member | None
    # 多重定義の候補の数。5.2 節の注記に使う
    candidates: int = 0


@dataclass(frozen=True)
class External:
    """受け手の型は決まったが、索引に無い（標準ライブラリや外部のパッケージ）。"""

    type: str


@dataclass(frozen=True)
class Unresolved:
    """受け手の型が決まらない。text は受け手の式の原文。"""

    text: str


Resolution = Resolved | External | Unresolved


@dataclass(frozen=True)
class Call:
    name: str
    resolution: Resolution
    path: str
    line: int
    # 評価の順を決める、呼び出しの式の終わりの位置
    end_byte: int
    argument_count: int
    is_creation: bool
    # 規則 9: 呼び先の本体を辿るときに、仮引数の名前から具象の型への対応
    bindings: tuple[tuple[str, TypeKey], ...] = ()


@dataclass(frozen=True)
class DeferredReference:
    """その場では実行されず、あとで呼ばれる参照。根の選び方（4.2.1 節）が使う。"""

    kind: Literal["lambda", "method_group"]
    path: str
    line: int
    text: str
    # lambda では本体の全ての呼び出しの解決（評価の順）、method_group では参照先の解決
    targets: tuple[Resolution, ...]


@dataclass(frozen=True)
class Extraction:
    calls: tuple[Call, ...]
    deferred: tuple[DeferredReference, ...]


# 型の決まり方。TypeInfo は索引の型、CompositeType はその合成、
# str は索引に無い型の単純名、None は決まらない
_Typed = TypeInfo | CompositeType | str | None


def extract_calls(
    index: Index, member: Member, bindings: Mapping[str, TypeKey] | None = None
) -> Extraction:
    """member の本体の呼び出しを評価の順に返す。

    bindings は規則 9 の対応（仮引数の名前から具象の型）で、呼び元の Call.bindings を渡す。
    """
    return _Extractor(index, member, bindings or {}).run()


def receiver_typer(index: Index, member: Member) -> Callable[[Node], TypeInfo | str | None]:
    """member の本体の式の型を、シーケンス図の呼び出しの解決と同じ規則で引く関数。"""
    return _Extractor(index, member, {}).type_of_receiver


def call_resolver(index: Index, member: Member) -> Callable[[CallSite], Call]:
    """member の本体の呼び出し一つを、シーケンス図の呼び出しの解決と同じ規則で解く関数。

    ラムダの中の呼び出しも解ける。extract_calls はラムダの中を DeferredReference にまとめる。
    """
    return _Extractor(index, member, {}).resolve_call


def _shape(arguments: tuple[Argument, ...]) -> tuple[int, tuple[str | None, ...]]:
    """lookup に渡す、引数の数とラベルの並び。"""
    return len(arguments), tuple(a.name for a in arguments)


class _Extractor:
    def __init__(self, index: Index, member: Member, bindings: Mapping[str, TypeKey]) -> None:
        self.index = index
        self.member = member
        self.path = member.path
        self.owner = required(index.types.get(member.owner))
        # 本体を持たない宣言（フィールドなど）は、宣言全体の呼び出しを対象にする
        self.body = index.adapter.body_of(member.node) or member.node
        self.source = required(index.trees[member.path].root_node.text)
        self.bound = {
            name: info
            for name, key in bindings.items()
            if (info := index.types.get(key)) is not None
        }
        self.imported = index.imported.get(member.path, {})
        self.params = {p.name: p.type_name for p in member.parameters}
        scanned = index.adapter.locals_of(self.body)
        self.locals: dict[str, LocalVariable] = {v.name: v for v in scanned.variables}
        self.local_functions: set[str] = set(scanned.functions)
        # 名前が何を指すか分からないもの（ラムダの引数）。同名のフィールドを隠す
        self.opaque: set[str] = set(scanned.lambda_parameters)
        self._resolving: set[int] = set()

    # 型の決定

    def _to_type(self, raw: str) -> TypeInfo | CompositeType | str:
        components = self.index.adapter.composition_type_names(raw)
        if components:
            return self._to_composition(components)
        name = self.index.adapter.simple_type_name(raw)
        return self.index.find_type(name, self.path) or name

    def _to_composition(self, names: list[str]) -> TypeInfo | CompositeType | str:
        """索引にある成分が複数なら CompositeType、一つならその型、無ければ最初の成分の名前。"""
        found = [t for n in names if (t := self.index.find_type(n, self.path)) is not None]
        if not found:
            return names[0]
        return found[0] if len(found) == 1 else CompositeType(tuple(found))

    def _typed_member(self, info: TypeInfo | CompositeType, name: str) -> _Typed:
        found, _, _ = self.index.lookup(info, name, _VALUE_KINDS)
        if found is None or found.type_name is None:
            return None
        return self._to_type(found.type_name)

    def type_of_receiver(self, expr: Node) -> TypeInfo | str | None:
        """式の型。TypeInfo は索引の型、str は索引に無い型の単純名、None は決まらない。"""
        typed = self._type_of(expr)
        # 外部の利用者は合成の型を知らない。最初の成分に畳む
        return first_component(typed) if isinstance(typed, CompositeType) else typed

    def _type_of(self, expr: Node, depth: int = 0) -> _Typed:
        if depth > _EXPRESSION_DEPTH or expr.id in self._resolving:
            return None
        self._resolving.add(expr.id)
        try:
            return self._type_of_expression(expr, depth + 1)
        finally:
            self._resolving.discard(expr.id)

    def _type_of_expression(self, expr: Node, depth: int) -> _Typed:
        kind = self.index.adapter.receiver_of(expr)
        if isinstance(kind, Paren):
            return self._type_of(kind.inner, depth)
        if isinstance(kind, (Self, Super)):
            return self._self_or_super_type(kind)
        if isinstance(kind, Name):
            return self._identifier(kind.text, depth)
        if isinstance(kind, MemberRef):
            return self._member_ref_type(kind, depth)
        if isinstance(kind, (CallKind, Creation)):
            return self._call_type(expr, depth)
        return None

    def _self_or_super_type(self, kind: Self | Super) -> _Typed:
        """self は所有する型、super は索引にある最初の基底（無ければ宣言どおりの最初の基底）。"""
        if isinstance(kind, Self):
            return self.owner
        bases = self.index.base_types(self.owner)
        return bases[0] if bases else (self.owner.bases[0] if self.owner.bases else None)

    def _member_ref_type(self, kind: MemberRef, depth: int) -> _Typed:
        name = self.index.adapter.simple_type_name(kind.name)
        base = self._type_of(kind.base, depth)
        if not isinstance(base, (TypeInfo, CompositeType)):
            # 外部の型のメンバの型は分からない。基底が決まらなければ、その先も決まらない
            return None
        member_type = self._typed_member(base, name)
        if member_type is not None:
            return member_type
        for component in components_of(base):
            nested = self.index.types.get(
                TypeKey(f"{component.key.qualified_name}.{name}", component.key.file)
            )
            if nested is not None:
                return nested
        return None

    def _identifier(self, name: str, depth: int) -> _Typed:
        if name in self.locals:
            return self._local_type(self.locals[name], depth)
        if name in self.bound:
            return self.bound[name]
        if name in self.params:
            return self._to_type(self.params[name])
        if name in self.opaque:
            return None
        return self._enclosing_name_type(name)

    def _local_type(self, local: LocalVariable, depth: int) -> _Typed:
        if local.type_annotation is not None:
            return self._to_type(local.type_annotation)
        init = local.initializer
        if init is not None and isinstance(
            self.index.adapter.receiver_of(init), (CallKind, Creation)
        ):
            return self._type_of(init, depth)
        return None

    def _enclosing_name_type(self, name: str) -> _Typed:
        """局所にも引数にも無い名前を、import、所有する型のメンバ、索引の型の順に引く。"""
        origin = self.imported.get(name)
        imported = None if origin is None else self.index.types.get(TypeKey(origin))
        if imported is not None:
            return imported
        member_type = self._typed_member(self.owner, name)
        if member_type is not None:
            return member_type
        found = self.index.find_type(name, self.path)
        if found is not None:
            return found
        # 規則 8: 上のどれにも当たらない大文字始まりの識別子は、索引に無い型の静的な呼び出し
        return name if self.index.adapter.is_external_type_name(name) else None

    def _constructed(self, kind: object) -> TypeInfo | None:
        """受け手の無い Foo() が生成を意味する言語で、Foo が索引の型なら、その型。

        module は型に数えない。
        """
        if not isinstance(kind, CallKind) or not self.index.adapter.constructs_by_call:
            return None
        name = kind.parts.name
        if kind.parts.receiver is not None or name is None:
            return None
        if name in self.locals or name in self.params or name in self.opaque:
            return None
        if name in self.local_functions:
            return None
        found = self.index.find_type(self.index.adapter.simple_type_name(name), self.path)
        return found if found is not None and found.kind != "module" else None

    def _call_type(self, call: Node, depth: int) -> _Typed:
        """規則 7 と、生成の式の型。呼び先の戻り値の型注記の型を返す。"""
        kind = self.index.adapter.receiver_of(call)
        if isinstance(kind, Creation):
            return self._to_type(kind.type_text) if kind.type_text is not None else None
        if not isinstance(kind, CallKind):
            return None
        constructed = self._constructed(kind)
        if constructed is not None:
            return constructed
        resolution = self._resolve_invocation(kind.parts, depth)
        if isinstance(resolution, Resolved) and resolution.member is not None:
            declared = resolution.member.type_name
            return self._to_type(declared) if declared is not None else None
        return None

    # 呼び出しの解決

    def _resolve_invocation(self, parts: CallParts, depth: int = 0) -> Resolution:
        if parts.name is None:
            return Unresolved(parts.text)
        name = self.index.adapter.simple_type_name(parts.name)
        receiver = parts.receiver
        if receiver is None:
            return self._resolve_bare(name, parts.arguments)
        typed = self._type_of(receiver, depth)
        if isinstance(typed, (TypeInfo, CompositeType)):
            member, candidates, owner = self.index.lookup(
                typed, name, _CALLABLE_KINDS, *_shape(parts.arguments)
            )
            return Resolved(owner.key, member, candidates)
        if isinstance(typed, str):
            return External(typed)
        return Unresolved(text_of(receiver))

    def _resolve_bare(self, name: str, arguments: tuple[Argument, ...]) -> Resolution:
        """受け手の無い呼び出し。局所関数、取り込んだ名前、含む型、同じモジュールの最上位の順に引く。"""
        shape = _shape(arguments)
        if name in self.local_functions:
            # 局所関数は型のメンバではない
            return Resolved(self.owner.key, None, 0)
        shadowed = name in self.locals or name in self.params or name in self.opaque
        origin = None if shadowed else self.imported.get(name)
        if origin is not None:
            module_name, _, member_name = origin.rpartition(".")
            module = self.index.types.get(TypeKey(module_name))
            if module is not None and module.kind == "module":
                member, candidates, _ = self.index.lookup(
                    module, member_name, _CALLABLE_KINDS, *shape
                )
                if member is not None:
                    return Resolved(module.key, member, candidates)
        member, candidates, _ = self.index.lookup(self.owner, name, _CALLABLE_KINDS, *shape)
        if member is not None:
            return Resolved(self.owner.key, member, candidates)
        outer = self._enclosing_member(name, shape)
        if outer is not None:
            return outer
        own_module = self.index.modules.get(self.path)
        top = None if own_module is None else self.index.types.get(TypeKey(own_module))
        if top is not None and top is not self.owner:
            member, candidates, _ = self.index.lookup(top, name, _CALLABLE_KINDS, *shape)
            if member is not None:
                return Resolved(top.key, member, candidates)
        # どこにも無い名前（組み込み関数など）は、従来どおり含む型の呼び出しとして残す
        return Resolved(self.owner.key, None, 0)

    def _enclosing_member(
        self, name: str, shape: tuple[int, tuple[str | None, ...]]
    ) -> Resolved | None:
        """無名クラスの中の受け手の無い呼び出しを、囲む型のメンバから探す。

        無名クラス自身と基底の型は、呼ぶ側が引いた後である。名前の頭を削って内側から順に探す。
        """
        if self.index.adapter.anonymous_class_display(self.owner.qualified_name) is None:
            # 無名クラスでない型は、囲む型のメンバを受け手なしでは呼べない言語がある
            return None
        prefix = self.owner.key.qualified_name
        while "." in prefix:
            prefix = prefix.rpartition(".")[0]
            enclosing = self.index.types.get(self.index.type_key(self.path, prefix))
            if enclosing is None:
                # 途中の名前がメソッドやフィールドのとき。型ではないので飛ばす
                continue
            member, candidates, _ = self.index.lookup(enclosing, name, _CALLABLE_KINDS, *shape)
            if member is not None:
                return Resolved(enclosing.key, member, candidates)
        return None

    def _resolve_creation(self, creation: Creation) -> Resolution:
        if creation.type_text is None:
            # new(...) は代入先の型で決まる。受け手の型の注記だけでは決められない
            return Unresolved(creation.text.split("(", 1)[0] + "(...)")
        typed = self._to_type(creation.type_text)
        if isinstance(typed, CompositeType):
            # 合成の型は生成できない。型注記の誤りとして畳む
            typed = first_component(typed)
        if isinstance(typed, TypeInfo):
            member, candidates, _ = self.index.lookup(
                typed, typed.name, ("constructor",), *_shape(creation.arguments)
            )
            return Resolved(typed.key, member, candidates)
        return External(typed)

    def _bindings(
        self, resolution: Resolution, arguments: tuple[Argument, ...]
    ) -> tuple[tuple[str, TypeKey], ...]:
        """規則 9: インターフェース型の仮引数に渡した new T(...) の、仮引数名から T への対応。"""
        if not isinstance(resolution, Resolved) or resolution.member is None or not arguments:
            return ()
        positional = self.index.adapter.positional_argument_labels
        found: list[tuple[str, TypeKey]] = []
        for parameter, argument in resolution.member.bind(arguments, positional_labels=positional):
            expression = argument.expression
            if not isinstance(self.index.adapter.receiver_of(expression), Creation):
                continue
            concrete = self._call_type(expression, 0)
            declared = self._to_type(parameter.type_name)
            if (
                isinstance(declared, TypeInfo)
                and declared.kind == "interface"
                and isinstance(concrete, TypeInfo)
            ):
                found.append((parameter.name, concrete.key))
        return tuple(found)

    # 抽出

    def resolve_call(self, site: CallSite) -> Call:
        kind = self.index.adapter.receiver_of(site.node)
        constructed = self._constructed(kind)
        if constructed is not None:
            member, candidates, _ = self.index.lookup(
                constructed, constructed.name, ("constructor",), *_shape(site.arguments)
            )
            resolution: Resolution = Resolved(constructed.key, member, candidates)
        elif isinstance(kind, Creation):
            resolution = self._resolve_creation(kind)
        elif isinstance(kind, CallKind):
            resolution = self._resolve_invocation(kind.parts)
        else:
            resolution = Unresolved(text_of(site.node))
        return Call(
            self.index.adapter.simple_type_name(site.name),
            resolution,
            self.path,
            site.line,
            site.end,
            len(site.arguments),
            site.is_creation or constructed is not None,
            self._bindings(resolution, site.arguments),
        )

    def run(self) -> Extraction:
        sites = self.index.adapter.call_sites(self.body, self.source)
        calls = tuple(self.resolve_call(s) for s in sites if isinstance(s, CallSite))
        deferred = tuple(self._deferred(s) for s in sites if isinstance(s, DeferredSite))
        return Extraction(calls, tuple(d for d in deferred if d is not None))

    def _deferred(self, site: DeferredSite) -> DeferredReference | None:
        node = site.node
        line = node.start_point.row + 1
        if site.kind == "lambda":
            targets = tuple(self.resolve_call(c).resolution for c in site.calls)
            return DeferredReference(
                "lambda", self.path, line, text_of(node).split("\n", 1)[0].strip(), targets
            )
        resolution = self._method_group(site)
        if resolution is None:
            return None
        return DeferredReference("method_group", self.path, line, text_of(node), (resolution,))

    def _method_group(self, site: DeferredSite) -> Resolution | None:
        if site.receiver is None:
            name = site.name
            # 変数や引数や値のメンバの名前はメソッド群ではない
            if (
                name in self.locals
                or name in self.params
                or name in self.opaque
                or self.index.lookup(self.owner, name, _VALUE_KINDS)[0] is not None
            ):
                return None
            member, candidates, _ = self.index.lookup(self.owner, name, _CALLABLE_KINDS)
            if member is None and name not in self.local_functions:
                return None
            return Resolved(self.owner.key, member, candidates)
        typed = self._type_of(site.receiver)
        name = self.index.adapter.simple_type_name(site.name)
        if not isinstance(typed, (TypeInfo, CompositeType)):
            return None
        member, candidates, owner = self.index.lookup(typed, name, _CALLABLE_KINDS)
        if member is None:
            return None
        return Resolved(owner.key, member, candidates)
