"""足した公開の名前と呼ぶ側の抽出のうち、言語に依らない部分。

言語ごとの部品（`python_public_names`、`swift_public_names`、`java_public_names`、
`csharp_public_names`）は、構文木を歩いて `Tree` に定義と呼ぶ側を記録する関数だけを持つ。
ここは、記録された二つの木を比べて足した名前を選ぶ側と、定義ごとに呼ぶ側を数える側を分けて持つ。
定義の索引のキーは部品が決める（既定は種類と修飾名）。同じキーの定義は一つに合わせる。
構文木の型は部品が決める（Swift、Java、C# は tree-sitter の節点、Python は `ast`）。
足した引数のような言語固有の行は、部品が規則の結果（`AddedParameter`）だけを返し、
`PublicName` はここで組む。
"""

from collections.abc import Callable, Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Self

from jigane.languages.base import PublicName, PublicNameKind

type ParseSource[Root] = Callable[[str], Root]
type Place = tuple[str, int]


@dataclass
class Definition:
    kind: PublicNameKind
    qualified: str
    # (パス, 行)。合わせた定義の出現
    places: list[Place]
    is_public: bool
    is_constructor: bool = False

    def merge(self, other: Self) -> None:
        self.places += other.places
        self.is_public = self.is_public or other.is_public
        self.is_constructor = self.is_constructor or other.is_constructor


@dataclass(frozen=True)
class Site:
    path: str
    where: str
    name: str
    receiver: str | None = None


type DefinitionKey = Callable[[Definition], Hashable]


def _kind_and_qualified(definition: Definition) -> Hashable:
    return (definition.kind, definition.qualified)


# 一つのファイルの構文木の根を歩いて、パスと木に記録する関数
type Walk[Root] = Callable[[str, "Tree", Root], None]
type SiteCounter = Callable[[Definition, Tree], list[Site]]


@dataclass(frozen=True)
class AddedParameter:
    """足した引数一つ分の規則の結果。`PublicName` はここから core が組む。"""

    # 引数の名前。行の名前は定義の修飾名にこの名前を付けて組む
    name: str
    # その引数を実際に渡す呼ぶ側
    sites: tuple[Site, ...]


# 前からある定義（なければ None）、後の定義、前と後の木を受けて、定義に足した引数を返す。
# 変えたファイルに出現のある後の定義ごとに呼ぶ。公開かどうかの判断も部品が持つ
type AddedParameters = Callable[[Definition | None, Definition, Tree, Tree], list[AddedParameter]]


@dataclass(frozen=True)
class Rules[Root]:
    """言語ごとの差し込み。構文木を作る関数、歩き方、呼ぶ側を数える表、定義の索引のキー、足した引数の規則。"""

    parse_source: ParseSource[Root]
    walk: Walk[Root]
    count_sites: SiteCounter
    key_of: DefinitionKey = _kind_and_qualified
    # 並びの順に、後の定義を一巡ずつ呼ぶ。引数を足した名前に数えない言語は渡さない
    added_parameters: tuple[AddedParameters, ...] = ()


@dataclass
class Tree:
    # 歩き方が見つけた定義の並び。索引（`definitions`）には `parse_all` が `define` で入れる
    walked: list[Definition] = field(default_factory=list)
    definitions: dict[Hashable, Definition] = field(default_factory=dict)
    calls: list[Site] = field(default_factory=list)
    creations: list[Site] = field(default_factory=list)
    references: list[Site] = field(default_factory=list)
    inherits: list[Site] = field(default_factory=list)
    # 型の名前の出現。呼ぶ側を数える表は行ごとに引くので、名前で索引にする
    names: dict[str, list[Site]] = field(default_factory=dict)

    def add(self, definition: Definition) -> None:
        self.walked.append(definition)

    def define(self, definition: Definition, key_of: DefinitionKey) -> None:
        key = key_of(definition)
        existing = self.definitions.get(key)
        if existing is None:
            self.definitions[key] = definition
        else:
            existing.merge(definition)

    def record_name(self, site: Site) -> None:
        self.names.setdefault(site.name, []).append(site)

    def absorb(self, other: "Tree") -> None:
        self.calls += other.calls
        self.creations += other.creations
        self.references += other.references
        self.inherits += other.inherits
        for name, sites in other.names.items():
            self.names.setdefault(name, []).extend(sites)


def parse_all[Root](sources: Mapping[str, str], rules: Rules[Root]) -> Tree:
    tree = Tree()
    for path, source in sources.items():
        try:
            local = Tree()
            rules.walk(path, local, rules.parse_source(source))
        except (SyntaxError, RecursionError, MemoryError, ValueError):
            # 構文木を作れない、または深く入れ子で辿れないファイルは捨てる。
            # 他のファイルの名前で表は成り立つので止めない
            continue
        for definition in local.walked:
            tree.define(definition, rules.key_of)
        tree.absorb(local)
    return tree


def inside(site: Site, definition: Definition) -> bool:
    return any(site.path == path for path, _ in definition.places) and (
        site.where == definition.qualified or site.where.startswith(definition.qualified + ".")
    )


def sites_of(
    definition: Definition,
    tree: Tree,
    *,
    constructor_sites: Callable[[Tree], list[Site]],
    type_sites: Callable[[Tree, str], list[Site]],
) -> list[Site]:
    """定義を使う側。定義の中の箇所は除く。

    コンストラクタを呼ぶ側と、型の名前の出現（型の名前を受けて、並べる順も決める）は
    言語ごとに表が違うので、引数で受ける。メソッドは関数と同じく呼び出しの全てと照合する。
    """
    parts = definition.qualified.split(".")
    last = parts[-1]
    match definition.kind:
        case PublicNameKind.METHOD if definition.is_constructor:
            sites = [s for s in constructor_sites(tree) if s.name == parts[-2]]
        case PublicNameKind.FUNCTION | PublicNameKind.METHOD:
            sites = [s for s in tree.calls if s.name == last]
        case PublicNameKind.CLASS:
            sites = [s for s in type_sites(tree, last) if s.name == last]
        case _:
            sites = [s for s in tree.references if s.receiver == parts[-2] and s.name == last]
    return [s for s in sites if not inside(s, definition)]


def callers_of(sites: Sequence[Site]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(f"{s.path} の {s.where}" for s in sites))


def place_in(definition: Definition, changed: Collection[str]) -> Place | None:
    """変えたファイルにある最初の出現。変えていないファイルだけの定義は None。"""
    return next((p for p in definition.places if p[0] in changed), None)


def public_name(
    qualified: str, kind: PublicNameKind, place: Place, sites: Sequence[Site]
) -> PublicName:
    return PublicName(qualified, kind, place[0], place[1], callers_of(sites))


def added_definitions(
    old: Tree, new: Tree, changed: Collection[str]
) -> list[tuple[Definition, Place]]:
    """後の木にだけある公開の定義と、変えたファイルにある出現を、定義の出現順に返す。"""
    found = []
    for key, definition in new.definitions.items():
        if key in old.definitions or not definition.is_public:
            continue
        place = place_in(definition, changed)
        if place is None:
            # 変えていないファイルだけにある名前は、この変更で足した名前ではない
            continue
        found.append((definition, place))
    return found


def added_public_names[Root](
    before: Mapping[str, str],
    after: Mapping[str, str],
    changed: Collection[str],
    rules: Rules[Root],
) -> list[PublicName]:
    """足した名前、`rules.added_parameters` の並びごとに足した引数の順。

    `added_parameters` の各規則は、変えたファイルに出現がある後の定義ごとに呼ぶ。
    """
    old = parse_all(before, rules)
    new = parse_all(after, rules)
    found = [
        public_name(
            definition.qualified, definition.kind, place, rules.count_sites(definition, new)
        )
        for definition, place in added_definitions(old, new, changed)
    ]
    for added_parameters in rules.added_parameters:
        for key, definition in new.definitions.items():
            place = place_in(definition, changed)
            if place is None:
                # 変えていないファイルだけにある定義は、この変更で足した引数を持たない
                continue
            found.extend(
                public_name(
                    f"{definition.qualified}.{parameter.name}",
                    PublicNameKind.PARAMETER,
                    place,
                    parameter.sites,
                )
                for parameter in added_parameters(old.definitions.get(key), definition, old, new)
            )
    return found
