"""flaky の型の目録。項目は catalog.toml、言語ごとの query は queries/<言語>.toml で持つ。"""

import re
import tomllib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from jigane.flaky.checks import CHECKS, CONTEXT_CHECKS
from jigane.gates.explanation import GateExplanation
from jigane.languages import registry
from jigane.languages.data_places import toml_names

CATALOG_FILE = Path(__file__).with_name("catalog.toml")
CAUSES = frozenset(
    {
        "Async Wait",
        "Time",
        "Randomness",
        "Network",
        "IO",
        "Unordered Collections",
        "Floating Point Operations",
        "Conditional Skip",
        "Test Order Dependency",
        "Resource Leak",
        "Concurrency",
    }
)
CONFIDENCES = frozenset({"direct", "proxy"})
ID_PATTERN = re.compile(r"^FLK-[A-Z]+(-[A-Z]+)*$")
ANNOTATION_ID = "FLK-ANNOTATION"


# 注記の違反は目録の項目を持たないので、文はここで持つ
ANNOTATION_EXPLANATION = GateExplanation(
    "allow-flaky の注記が書式に合わないと、どの規則を何の理由で許したのかを機械が読めません。",
    "注記に規則の名前か理由が無い、または知らない規則を指しています。",
    "注記を allow-flaky <ID> -- <理由> の形に直してください。",
)


class CatalogError(Exception):
    pass


class EquivalentState(StrEnum):
    FOUND = "found"
    NONE = "none"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Equivalent:
    """項目と言語の組に一つ。相当する既製のリンタの規則の有無と、その確かさ。"""

    state: EquivalentState
    # FOUND のときだけ持つ。規則の名前（例: ruff DTZ005）
    rules: tuple[str, ...] = ()
    # FOUND では jigane の型との違い、NONE では相当が無い理由の補足。UNVERIFIED では持たない
    note: str = ""

    def __post_init__(self) -> None:
        if self.state is EquivalentState.FOUND:
            if not self.rules or not self.note.strip():
                raise CatalogError("found には rules と note が必要です")
        elif self.rules:
            raise CatalogError(f"{self.state.value} は rules を持てません")
        elif self.state is EquivalentState.UNVERIFIED and self.note:
            raise CatalogError("unverified は note を持てません")

    def to_entry(self) -> dict[str, Any]:
        return {"state": self.state.value, "rules": list(self.rules), "note": self.note}


@dataclass(frozen=True)
class QuerySpec:
    # 言語アダプタの name
    language: str
    # ヒットした節を @hit に取る
    query: str
    # CHECKS の名前。query の結果に掛ける検査
    check: str | None
    # この言語の検出が当てる範囲と当てない範囲。項目の legitimate とは別の欄に表示する
    note: str | None
    # その言語に固有の直す向き。あれば項目の explanation.direction の代わりに表示する
    direction: str | None
    # 項目とこの言語の組の相当。同じ組の query どうしは同じ値を指す
    equivalent: Equivalent


@dataclass(frozen=True)
class Item:
    id: str
    cause: str
    confidence: str
    source: str
    legitimate: str
    # 返信とレポートに ID の代わりに出す三つの文。直す向きもここだけが持つ
    explanation: GateExplanation
    queries: tuple[QuerySpec, ...]


def _text(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}: {key} が無いか空です")
    return value


def _query(entry: dict[str, Any], language: str, equivalent: Equivalent) -> tuple[str, QuerySpec]:
    query_id = _text(entry, "id", language)
    where = f"{language}/{query_id}"
    check = entry.get("check")
    if check is not None and check not in CHECKS and check not in CONTEXT_CHECKS:
        raise CatalogError(f"{where}: 知らない check: {check}")
    note = entry.get("note")
    if note is not None:
        note = _text(entry, "note", where)
    direction = entry.get("direction")
    if direction is not None:
        direction = _text(entry, "direction", where)
    spec = QuerySpec(language, _text(entry, "query", where), check, note, direction, equivalent)
    return query_id, spec


def _equivalent(table: dict[str, Any], where: str) -> Equivalent:
    unknown = set(table) - {"state", "rules", "note"}
    if unknown:
        raise CatalogError(f"{where}: 知らない鍵: {sorted(unknown)}")
    try:
        state = EquivalentState(str(table.get("state")))
    except ValueError:
        raise CatalogError(f"{where}: state が無いか不正です") from None
    rules = table.get("rules", [])
    if not isinstance(rules, list) or not all(isinstance(r, str) and r.strip() for r in rules):
        raise CatalogError(f"{where}: rules は空でない文字列の配列です")
    note = table.get("note", "")
    if not isinstance(note, str):
        raise CatalogError(f"{where}: note は文字列です")
    try:
        return Equivalent(state, tuple(rules), note)
    except CatalogError as error:
        raise CatalogError(f"{where}: {error}") from None


def _equivalents(
    entry: dict[str, Any], languages: Collection[str], where: str, required: Collection[str]
) -> dict[str, Equivalent]:
    """required の言語は表が要る。それ以外（外の言語）は、表が無ければ未検証にする。"""
    table = entry.get("equivalent")
    if not isinstance(table, dict):
        raise CatalogError(f"{where}: equivalent がありません")
    unknown = set(table) - set(languages)
    if unknown:
        raise CatalogError(f"{where}/equivalent: 言語アダプタに無い言語: {sorted(unknown)}")
    missing = [lang for lang in sorted(required) if not isinstance(table.get(lang), dict)]
    if missing:
        raise CatalogError(f"{where}/equivalent: 言語の表がありません: {missing}")
    return {
        language: (
            _equivalent(table[language], f"{where}/equivalent/{language}")
            if isinstance(table.get(language), dict)
            else Equivalent(EquivalentState.UNVERIFIED)
        )
        for language in sorted(languages)
    }


def _item(entry: dict[str, Any], queries: tuple[QuerySpec, ...]) -> Item:
    where = str(entry.get("id", "?"))
    item_id = _text(entry, "id", where)
    if ID_PATTERN.match(item_id) is None:
        raise CatalogError(f"{where}: id の形が不正です")
    cause = _text(entry, "cause", where)
    if cause not in CAUSES:
        raise CatalogError(f"{where}: 知らない cause: {cause}")
    confidence = _text(entry, "confidence", where)
    if confidence not in CONFIDENCES:
        raise CatalogError(f"{where}: 知らない confidence: {confidence}")
    return Item(
        item_id,
        cause,
        confidence,
        _text(entry, "source", where),
        _text(entry, "legitimate", where),
        GateExplanation(
            _text(entry, "concern", where),
            _text(entry, "violation", where),
            _text(entry, "direction", where),
        ),
        queries,
    )


def _load_toml(text: str, where: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CatalogError(f"{where}を読めません: {error}") from error


def parse_catalog(
    text: str,
    query_texts: Mapping[str, str],
    languages: Collection[str],
    required: Collection[str] | None = None,
) -> list[Item]:
    """query_texts は言語アダプタの name から、その言語の queries ファイルの本文への対応。

    required は目録に相当の表が要る言語（組み込み）。省略時は languages の全て。
    """
    required = languages if required is None else required
    entries = _load_toml(text, "目録").get("item", [])
    ids = [entry.get("id") for entry in entries]
    if len(set(ids)) != len(ids):
        raise CatalogError("id が重複しています")
    equivalents = {
        str(entry.get("id")): _equivalents(entry, languages, str(entry.get("id", "?")), required)
        for entry in entries
    }
    by_id: dict[str, list[QuerySpec]] = {}
    for language, query_text in query_texts.items():
        if language not in languages:
            raise CatalogError(f"{language}: 言語アダプタに無い言語の queries")
        # check が違えば、同じ id に複数の query を置ける。Python の FLK-FLOAT が該当する
        seen: set[tuple[str, str | None]] = set()
        for entry in _load_toml(query_text, f"{language} の queries").get("query", []):
            query_id = _text(entry, "id", language)
            if query_id not in ids:
                raise CatalogError(f"{language}/{query_id}: 目録に無い id")
            query_id, spec = _query(entry, language, equivalents[query_id][language])
            if (query_id, spec.check) in seen:
                raise CatalogError(
                    f"{language}/{query_id}: 同じ言語で id と check が重複しています"
                )
            seen.add((query_id, spec.check))
            by_id.setdefault(query_id, []).append(spec)
    return [_item(entry, tuple(by_id.get(str(entry.get("id")), []))) for entry in entries]


def load_catalog(
    path: Path = CATALOG_FILE, places: Sequence[Traversable] | None = None
) -> list[Item]:
    """places は flaky の型の置き場の一覧で、先の置き場のファイルが勝つ。省略時は登録の一覧。"""
    if places is None:
        places = registry.flaky_places()
    query_texts: dict[str, str] = {}
    for place in places:
        for name in toml_names(place):
            # 同じ名前は先の置き場が勝つ。外の言語との重なりは発見のときに誤りにしてある
            query_texts.setdefault(
                name.removesuffix(".toml"), place.joinpath(name).read_text(encoding="utf-8")
            )
    languages = {adapter.name for adapter in registry.adapters()}
    builtin = {plugin.adapter.name for plugin in registry.builtin_plugins()}
    return parse_catalog(path.read_text(encoding="utf-8"), query_texts, languages, builtin)
