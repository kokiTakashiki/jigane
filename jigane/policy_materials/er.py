"""方針のレビューに渡す ER 図。変更した、項目を持つ型の項目と任意の項目、型どうしの関係を描く。

型と項目の宣言だけから作り、関数の本体は読まない。保存の関数の有無は分からないので描かない。
"""

import re
from pathlib import Path

from jigane.figures.models import MermaidFigure, Panel
from jigane.git.tree_files import walk_tree_files
from jigane.policy_materials import classes
from jigane.policy_materials.classes import _changed_types, distinct_members
from jigane.reviews.lifecycle import Review
from jigane.sequence.index import Member, TypeInfo
from jigane.sequence.indexes import Indexes

SECTION_ER = "ER 図"
LEGEND = (
    "変更した、項目を持つ型（列挙型とインターフェースを除く）と、その項目の型として現れる、"
    "項目を持つ型を、型と項目の宣言から作った。関数の本体と保存の関数は読まない。"
    "項目の型は原文の記号を `_` に置き換え、配列は `X_array`、Swift の `[X]` は `array_X`、"
    "`[K: V]` は `dict_K_V` とし、型全体が括弧の入れ子は内側も同じ規則で置き換えた"
    "（総称型の引数の中の括弧は置き換えない）。"
    "コメントの `optional` は、型注記が `X | None`、`Optional[X]`、`X?` の任意の項目。"
    "関係の線は項目の型が別の型を指すことを表し、"
    "`||--||` は必須、`||--o|` は任意（0 か 1）の参照。線の名前は項目の名前。"
)
_OPTIONAL = re.compile(r"(?:\|\s*None\b|\bNone\s*\||^\s*(?:typing\.)?Optional\[|\?\s*$)")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def is_optional(member: Member) -> bool:
    return bool(member.type_name and _OPTIONAL.search(member.type_name))


def data_fields(info: TypeInfo) -> list[Member]:
    return [g[0] for g in distinct_members(info) if g[0].kind in ("field", "property")]


def is_enum_like(info: TypeInfo) -> bool:
    # Python の列挙型は class で宣言され、基底の名前だけが印になる
    return info.kind == "enum" or any(
        base.endswith("Enum") or base == "Flag" for base in info.bases
    )


def is_data_type(info: TypeInfo) -> bool:
    return (
        info.kind in ("class", "struct", "record")
        and not is_enum_like(info)
        and bool(data_fields(info))
    )


_ARRAY_SUFFIX = re.compile(r"\[[\s,]*\]")


def _bracket_inner(text: str) -> str | None:
    # 先頭の `[` が末尾の `]` と対になるときだけ中身を返す。`[A][]` のような形は対象外。
    if not (text.startswith("[") and text.endswith("]")):
        return None
    depth = 0
    for index, char in enumerate(text):
        depth += (char == "[") - (char == "]")
        if depth == 0:
            return text[1:-1] if index == len(text) - 1 else None
    return None


def _top_level_colon(text: str) -> int:
    depth = 0
    for index, char in enumerate(text):
        if char in "[(<":
            depth += 1
        elif char in "])>":
            depth -= 1
        elif char == ":" and depth == 0:
            return index
    return -1


def _words(text: str) -> str:
    # Swift の `?` は並びの印ではないので、各段で末尾から落とす
    body = text.strip().rstrip("?").strip()
    inner = _bracket_inner(body)
    if inner is None:
        return _ARRAY_SUFFIX.sub("_array", body)
    colon = _top_level_colon(inner)
    if colon < 0:
        return f"array_{_words(inner)}"
    return f"dict_{_words(inner[:colon])}_{_words(inner[colon + 1 :])}"


def _attribute_type(text: str | None) -> str:
    # erDiagram の属性の型は単語の文字しか安全に書けない。外側の記号を `_` に落とすと、
    # 先頭と末尾の括弧は strip で消え、`Object[]` や `[Int]` が要素の型だけになる。
    # 並びの印は消える前に単語として残し、入れ子は内側から順に置き換える。
    cleaned = re.sub(r"\W+", "_", _words(text or "")).strip("_")
    return cleaned or "unknown"


def _target(member: Member, shown: dict[str, TypeInfo], owner: TypeInfo) -> TypeInfo | None:
    for name in _IDENTIFIER.findall(member.type_name or ""):
        info = shown.get(name)
        if info is not None and info.qualified_name != owner.qualified_name:
            return info
    return None


def er_types(indexes: Indexes, changed: list[TypeInfo]) -> list[TypeInfo]:
    """ER 図に出す型。変更した、項目を持つ型と、その項目の型に現れる、項目を持つ型（一段）。"""
    own = [t for t in changed if is_data_type(t)]
    known = {t.qualified_name for t in own}
    extra: dict[str, TypeInfo] = {}
    for info in own:
        for member in data_fields(info):
            for name in _IDENTIFIER.findall(member.type_name or ""):
                for candidate in indexes.named(name)[:1]:
                    if is_data_type(candidate) and candidate.qualified_name not in known:
                        extra.setdefault(candidate.qualified_name, candidate)
    return [*own, *sorted(extra.values(), key=lambda t: (t.path, t.line))]


def er_diagram(review: Review) -> MermaidFigure | None:
    """ER 図。変更した、項目を持つ型が無ければ None。"""
    root = Path(review.repository_root)
    paths = walk_tree_files(root, root) if review.is_diff else review.paths
    indexes = Indexes(root, paths)
    changed = _changed_types(indexes, review.map_kind.segments)
    ordered = er_types(indexes, changed)
    if not ordered:
        return None
    names = {t.name: t for t in ordered}
    body = ["erDiagram"]
    relations: list[str] = []
    for info in ordered:
        display = indexes.display(info)
        body.append(f"    %% {display} {info.path}:{info.line}")
        body.append(f"    {classes.diagram_id(display)} {{")
        fields = data_fields(info)
        for member in fields:
            comment = ' "optional"' if is_optional(member) else ""
            body.append(f"        {_attribute_type(member.type_name)} {member.name}{comment}")
            target = _target(member, names, info)
            if target is not None:
                arrow = "||--o|" if is_optional(member) else "||--||"
                relations.append(
                    f"    {classes.diagram_id(display)} {arrow} "
                    f'{classes.diagram_id(indexes.display(target))} : "{member.name}"'
                )
        body.append("    }")
    return MermaidFigure(LEGEND, (Panel("", "\n".join([*body, *sorted(set(relations))])),))
