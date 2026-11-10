"""値の組の表の材料。追跡されたファイルから値の組を集め、珍しい値で絞り、重なる組をまとめる。

組の拾い方は言語アダプタの `ValueSetReader` が持つ。ここは言語に依らない集計だけを持つ。
"""

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from jigane.git.tree_files import read_tree_text, work_files
from jigane.languages import registry
from jigane.languages.base import (
    ComparisonKind,
    DefinitionKind,
    StringComparison,
    ValueSetReader,
)
from jigane.text.document_files import is_document_path

# 追跡されたファイルのこの割合未満にしか現れない値を、珍しい値とする
RARE_VALUE_RATIO = 0.05
# 小さな木でも値が珍しくなるよう、珍しさの上限をこの数より下げない（100 ファイルで割合と並ぶ）
MIN_RARE_CEILING = 5
# 珍しい値が残っても、この数に満たない組は値の組とみなさない
MIN_RARE_VALUES = 3
# 残った値のうち、この割合以上を含むファイルを数える。ただし最低でも MIN_RARE_VALUES 個
CONTAINING_VALUE_RATIO = 0.5
# 二つの組の珍しい値の Jaccard 係数がこの値以上なら、一つにまとめる
MERGE_OVERLAP = 0.5


class FileKind(StrEnum):
    CODE = "code"
    TEST = "test"
    DOCUMENT = "document"


@dataclass(frozen=True)
class ValueSetLocation:
    path: str
    # 1 始まり
    line: int
    # 組の名前。名前の無い並びは None
    name: str | None = None
    # 名前に束ねた定義か名前の無いリテラルか。重なる組をまとめても場所ごとに保つ
    kind: DefinitionKind = DefinitionKind.UNDETERMINED


@dataclass(frozen=True)
class FileCounts:
    """値を一定以上含むファイルの数。"""

    code: int
    test: int
    document: int


@dataclass(frozen=True)
class ValueSetRow:
    """表の一行。重なる組をまとめたときは、定義の場所が複数になり、値はその和になる。"""

    definitions: tuple[ValueSetLocation, ...]
    values: tuple[str, ...]
    files: FileCounts
    # 値を一定以上含むファイル。files はこれを種類ごとに数えたもの
    containing_paths: frozenset[str]
    # 組の値と文字列のまま比べている箇所。同じ箇所（パス、行、形）は一つにまとめる
    comparisons: tuple[StringComparison, ...]
    # 定義の無い値の組（`if` の連鎖などで同じ式を同じ値と比べるだけの組）では、比べている式の原文。
    # このとき definitions は空で、場所は comparisons の最初の要素。定義のある組は空
    subject: str = ""


@dataclass
class _Group:
    definitions: list[ValueSetLocation]
    values: set[str]


WORD_PATTERN = re.compile(r"[\w-]+")


def kind_of(path: str) -> FileKind:
    if registry.is_test_path(path, registry.adapters()):
        return FileKind.TEST
    return FileKind.DOCUMENT if is_document_path(path) else FileKind.CODE


def _files_containing(values: set[str], texts: Mapping[str, str]) -> dict[str, set[str]]:
    """値ごとの、その値を含むファイルの集合。

    各ファイルは一度だけ語に分け、語の並びに収まる値は語の集合で引く。
    空白や記号を含む値は語に分けられないので、その値だけ正規表現で数える。
    """
    found: dict[str, set[str]] = {value: set() for value in values}
    phrases = [value for value in values if WORD_PATTERN.fullmatch(value) is None]
    # 語の境界の正規表現は組み立てが重いので、値ごとに一度だけ作る
    phrase_patterns = {
        phrase: re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)") for phrase in phrases
    }
    for path, text in texts.items():
        for word in set(WORD_PATTERN.findall(text)):
            if word in found:
                found[word].add(path)
        for phrase in phrases:
            # 部分文字列の検索で先に落とし、含む見込みのあるファイルだけ正規表現で確かめる
            if phrase in text and phrase_patterns[phrase].search(text):
                found[phrase].add(path)
    return found


def _merge(groups: list[_Group]) -> list[_Group]:
    """珍しい値が重なる組を、重ならなくなるまで一つにまとめる。"""
    merged: list[_Group] = []
    for group in groups:
        target = next(
            (
                other
                for other in merged
                if len(other.values & group.values) / len(other.values | group.values)
                >= MERGE_OVERLAP
            ),
            None,
        )
        if target is None:
            merged.append(_Group(list(group.definitions), set(group.values)))
        else:
            target.definitions.extend(group.definitions)
            target.values |= group.values
    # 和が増えると、先にまとめなかった組と重なり始めることがある。変化が止まるまで繰り返す
    return merged if len(merged) == len(groups) else _merge(merged)


def _containing_paths(group: _Group, containing: dict[str, set[str]]) -> frozenset[str]:
    needed = max(MIN_RARE_VALUES, int(len(group.values) * CONTAINING_VALUE_RATIO + 0.999999))
    hits = Counter(path for value in group.values for path in containing[value])
    return frozenset(path for path, hit in hits.items() if hit >= needed)


def _counts(paths: frozenset[str]) -> FileCounts:
    kinds = Counter(kind_of(path) for path in paths)
    return FileCounts(kinds[FileKind.CODE], kinds[FileKind.TEST], kinds[FileKind.DOCUMENT])


def _compares_to(group: _Group, comparison: StringComparison) -> bool:
    """比べている文字列が組の値に当たるか。接頭辞と接尾辞は、組の値のいずれかに掛かれば当たる。"""
    match comparison.kind:
        case ComparisonKind.PREFIX:
            return any(value.startswith(comparison.value) for value in group.values)
        case ComparisonKind.SUFFIX:
            return any(value.endswith(comparison.value) for value in group.values)
        case ComparisonKind.EQUALS | ComparisonKind.MEMBERSHIP | ComparisonKind.REGEX:
            # 正規表現は、パターンの文字列を一つの値として扱う
            return comparison.value in group.values


def _matching_comparisons(
    group: _Group, comparisons: list[StringComparison]
) -> tuple[StringComparison, ...]:
    found: dict[tuple[str, int, ComparisonKind], StringComparison] = {}
    for comparison in comparisons:
        if _compares_to(group, comparison):
            # `in` の組は要素ごとに返るので、同じ箇所と形は先に当たった一つだけを残す
            found.setdefault((comparison.path, comparison.line, comparison.kind), comparison)
    return tuple(sorted(found.values(), key=lambda c: (c.path, c.line, c.kind)))


def _compared_only_candidates(
    comparisons: list[StringComparison],
) -> dict[tuple[str, str], list[StringComparison]]:
    """同じファイルの同じ式との等値の比べを、式ごとに束ねる。異なる値が足りない束は落とす。"""
    bundles: dict[tuple[str, str], list[StringComparison]] = {}
    for comparison in comparisons:
        if comparison.kind == ComparisonKind.EQUALS and comparison.subject:
            bundles.setdefault((comparison.path, comparison.subject), []).append(comparison)
    return {
        key: found
        for key, found in bundles.items()
        if len({c.value for c in found}) >= MIN_RARE_VALUES
    }


def build_value_set_rows(
    sources: Mapping[str, str], texts: Mapping[str, str] | None = None
) -> tuple[ValueSetRow, ...]:
    """値の組の表の行。sources は組を読めるファイル、texts は値を数える全ファイル。

    texts を省くと、sources の全ファイルで数える。行は、値を含むファイルの合計が多い順。
    """
    all_texts = sources if texts is None else texts
    groups: list[_Group] = []
    candidates: list[tuple[ValueSetLocation, frozenset[str]]] = []
    comparisons: list[StringComparison] = []
    for path, text in sorted(sources.items()):
        adapter = registry.adapter_for(path)
        if not isinstance(adapter, ValueSetReader):
            # 組の拾い方を持たない言語のファイルは、値を数える側にだけ使う
            continue
        candidates += [
            (ValueSetLocation(found.path, found.line, found.name, found.kind), found.values)
            for found in adapter.value_sets(text, path)
        ]
        comparisons += adapter.string_comparisons(text, path)
    bundles = _compared_only_candidates(comparisons)
    every_value = set().union(*(values for _, values in candidates))
    every_value |= {c.value for found in bundles.values() for c in found}
    containing = _files_containing(every_value, all_texts)
    ceiling = max(len(all_texts) * RARE_VALUE_RATIO, MIN_RARE_CEILING)
    for location, values in candidates:
        rare = {value for value in values if len(containing[value]) < ceiling}
        if len(rare) >= MIN_RARE_VALUES:
            groups.append(_Group([location], rare))
    rows = []
    defined_groups = _merge(groups)
    for (_, subject), found in bundles.items():
        # 定義のある組の値に当たる比べは、その組の行が持つので、ここでは数えない
        undefined = [c for c in found if not any(_compares_to(g, c) for g in defined_groups)]
        # 珍しさの規則は、定義のある組と同じに当てる
        rare = {c.value for c in undefined if len(containing[c.value]) < ceiling}
        if len(rare) >= MIN_RARE_VALUES:
            group = _Group([], rare)
            paths = _containing_paths(group, containing)
            kept = tuple(sorted((c for c in undefined if c.value in rare), key=lambda c: c.line))
            rows.append(ValueSetRow((), tuple(sorted(rare)), _counts(paths), paths, kept, subject))
    for group in defined_groups:
        paths = _containing_paths(group, containing)
        rows.append(
            ValueSetRow(
                tuple(sorted(group.definitions, key=lambda d: (d.path, d.line))),
                tuple(sorted(group.values)),
                _counts(paths),
                paths,
                _matching_comparisons(group, comparisons),
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda r: (
                -(r.files.code + r.files.test + r.files.document),
                [(d.path, d.line) for d in r.definitions],
            ),
        )
    )


def value_set_rows(root: Path) -> tuple[ValueSetRow, ...]:
    """追跡されたファイルから、値の組の表の行を組む。読めないファイルは数えない。"""
    texts: dict[str, str] = {}
    for path in work_files(root).files:
        text = read_tree_text(root, path)
        if text is not None:
            texts[path] = text
    return build_value_set_rows(texts)
