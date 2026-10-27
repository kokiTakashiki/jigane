"""別のファイルへ移しただけの宣言を見つける。方針の資料が、追加と削除の組を一つの「移した」に直すために使う。"""

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class PlacedDeclaration[T]:
    """片側の木の宣言一つ。item は呼び出し側が結果を引き戻すための持ち物で、規則は見ない。"""

    qualified_name: str
    kind: str
    path: str
    # シグネチャ（型なら外形）の原文。空白の違いは規則の側でそろえる
    shape: str
    item: T

    @property
    def name(self) -> str:
        return self.qualified_name.rpartition(".")[2]


@dataclass(frozen=True)
class MovedPair[T, U]:
    """移しただけの宣言の組。old は変更前の木の側、new は変更後の木の側。"""

    old: PlacedDeclaration[T]
    new: PlacedDeclaration[U]


def _normalized(shape: str) -> str:
    return " ".join(shape.split())


def find_moved[T, U](
    deleted: list[PlacedDeclaration[T]], added: list[PlacedDeclaration[U]]
) -> list[MovedPair[T, U]]:
    """変更前だけにある宣言と変更後だけにある宣言から、移しただけの組を返す。

    名前（修飾名の末尾）と種類が同じで、正規化したシグネチャが同じ、パスが違う組を移したとする。
    名前と種類が同じ候補がどちらかの側に複数あるときは、どれが移ったか決められないので組にしない。
    """
    old_by_key: defaultdict[tuple[str, str], list[PlacedDeclaration[T]]] = defaultdict(list)
    new_by_key: defaultdict[tuple[str, str], list[PlacedDeclaration[U]]] = defaultdict(list)
    for old in deleted:
        old_by_key[(old.name, old.kind)].append(old)
    for new in added:
        new_by_key[(new.name, new.kind)].append(new)
    pairs: list[MovedPair[T, U]] = []
    for key, olds in old_by_key.items():
        news = new_by_key.get(key, [])
        if len(olds) != 1 or len(news) != 1:
            continue
        old, new = olds[0], news[0]
        if old.path != new.path and _normalized(old.shape) == _normalized(new.shape):
            pairs.append(MovedPair(old, new))
    return pairs
