"""ツリーの中の一つの言語の全宣言を、型とメンバの名前で引ける索引にする。現在は C# だけを扱う。"""

from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from tree_sitter import Node, Tree

from jigane.git.tree_files import read_tree_bytes
from jigane.languages.base import (
    Argument,
    Declaration,
    MarkupBindingAdapter,
    MemberDecl,
    ParameterDecl,
    SequenceAdapter,
    TypeDecl,
    TypeKind,
    TypeScope,
    TypeSyntax,
    parse,
)
from jigane.languages.base import (
    MemberKind as MemberKind,  # noqa: PLC0414 他のモジュールが jigane.sequence.index から再エクスポートとして取り込む
)
from jigane.preconditions.required import required

_BASE_DEPTH = 8


def text_of(node: Node) -> str:
    return required(node.text).decode(errors="replace")


@dataclass(frozen=True)
class CompositeType:
    """Swift の合成の型 `A & B` のうち、索引にある成分。呼び先は成分を順に探す。"""

    components: tuple["TypeInfo", ...]


def components_of(typed: "TypeInfo | CompositeType") -> tuple["TypeInfo", ...]:
    return typed.components if isinstance(typed, CompositeType) else (typed,)


def first_component(typed: "TypeInfo | CompositeType") -> "TypeInfo":
    """合成の型を単一の型として扱う箇所（生成、外部の利用者）が使う唯一の畳み方。最初の成分を採る。"""
    return components_of(typed)[0]


@dataclass(frozen=True)
class Parameter:
    name: str
    type_name: str
    optional: bool
    # 呼び出しで引数に付けるラベル。外部名を持たない言語では仮引数の名前、外部名が `_` なら None
    label: str | None = None


@dataclass(frozen=True)
class TypeKey:
    """索引の中の型の同一性。file を持つのはファイルに閉じる型だけで、同名の型を区別する。"""

    qualified_name: str
    file: str | None = None


@dataclass(frozen=True)
class Member:
    qualified_name: str
    name: str
    kind: MemberKind
    # 所有する型
    owner: TypeKey
    path: str
    # 宣言の先頭行。1 始まり
    line: int
    # フィールドとプロパティの型注記、メソッドの戻り値の型の原文。無ければ None
    type_name: str | None
    parameters: tuple[Parameter, ...]
    declaration: Declaration | None
    node: Node = field(compare=False, repr=False)

    def accepts(self, argument_count: int) -> bool:
        required_count = sum(1 for p in self.parameters if not p.optional)
        return required_count <= argument_count <= len(self.parameters)

    def bind_positions(
        self, argument_names: Sequence[str | None], *, positional_labels: bool
    ) -> list[tuple[Parameter, int]]:
        """引数（名前の並び）を、対応する仮引数と引数の位置の組にする。対応の取れない引数は含めない。

        ラベルを位置に結び付ける言語（Swift）は、同じ位置の仮引数を対応させ、ラベルの付いた引数は
        仮引数のラベルが一致するときだけ対応させる。そうでない言語（C#、Python）は、名前付きの引数を
        仮引数の名前で、残りを位置で対応させる。
        """
        bound: list[tuple[Parameter, int]] = []
        for position, name in enumerate(argument_names):
            by_position = self.parameters[position] if position < len(self.parameters) else None
            if name is None:
                parameter = by_position
            elif positional_labels:
                parameter = by_position if by_position and by_position.label == name else None
            else:
                parameter = next((p for p in self.parameters if p.name == name), None)
            if parameter is not None:
                bound.append((parameter, position))
        return bound

    def bind(
        self, arguments: Sequence[Argument], *, positional_labels: bool
    ) -> list[tuple[Parameter, Argument]]:
        """引数と対応する仮引数の組。bind_positions の引数の位置を引数に引き直したもの。"""
        pairs = self.bind_positions(
            [a.name for a in arguments], positional_labels=positional_labels
        )
        return [(parameter, arguments[position]) for parameter, position in pairs]

    def labels_match(self, argument_labels: Sequence[str | None]) -> bool:
        """ラベルを付けた引数が、すべて仮引数と対応するか。ラベルの無い引数は問わない。"""
        bound = {
            position
            for _, position in self.bind_positions(argument_labels, positional_labels=True)
        }
        return all(
            label is None or position in bound for position, label in enumerate(argument_labels)
        )


@dataclass(frozen=True)
class TypeInfo:
    key: TypeKey
    # 図に出る名前。ファイルに閉じる型はファイルの印を挿す。索引の鍵は key
    qualified_name: str
    name: str
    kind: TypeKind
    path: str
    # 基底の型とインターフェースの単純名。宣言の順
    bases: tuple[str, ...]
    members: dict[str, tuple[Member, ...]] = field(compare=False, repr=False)
    # partial で複数のファイルに分かれた宣言の、すべてのパス。単一の宣言なら path だけ
    paths: tuple[str, ...] = ()
    # 宣言の先頭行。1 始まり。partial で分かれた型は最初の宣言の行
    line: int = 0
    # 型の宣言と拡張ごとの (パス, 節点)。宣言の順
    syntaxes: tuple[tuple[str, Node], ...] = field(default=(), compare=False, repr=False)


@dataclass
class Index:
    adapter: SequenceAdapter
    types: dict[TypeKey, TypeInfo] = field(default_factory=dict)
    by_name: dict[str, list[TypeInfo]] = field(default_factory=lambda: defaultdict(list))
    # (path, 宣言の先頭行) から、メソッドとコンストラクタ
    callables: dict[tuple[str, int], Member] = field(default_factory=dict)
    trees: dict[str, Tree] = field(default_factory=dict)
    # 読まなかったパス（シンボリックリンク、木の外へ出る道、無いファイル）
    skipped: list[str] = field(default_factory=list)
    # パスから、そのファイルのモジュール型の修飾名。宣言の記録はこの接頭辞を持たない
    modules: dict[str, str] = field(default_factory=dict)
    # パスから、そのファイルの import が束縛する名前と取り込んだ元の修飾名
    imported: dict[str, dict[str, str]] = field(default_factory=dict)
    # パスから、対になるマークアップが呼ぶ名前の候補（XAML のイベント結線）
    markup_called: dict[str, frozenset[str]] = field(default_factory=dict)
    # パスから、そのファイルに閉じる最上位の型の宣言上の修飾名。TypeKey を作る手順の内部の状態
    _file_scoped: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)
    # パスから、図に出る名前に挿すファイルの印。TypeKey から名前を作る手順の内部の状態
    _file_marks: dict[str, str] = field(default_factory=dict, repr=False)

    def type_key(self, path: str, qualified: str) -> TypeKey:
        """path の宣言上の修飾名（モジュールの接頭辞を含む）が指す型の同一性。"""
        for prefix in self._file_scoped.get(path, ()):
            if qualified == prefix or qualified.startswith(prefix + "."):
                return TypeKey(qualified, path)
        # ファイルに閉じる型の中でも外でもない名前は、どのファイルでも同じ型
        return TypeKey(qualified)

    def type_name(self, key: TypeKey) -> str:
        """型の同一性から、図の名前を作る。閉じる型はファイル名を挿す。"""
        qualified = key.qualified_name
        if key.file is not None:
            prefix = next(
                p
                for p in self._file_scoped[key.file]
                if qualified == p or qualified.startswith(p + ".")
            )
            qualified = prefix + self._file_marks[key.file] + qualified[len(prefix) :]
        return qualified

    def register_file_types(self, path: str, decls: Sequence[TypeDecl | MemberDecl]) -> None:
        """そのファイルでしか名前が通らない最上位の型を、ファイルごとに別の型にする準備をする。

        ファイルに閉じるかどうかは、言語アダプタが TypeDecl.scope に載せる。同じ名前の型が別の
        ファイルにあっても併合されないよう、図に出る名前にファイルの名前を挿す。同じファイルの拡張は同じ型のまま。
        """
        types = [d for d in decls if isinstance(d, TypeDecl)]
        names = {t.qualified_name for t in types}
        closed = frozenset(
            t.qualified_name
            for t in types
            if t.scope is TypeScope.FILE
            # 型の中の型は、外側の型の修飾名がすでに区別する
            and not any(t.qualified_name.startswith(other + ".") for other in names)
        )
        if not closed:
            # ファイルに閉じる型の無いファイルは、修飾名を変えない
            return
        mark = f"（{Path(path).name}）"
        if mark in self._file_marks.values():
            # 別のディレクトリの同じ名前のファイルと印が重なるときは、パスで区別する
            mark = f"（{path}）"
        self._file_scoped[path] = closed
        self._file_marks[path] = mark

    def declared_type(self, path: str, name: str) -> TypeKey:
        """宣言の記録のファイル相対の型名から、索引の型の同一性を作る。モジュールを持たない言語ではそのまま。"""
        module = self.modules.get(path)
        qualified = name if module is None else f"{module}.{name}"
        return self.type_key(path, qualified)

    def type_of_declaration(self, path: str, name: str) -> TypeInfo | None:
        """宣言の記録の名前とパスから、索引の型を引く。ファイルに閉じる型も、パスで区別できる。"""
        return self.types.get(self.declared_type(path, name))

    def declaration_name(self, member: Member) -> str:
        """メンバの、宣言の記録（declarations）での修飾名。"""
        shown_owner = self.type_name(member.owner)
        qualified = member.owner.qualified_name + member.qualified_name[len(shown_owner) :]
        module = self.modules.get(member.path)
        if module is None:
            return qualified
        # 最上位の関数の owner はモジュール自身で、クラスの owner は「モジュール.クラス」
        return qualified.removeprefix(module).removeprefix(".")

    def find_type(self, name: str, prefer_path: str) -> TypeInfo | None:
        """同じ単純名の型が複数あれば、同じファイルのものを、無ければ最初に索引へ入ったものを採る。"""
        candidates = self.by_name.get(name)
        if not candidates:
            return None
        return next((t for t in candidates if prefer_path in t.paths), candidates[0])

    def type_syntaxes(self, type_name: str) -> list[TypeSyntax]:
        """IsolationContext の口。同じ単純名の型が複数あれば、最初に索引へ入ったものを採る。"""
        info = self.find_type(type_name, "")
        if info is None:
            return []
        return [
            TypeSyntax(node, self.trees[path].root_node.text or b"")
            for path, node in info.syntaxes
        ]

    def supertype_names(self, type_name: str) -> list[str]:
        """IsolationContext の口。型の基底の型とインターフェースの単純名。"""
        info = self.find_type(type_name, "")
        return [] if info is None else list(info.bases)

    def base_types(self, info: TypeInfo) -> list[TypeInfo]:
        found = (self.find_type(name, info.path) for name in info.bases)
        return [t for t in found if t is not None]

    def hierarchy(self, info: TypeInfo) -> Iterator[TypeInfo]:
        """info、その基底の型の順に、幅優先で重複なく返す。"""
        seen = {info.key}
        queue = [info]
        for _ in range(_BASE_DEPTH):
            next_queue: list[TypeInfo] = []
            for current in queue:
                yield current
                for base in self.base_types(current):
                    if base.key not in seen:
                        seen.add(base.key)
                        next_queue.append(base)
            queue = next_queue
            if not queue:
                return

    def lookup(
        self,
        typed: "TypeInfo | CompositeType",
        name: str,
        kinds: Iterable[MemberKind],
        argument_count: int | None = None,
        argument_labels: Sequence[str | None] | None = None,
    ) -> tuple[Member | None, int, TypeInfo]:
        """呼び先を探す唯一の入口。(採ったメンバ, 候補の数, 探した成分の型)。

        合成の型は成分を順に探し、単一の型はその型を探す。
        返す型は、見つかった成分。見つからなければ最初の成分。
        """
        wanted = tuple(kinds)
        first: tuple[Member | None, int, TypeInfo] | None = None
        for component in components_of(typed):
            member, candidates = self._lookup_in(
                component, name, wanted, argument_count, argument_labels
            )
            if member is not None:
                return member, candidates, component
            first = first or (member, candidates, component)
        return required(first)

    def _lookup_in(
        self,
        info: "TypeInfo",
        name: str,
        kinds: Iterable[MemberKind],
        argument_count: int | None = None,
        argument_labels: Sequence[str | None] | None = None,
    ) -> tuple[Member | None, int]:
        """型と基底の型から、名前の一致する最初の型のメンバを引く。(採ったメンバ, 候補の数)。

        多重定義は、引数の数に合うものを先に絞り、候補が二つ以上残るときは引数のラベルで絞り、
        残りの先頭（宣言の順）を採る。ラベルで絞るのは、言語アダプタが引数のラベルを位置に結び付ける
        （positional_argument_labels）ときだけ。どれも合わなければ、絞らずに全候補から先頭を採る。
        """
        wanted = tuple(kinds)
        for current in self.hierarchy(info):
            candidates = [m for m in current.members.get(name, ()) if m.kind in wanted]
            if not candidates:
                continue
            if argument_count is not None:
                fitting = [m for m in candidates if m.accepts(argument_count)]
                candidates = fitting or candidates
            if (
                argument_labels is not None
                and self.adapter.positional_argument_labels
                and len(candidates) > 1
            ):
                candidates = [
                    m for m in candidates if m.labels_match(argument_labels)
                ] or candidates
            return candidates[0], len(candidates)
        return None, 0


def build_index(root: Path, paths: Iterable[str], adapter: SequenceAdapter) -> Index:
    """paths のうち adapter の拡張子のファイルを、root からの相対パスの順に読んで索引にする。"""
    index = Index(adapter)
    for path in sorted(paths):
        if Path(path).suffix not in adapter.suffixes:
            continue
        source = read_tree_bytes(root, path)
        if source is None:
            index.skipped.append(path)
            continue
        tree = parse(adapter, source)
        index.trees[path] = tree
        index.imported[path] = adapter.imported_names(tree.root_node, source, path)
        if isinstance(adapter, MarkupBindingAdapter):
            markup = _read_markup(root, adapter.markup_path(path))
            if markup is not None:
                index.markup_called[path] = adapter.markup_handlers(markup)
        declarations = {
            (d.qualified_name, d.span.start_line): d
            for d in adapter.declarations(tree.root_node, source)
        }
        decls = adapter.type_members(tree.root_node, source, path)
        index.register_file_types(path, decls)
        for decl in decls:
            if isinstance(decl, TypeDecl):
                key = index.type_key(path, decl.qualified_name)
                _add_type(index, replace(decl, qualified_name=index.type_name(key)), key)
            else:
                _add_member(index, decl, path, declarations, source)
    return index


def _read_markup(root: Path, markup_path: str | None) -> str | None:
    if markup_path is None:
        return None
    data = read_tree_bytes(root, markup_path)
    return None if data is None else data.decode("utf-8", errors="replace")


def _add_type(index: Index, decl: TypeDecl, key: TypeKey) -> None:
    if decl.kind == "module":
        index.modules[decl.path] = key.qualified_name
    bases = tuple(index.adapter.simple_type_name(b) for b in decl.bases)
    info = TypeInfo(
        key,
        decl.qualified_name,
        decl.name,
        decl.kind,
        decl.path,
        bases,
        {},
        (decl.path,),
        decl.line,
        ((decl.path, decl.node),),
    )
    previous = index.types.get(key)
    if previous is None:
        index.by_name[decl.name].append(info)
    else:
        # 同じ型の複数の宣言。メンバーは同じ辞書へ足し、基底とパスを併合した型で置き換える
        info = replace(
            previous,
            bases=previous.bases + tuple(b for b in info.bases if b not in previous.bases),
            paths=previous.paths + (() if decl.path in previous.paths else (decl.path,)),
            syntaxes=previous.syntaxes + info.syntaxes,
        )
        siblings = index.by_name[decl.name]
        siblings[siblings.index(previous)] = info
    index.types[key] = info


def _add_member(
    index: Index,
    decl: MemberDecl,
    path: str,
    declarations: dict[tuple[str, int], Declaration],
    source: bytes,
) -> None:
    shown = index.adapter.constructor_display_name if decl.kind == "constructor" else None
    # 宣言の記録の名前は、ファイルに閉じる型の名前を作る前の decl.owner で引く
    owner = index.type_key(path, decl.owner)
    qualified = f"{index.type_name(owner)}.{shown or decl.name}"
    member = Member(
        qualified,
        decl.name,
        decl.kind,
        owner,
        path,
        decl.line,
        decl.type_annotation,
        tuple(
            Parameter(p.name, p.type_annotation, p.optional, _label_of(p)) for p in decl.parameters
        ),
        # プライマリコンストラクタの引数に、宣言の記録は無い
        None
        if decl.kind == "parameter"
        else declarations.get((_declaration_name(index, decl, path), decl.line)),
        decl.node,
    )
    members = index.types[owner].members
    members[decl.name] = (*members.get(decl.name, ()), member)
    # 本体に呼び出しを持つ計算プロパティ（Swift の body など）だけを入口と呼び手にする。
    # 保存されるだけのプロパティ、本体を持たない C# の自動実装プロパティ、
    # 属性の読み出しや文字列の組み立てだけの計算プロパティは、図に矢印が出ないので入れない
    has_computed_body = (
        decl.kind == "property"
        and decl.body is not None
        and bool(index.adapter.call_sites(decl.body, source))
    )
    if decl.kind in ("method", "constructor") or has_computed_body:
        index.callables[(path, decl.line)] = member


def _label_of(parameter: ParameterDecl) -> str | None:
    if parameter.external_name is None:
        return parameter.name
    return None if parameter.external_name == "_" else parameter.external_name


def _declaration_name(index: Index, decl: MemberDecl, path: str) -> str:
    """declarations() の修飾名。モジュールの型を持つ言語では、記録がファイルの中の相対名で付く。"""
    name = decl.selector or decl.name
    module = index.modules.get(path)
    if module is None:
        return f"{decl.owner}.{name}"
    # 最上位の関数の owner はモジュール自身で、クラスの owner は「モジュール.クラス」
    relative = decl.owner.removeprefix(module).removeprefix(".")
    return f"{relative}.{name}" if relative else name
