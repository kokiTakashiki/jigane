"""型注記の原文から単純な型の名前を取り出す部品。各言語のアダプタが、自分の書き方の分を組み合わせて使う。"""

import re

_GENERIC_ARGUMENTS = re.compile(r"<[^<>]*>")
# Python の型引数 list[X] と、C# と Java の配列 T[]。先頭が [ の Swift の [X] には当たらない
_SUBSCRIPT = re.compile(r"(?<=\w)\[[^\[\]]*\]")
_OPTIONAL = re.compile(r"^(?:typing\.)?Optional\[(.*)\]$")
# Swift の型の前に付く修飾（some P、any P、inout T、@escaping T）
_SWIFT_TYPE_PREFIX = re.compile(r"^(?:(?:some|any|inout|borrowing|consuming|@\w+)\s+)+")


def _strip_subscripts(raw: str) -> str:
    while (stripped := _SUBSCRIPT.sub("", raw)) != raw:
        raw = stripped
    return raw


def _strip_generic_arguments(raw: str) -> str:
    while "<" in raw:
        stripped = _GENERIC_ARGUMENTS.sub("", raw)
        if stripped == raw:
            break
        raw = stripped
    return raw


def _last_segment(raw: str) -> str:
    return raw.strip().rstrip("?").rsplit(".", 1)[-1]


def python_simple_type_name(raw: str) -> str:
    """Optional[X] と X | None は X に、list[X] は list に、文字列の注釈は中身にする。"""
    raw = raw.strip().strip("'\"")
    optional = _OPTIONAL.match(raw)
    if optional is not None:
        raw = optional.group(1)
    if "|" in raw:
        members = [m.strip() for m in raw.split("|") if m.strip() != "None"]
        raw = members[0] if len(members) == 1 else raw
    raw = raw.strip().strip("'\"")
    return _last_segment(_strip_generic_arguments(_strip_subscripts(raw)))


def swift_simple_type_name(raw: str) -> str:
    """修飾（some、any）と Null 許容の印を落とす。

    `[X]` は Array、
    `[K: V]` は Dictionary にする。
    """
    raw = _SWIFT_TYPE_PREFIX.sub("", raw.strip()).rstrip("?!")
    if raw.startswith("[") and raw.endswith("]"):
        return _swift_collection_name(raw[1:-1])
    return _last_segment(_strip_generic_arguments(_strip_subscripts(raw)))


def swift_composition_type_names(raw: str) -> list[str]:
    """Swift の合成の型 `any A & B` の成分の単純名。合成でなければ空。"""
    stripped = _SWIFT_TYPE_PREFIX.sub("", raw.strip()).strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1]
    parts = _split_top_level(stripped, "&")
    return [swift_simple_type_name(part) for part in parts] if len(parts) > 1 else []


def generic_simple_type_name(raw: str) -> str:
    """Java と C# の、総称型の引数、配列、Null 許容の印、名前空間の接頭辞を落とした単純名。"""
    raw = raw.removeprefix("global::").strip().rstrip("?!")
    return _last_segment(_strip_generic_arguments(_strip_subscripts(raw)))


def _split_top_level(text: str, separator: str) -> list[str]:
    depth = 0
    parts: list[str] = []
    start = 0
    for position, character in enumerate(text):
        if character in "[<(":
            depth += 1
        elif character in "]>)":
            depth -= 1
        elif character == separator and depth == 0:
            parts.append(text[start:position].strip())
            start = position + 1
    parts.append(text[start:].strip())
    return parts


def _swift_collection_name(inner: str) -> str:
    """Swift の `[X]` は Array、`[K: V]` は Dictionary とする。

    要素の型 X にしないのは、コンテナ自身のメソッドの呼び出しを要素の型のものと誤って解決しないため
    （Python の list[X] や C# の List<X> と同じく、コンテナ自身の名前を採る）。
    """
    depth = 0
    for character in inner:
        if character in "[<(":
            depth += 1
        elif character in "]>)":
            depth -= 1
        elif character == ":" and depth == 0:
            return "Dictionary"
    return "Array"


def capitalized_is_external_type(name: str) -> bool:
    """型の名前を大文字で始める慣習の言語で、索引に無い大文字始まりの識別子を型の名前とみなす。"""
    return name[:1].isupper()
