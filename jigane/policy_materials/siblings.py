"""変更した型の兄弟の型（共通の基底と、基底を継承する別の型）を探す。

基底の宣言か、同名メソッドからの推定で基底を決める。型とメンバの宣言だけを読む。
"""

from dataclasses import dataclass

from jigane.sequence.index import TypeInfo, TypeKey
from jigane.sequence.indexes import Indexes

MAX_SIBLINGS = 3  # 一つの基底につき、図に出す兄弟の型の上限。基底そのものは数えない
MIN_BASE_CHILDREN = 2  # 同名メソッドから基底を推定するときの、変更した型を除く子の下限


@dataclass(frozen=True)
class Siblings:
    """一つの基底について、変更した型の兄弟として出す型と、上限を超えて出さなかった件数。"""

    base: TypeInfo
    siblings: tuple[TypeInfo, ...]
    omitted: int


def _method_names(info: TypeInfo) -> set[str]:
    return {
        name
        for name, group in info.members.items()
        if not name.startswith("__") and any(m.kind == "method" for m in group)
    }


def _children_by_base(indexes: Indexes) -> dict[TypeKey, list[TypeInfo]]:
    """基底の型の同一性から、それを継承する型への引き先。基底の名前は `named` の先頭で引く。"""
    children: dict[TypeKey, list[TypeInfo]] = {}
    for info in indexes.types():
        if info.kind == "module":
            continue
        for base in info.bases:
            for target in indexes.named(base)[:1]:
                group = children.setdefault(target.key, [])
                if info not in group:  # 同じ基底を二度宣言した型（`A(B, B)`）を重ねない
                    group.append(info)
    return children


def _inferred_base(
    indexes: Indexes,
    info: TypeInfo,
    changed_keys: set[TypeKey],
    owners: dict[str, list[TypeInfo]],
    children: dict[TypeKey, list[TypeInfo]],
) -> TypeInfo | None:
    """基底を宣言しない型の基底。同名メソッドを自前で宣言する型とその基底のうち、子が多いもの。"""
    peers: dict[TypeKey, TypeInfo] = {}
    for name in _method_names(info):
        for owner in owners.get(name, []):
            if owner.key not in changed_keys:
                peers[owner.key] = owner
    candidates = dict(peers)
    for peer in peers.values():
        for base in peer.bases:
            for target in indexes.named(base)[:1]:
                candidates.setdefault(target.key, target)

    def child_count(candidate: TypeInfo) -> int:
        return sum(1 for c in children.get(candidate.key, []) if c.key not in changed_keys)

    ranked = sorted(
        (c for c in candidates.values() if child_count(c) >= MIN_BASE_CHILDREN),
        key=lambda c: (-child_count(c), c.name),
    )
    return ranked[0] if ranked else None


def _by_shared_methods(infos: list[TypeInfo], names: set[str]) -> list[TypeInfo]:
    return sorted(infos, key=lambda c: (-len(_method_names(c) & names), c.path, c.line))


def siblings_of(
    indexes: Indexes, changed: list[TypeInfo], shown_keys: set[TypeKey]
) -> tuple[Siblings, ...]:
    """変更した型の基底ごとに、その基底を継承するほかの型を束ねる。

    宣言された基底は `named` の先頭、宣言の無い型は同名メソッドから推定した基底を使う。
    shown_keys の型はすでに図にあるので兄弟に数えない。兄弟が無い基底の束は作らない。
    """
    changed_keys = {t.key for t in changed}
    owners: dict[str, list[TypeInfo]] = {}
    for info in indexes.types():
        if info.kind != "module":
            for name in _method_names(info):
                owners.setdefault(name, []).append(info)
    children = _children_by_base(indexes)
    # 基底ごとに、基底を使う変更した型の同名メソッド名を集める
    groups: dict[TypeKey, tuple[TypeInfo, set[str]]] = {}
    for info in changed:
        if info.kind == "module":
            continue
        if info.bases:
            bases = [t for b in info.bases for t in indexes.named(b)[:1]]
        else:
            inferred = _inferred_base(indexes, info, changed_keys, owners, children)
            bases = [] if inferred is None else [inferred]
        for base in bases:
            _, names = groups.setdefault(base.key, (base, set()))
            names |= _method_names(info)
    result: list[Siblings] = []
    excluded = changed_keys | shown_keys
    for base, names in groups.values():
        others = [c for c in children.get(base.key, []) if c.key not in excluded]
        if others:
            ordered = _by_shared_methods(others, names)
            omitted = max(0, len(ordered) - MAX_SIBLINGS)
            result.append(Siblings(base, tuple(ordered[:MAX_SIBLINGS]), omitted))
    return tuple(result)


@dataclass(frozen=True)
class NotSearched:
    """兄弟を探さなかった（ファイルのレビュー）。探して無かったときは Searched で groups が空。"""


@dataclass(frozen=True)
class Searched:
    """兄弟を探した結果。基底ごとの束で、兄弟が無ければ空。"""

    groups: tuple[Siblings, ...]


SiblingSearch = Searched | NotSearched
