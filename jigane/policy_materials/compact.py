"""Codex の方針資料に載せる変更節点と根拠の位置。"""

import json
import re
import string
from collections import Counter
from dataclasses import dataclass
from itertools import groupby

from jigane.figures.models import FileState
from jigane.judgment_map.changes import (
    OMITTED_BINARY,
    OMITTED_GENERATED,
    OMITTED_LIMIT,
    OMITTED_TOO_LARGE,
)
from jigane.judgment_map.kinds import Kind
from jigane.judgment_map.region_table import CodeRegion, Region
from jigane.languages import registry
from jigane.policy_materials import changed_files
from jigane.policy_materials.classes import by_simple_name
from jigane.policy_materials.type_deltas import TypeDeltas, members_of, render_member
from jigane.reviews.lifecycle import Review

# 小さい資料では、位置と状態を各行に書く従来の形が読みやすい。
_COMPACT_FROM_BYTES = 8 * 1024
_REFERENCE = "%"
_REFERENCES = r"%[A-Za-z%]"
_FRAGMENT_TOKENS = re.compile(r"\w+|[^\w\s]")
_HEADINGS = re.compile(r"(?m)^(### [^\n]*(?:\n|$))")
_FRAGMENT_END = "### 共通表記の終わり"
_MIN_FRAGMENT_LENGTH = 5
_MAX_FRAGMENT_TOKENS = 12
_FRAGMENT_LOOKUP_COST = 12
_LARGE_MATERIALS = 20 * 1024
_PATH_FLAGS = {
    FileState.ADDED.label: "+",
    FileState.DELETED.label: "-",
    FileState.CHANGED.label: "~",
    FileState.MOVED.label: ">",
    FileState.UNCHANGED.label: "=",
    "範囲のみ": "r",
    "範囲外": "s",
    "対象外": "t",
    "生成物・除外": "g",
    OMITTED_BINARY: "b",
    OMITTED_GENERATED: "a",
    OMITTED_LIMIT: "l",
    OMITTED_TOO_LARGE: "z",
}


@dataclass(frozen=True)
class ChangeNodes:
    text: str
    numbers: dict[str, int]
    packed: bool = False

    def path(self, path: str) -> str:
        number = self.numbers.get(path)
        return f"#{number}" if number is not None else path

    def place(self, place: str) -> str:
        path, sep, line = place.rpartition(":")
        return f"{self.path(path)}{sep}{line}"


def change_nodes(review: Review, scope: set[str], omissions: dict[str, str]) -> ChangeNodes:
    """パスを一度だけ掲示し、判断箇所は同じ表の番号から参照する。"""
    changes = changed_files.file_changes(review)
    targets = set(review.paths)
    paths = sorted(
        scope
        | targets
        | changes.keys()
        | {
            change.moved_from
            for change in changes.values()
            if isinstance(change, changed_files.MovedFileChange)
        }
    )
    generated = {
        segment.new_path or segment.old_path
        for segment in review.map_kind.segments
        if segment.kind is Kind.GENERATED
    }
    numbers = {path: number for number, path in enumerate(paths, 1)}
    path_flags: dict[str, list[str]] = {}
    for path in paths:
        change = changes.get(path)
        flags = []
        if path not in scope:
            flags.append("範囲外")
        if path not in targets:
            flags.append("対象外")
        if change is not None:
            flags.append(change.state.label)
            if isinstance(change, changed_files.MovedFileChange):
                flags.append(f"移動元 {numbers[change.moved_from]}")
        elif path in scope:
            flags.append("範囲のみ")
        if path in omissions:
            flags.append(omissions[path])
        elif path in generated:
            flags.append("生成物・除外")
        path_flags[path] = flags
    nodes = _render_nodes(review, path_flags, numbers)
    deleted = sum(change.state is FileState.DELETED for change in changes.values())
    if deleted:
        return ChangeNodes(
            f"{nodes.text}\n全削除 {deleted} 件。削除本文は省略。", numbers, nodes.packed
        )
    return nodes


def _render_nodes(
    review: Review, path_flags: dict[str, list[str]], numbers: dict[str, int]
) -> ChangeNodes:
    lines = ["### 変更節点と範囲", "#番号と判断箇所行頭のパス番号はこの表を引く。"]
    lines.extend(_path_line(numbers[path], path, flags) for path, flags in path_flags.items())
    lines.extend(_region_lines(review, numbers))
    text = "\n".join(lines)
    size = len(text.encode("utf-8"))
    if size >= _COMPACT_FROM_BYTES:
        packed = "\n".join(
            [*_compact_paths(path_flags, numbers), *_region_lines(review, numbers, packed=True)]
        )
        if len(packed.encode("utf-8")) < size:
            return ChangeNodes(packed, numbers, packed=True)
    return ChangeNodes(text, numbers)


def _path_line(number: int, path: str, flags: list[str]) -> str:
    flags_text = f" [{' / '.join(flags)}]" if flags else ""
    return f"{number} {json.dumps(path, ensure_ascii=False)}{flags_text}"


def _compact_paths(path_flags: dict[str, list[str]], numbers: dict[str, int]) -> list[str]:
    labels = "、".join(f"{code}={flag}" for flag, code in _PATH_FLAGS.items())
    lines = [
        "### 変更節点と範囲",
        '「@」は接頭辞 接尾辞 [既定状態]。パス=接頭辞+中央部+接尾辞。引用部は JSON、"" は空。',
        f"#番号はこの表。行に状態がなければ @ の既定。状態 {labels}。移動元はパス番号。",
    ]
    directories: dict[str, list[tuple[int, str, str]]] = {}
    for path, flags in path_flags.items():
        directory, slash, name = path.rpartition("/")
        codes = " ".join(_PATH_FLAGS.get(flag, flag) for flag in flags)
        directories.setdefault(directory + slash, []).append((numbers[path], name, codes))
    for directory, files in directories.items():
        prefix, suffix = _filename_affixes([name for _, name, _ in files])
        quoted_prefix = json.dumps(directory + prefix, ensure_ascii=False)
        quoted_suffix = json.dumps(suffix, ensure_ascii=False)
        common, count = Counter(codes for _, _, codes in files).most_common(1)[0]
        default = common if count > 1 else ""
        header = f"@ {quoted_prefix} {quoted_suffix}"
        lines.append(f"{header} {default}" if default else header)
        for number, name, codes in files:
            middle = _compact_path_part(name[len(prefix) : len(name) - len(suffix)])
            row = f"{number} {middle}"
            lines.append(row if codes == default else f"{row} {codes}")
    return lines


def _compact_path_part(part: str) -> str:
    quoted = json.dumps(part, ensure_ascii=False)
    if part and quoted[1:-1] == part and not any(char.isspace() for char in part):
        return part
    return quoted


def _filename_affixes(names: list[str]) -> tuple[str, str]:
    if len(names) <= 1:
        return "", ""
    prefix = shared_prefix(min(names), max(names))
    # test_ や .py のように区切りのある部分を共有する。
    split = max(prefix.rfind(mark) for mark in "-_.") + 1
    reversed_names = [name[split:][::-1] for name in names]
    suffix = shared_prefix(min(reversed_names), max(reversed_names))[::-1]
    return prefix[:split], suffix[suffix.find(".") :] if "." in suffix else ""


def shared_prefix(first: str, second: str) -> str:
    for index, (left, right) in enumerate(zip(first, second, strict=False)):
        if left != right:
            return first[:index]
    return first[: min(len(first), len(second))]


def _repeated_fragments(text: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for line in text.splitlines():
        tokens = list(_FRAGMENT_TOKENS.finditer(line))
        for index, first in enumerate(tokens):
            if not first.group().isalnum():
                continue
            for last in tokens[index : index + _MAX_FRAGMENT_TOKENS]:
                fragment = line[first.start() : last.end()]
                if len(fragment) >= _MIN_FRAGMENT_LENGTH and _REFERENCE not in fragment:
                    counts[fragment] += 1
    return {
        fragment: len(fragment.encode("utf-8")) for fragment, count in counts.items() if count > 1
    }


def shared_fragments(sections: list[str]) -> str:
    """大きな静的資料の繰り返しだけを、同じ資料内の原文への参照にする。"""
    text = "\n\n".join(filter(None, sections))
    size = len(text.encode("utf-8"))
    if size < _LARGE_MATERIALS or _FRAGMENT_END in text:
        return text
    # 見出しを探す手がかりとして残し、参照は本文の行の中だけに入れる。
    pieces = _HEADINGS.split(text)
    bodies = [part.replace(_REFERENCE, _REFERENCE * 2) for part in pieces[::2]]
    body = "\n".join(bodies)
    references = re.compile(_REFERENCES)
    fragments = _repeated_fragments(references.sub("\n", body))
    aliases = []
    for letter in string.ascii_uppercase + string.ascii_lowercase:
        ref = f"{_REFERENCE}{letter}"
        ref_size = len(ref.encode("utf-8"))
        literal = references.sub("\n", body)
        scores = {
            fragment: (length - ref_size) * literal.count(fragment) - length
            for fragment, length in fragments.items()
        }
        fragment = max(scores, key=scores.__getitem__, default="")
        if not fragment or scores[fragment] < _FRAGMENT_LOOKUP_COST:
            break
        aliases.append(f"{ref} = {json.dumps(fragment, ensure_ascii=False)}")
        del fragments[fragment]
        pattern = re.compile(f"{_REFERENCES}|{re.escape(fragment)}")
        bodies = [
            pattern.sub(
                lambda match, replacement=ref: (
                    match[0] if match[0].startswith(_REFERENCE) else replacement
                ),
                part,
            )
            for part in bodies
        ]
        body = "\n".join(bodies)
    if not aliases:
        return text
    pieces[::2] = bodies
    packed = "\n".join(
        [
            "### 資料中の共通表記",
            "「共通表記の終わり」までの本文で %と英字は右辺の JSON 文字列、%%は元の % に戻す。",
            "位置と識別子を出力するときは、展開した表記を使う。",
            *aliases,
            "",
            "".join(pieces),
            "",
            _FRAGMENT_END,
        ]
    )
    return packed if len(packed.encode("utf-8")) < size else text


def _region_lines(review: Review, numbers: dict[str, int], *, packed: bool = False) -> list[str]:
    regions = review.map_kind.table.regions
    kinds = {kind: str(index) for index, kind in enumerate(sorted({r.kind for r in regions}), 1)}
    lines = [
        "",
        "### 判断箇所（番号 → 上のパス、旧 → 変更前）",
        "key の別記がない行の judgment_region は パス::名前::種類（種類番号は下の語へ戻す）。",
    ]
    if kinds:
        lines.append("種類 " + "、".join(f"{code}={kind.value}" for kind, code in kinds.items()))
    adapters = registry.adapters()
    tests: dict[str, list[str]] = {}
    groups: dict[tuple[str, str], list[Region]] = {}
    for region in regions:
        if registry.is_test_path(region.path, adapters):
            tests.setdefault(region.path, []).append(f"{region.id}:{_region_span(region)}")
        else:
            kind = kinds[region.kind]
            if packed:
                groups.setdefault((_region_path(region, numbers), kind), []).append(region)
            else:
                lines.append(_region_line(region, numbers, kind))
    if groups:
        lines.append(
            "@ はパスと種類番号。各行は J番号:行 名前、^ は旧側。"
            "= は名前の接頭辞を指定し、@ ごとに空へ戻る。"
        )
        for (path, kind), rows in groups.items():
            lines.append(f"@ {path} {kind}")
            lines.extend(_compact_region_group(rows, numbers))
    if tests:
        lines.append("テストの判断箇所はファイル別に J番号:行範囲のみ。名前・種類・key は省略。")
        lines.extend(
            f"{numbers.get(path, path)} {' '.join(sites)}" for path, sites in tests.items()
        )
    return lines


def _compact_region_group(regions: list[Region], numbers: dict[str, int]) -> list[str]:
    lines = []
    previous = ""
    for owner, group in groupby(
        regions, key=lambda region: region.qualified_name.rpartition(".")[0]
    ):
        rows = list(group)
        prefix = f"{owner}." if owner and len(rows) > 1 else ""
        if prefix != previous:
            lines.append(f"= {json.dumps(prefix, ensure_ascii=False)}")
            previous = prefix
        lines.extend(_region_line(region, numbers, "", prefix=prefix) for region in rows)
    return lines


def _region_span(region: Region) -> str:
    span = region.new_span or region.old_span
    position = f"{span.start_line}-{span.end_line}" if span else "-"
    return f"{position}{'旧' if region.new_span is None else ''}"


def _region_path(region: Region, numbers: dict[str, int]) -> str:
    number = numbers.get(region.path)
    return str(number) if number is not None else json.dumps(region.path, ensure_ascii=False)


def _region_line(
    region: Region, numbers: dict[str, int], kind_code: str, *, prefix: str = ""
) -> str:
    span = region.new_span or region.old_span
    position = f":{span.start_line}-{span.end_line}" if span else ""
    side = " 旧" if region.new_span is None else ""
    old_path = region.old_path if isinstance(region, CodeRegion) else None
    old_ref = f" 旧パス={numbers.get(old_path, old_path)}" if old_path else ""
    standard_key = f"{region.path}::{region.qualified_name}::{region.kind.value}"
    key = f" key={region.key}" if region.key != standard_key else ""
    if not kind_code:
        if span is not None and span.start_line == span.end_line:
            position = f":{span.start_line}"
        old = "^" if side else ""
        name = region.qualified_name[len(prefix) :]
        return f"{region.id}{position}{old} {name}{old_ref}{key}"
    return (
        f"{region.id} {_region_path(region, numbers)}{position}{side} {kind_code} "
        f"{region.qualified_name}{old_ref}{key}"
    )


def type_changes(result: TypeDeltas, nodes: ChangeNodes) -> str:
    """変更した型と、その宣言に現れる基底・項目だけを示す。"""
    lines = [
        "### 変更型と関連する宣言",
        (
            "型の位置と基底を示す。項目は型注記の変更・追加・削除のみ。"
            "未変更の項目、兄弟型の一覧、クラス図は省略。"
        ),
    ]
    for delta in result.deltas:
        info = delta.info
        if not delta.changed or info.kind == "module":
            continue
        bases = ", ".join(info.bases)
        old_bases = ""
        if delta.shape is not None and delta.shape.bases_changed:
            old_bases = ", ".join(delta.shape.bases_before) + " → "
        base_text = f" : {old_bases}{bases or '∅'}" if bases or old_bases else ""
        kind: str = f"abstract {info.kind}" if info.abstract else info.kind
        if delta.shape is not None and delta.shape.kind_changed:
            kind = f"{delta.shape.kind_before} → {kind}"
        move = (
            f" ← {nodes.path(delta.moved_from.path)}:{delta.moved_from.line}"
            if delta.moved_from
            else ""
        )
        added = "+ " if delta.added else ""
        name = result.indexes.display(info)
        lines.append(f"{nodes.path(info.path)}:{info.line} {added}{kind} {name}{base_text}{move}")
        before = delta.moved_from
        if before is None and result.before_indexes is not None:
            before = result.before_indexes.find(info.key)
        # 公開メソッドの前後はシグネチャ節に集約する。項目の型注記はそこには出ない。
        for member in members_of(info):
            mark = delta.member_marks.get((member.path, member.line), "")
            if mark and member.kind not in ("method", "constructor"):
                location = (
                    str(member.line)
                    if member.path == info.path
                    else f"{nodes.path(member.path)}:{member.line}"
                )
                form = render_member(member)
                if mark == "~" and before is not None:
                    previous = sorted(
                        {render_member(old) for old in before.members.get(member.name, ())}
                    )
                    if previous:
                        form = f"{' / '.join(previous)} → {form}"
                lines.append(f"  {mark}{location} {form}")
        lines.extend(f"  - {name}" for name in delta.removed_members)
        lines.extend(f"  + {value}" for value, mark in delta.value_marks if mark)
    return "\n".join(lines)


def implementation_edges(result: TypeDeltas, nodes: ChangeNodes) -> str:
    """変更型に接する抽象型と実装の辺だけを、P2 に示す。"""
    bases = {
        name: infos[0]
        for name, infos in by_simple_name([delta.info for delta in result.deltas]).items()
    }
    changed = {delta.info.key for delta in result.deltas if delta.changed}
    rows = []
    for delta in result.deltas:
        derived = delta.info
        for name in derived.bases:
            base = bases.get(name)
            if base is None or not (base.abstract or base.kind == "interface"):
                continue
            if base.key in changed or derived.key in changed:
                rows.append(
                    f"{nodes.path(base.path)}:{base.line} {result.indexes.display(base)} ← "
                    f"{nodes.path(derived.path)}:{derived.line} {result.indexes.display(derived)}"
                )
    if not rows:
        return ""
    return "\n".join(
        [
            "### 抽象型と実装",
            "索引で照合できた変更型に接する辺。掲載数を実装の総数とみなさない。",
            *rows,
        ]
    )
