"""箇所を、役割と持ち主を付けた宣言の並びへ展開する。方針の資料の二つの表が共有する。"""

from dataclasses import dataclass
from typing import Literal

from jigane.judgment_map.region_table import Region
from jigane.languages import registry
from jigane.languages.base import Declaration, parse

Role = Literal["row", "member", "constant"]


@dataclass(frozen=True)
class ScopedDeclaration:
    name: str
    # 名前のある箇所で、その名前の宣言を木から見つけられなかったときは None
    declaration: Declaration | None
    # member のとき、持ち主の修飾名。それ以外は空
    owner: str
    role: Role


def _owner_of(qualified_name: str) -> str:
    return qualified_name.rpartition(".")[0]


def _scoped(declaration: Declaration) -> ScopedDeclaration:
    name = declaration.qualified_name
    if declaration.kind != "variable":
        return ScopedDeclaration(name, declaration, "", "row")
    # 持ち主は範囲の包含ではなく修飾名で決める。Swift の extension の中の要素は宣言にならず、
    # 拡張した型の名前の下に置かれるので、型の行が範囲に無くても持ち主の名前は残る
    owner = _owner_of(name)
    if owner:
        return ScopedDeclaration(name, declaration, owner, "member")
    return ScopedDeclaration(name, declaration, "", "constant")


def region_declarations(region: Region, data: bytes | None) -> list[ScopedDeclaration]:
    """箇所に対応する宣言を、役割と持ち主を付けて返す。data は箇所のファイルの本文。

    名前のある箇所は変更のあった宣言なので、変数でも row の一件にする。木から見つからなくても
    名前だけの一件を返す。名前の無い箇所（機能レビューのファイル単位）は、範囲に入る公開の宣言を
    挙げる。役割は row（型、関数、メソッド）、member（型に属する変数と enum の case）、
    constant（型に属さない変数）のいずれか。
    名前の無い箇所で宣言を挙げられないとき（削除された箇所、アダプタの無い言語、読めないファイル、
    範囲に公開の宣言が無い）は、シグネチャの無い row の一件を返す。
    呼び出し側は空の場合を扱わずに済む。
    """
    name = region.qualified_name
    span = region.new_span
    adapter = registry.adapter_for(region.path)
    if span is None or adapter is None or data is None:
        # 削除された箇所には木の側の宣言が無く、後ろの二つは解析できない。名前だけを残す
        return [ScopedDeclaration(name, None, "", "row")]
    declarations = adapter.declarations(parse(adapter, data).root_node, data)
    if name:
        found = next((d for d in declarations if d.qualified_name == name), None)
        return [ScopedDeclaration(name, found, "", "row")]
    listed = [
        _scoped(d)
        for d in declarations
        if d.is_public
        and span.start_line <= d.span.start_line
        and d.span.end_line <= span.end_line
    ]
    return listed or [ScopedDeclaration(name, None, "", "row")]
