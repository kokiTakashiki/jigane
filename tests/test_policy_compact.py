import json
import re
from pathlib import Path

from review_fakes import make_review
from test_policy_classes import whole

from jigane.judgment_map.changes import OMITTED_LIMIT
from jigane.judgment_map.kinds import Kind
from jigane.judgment_map.region_table import CodeRegion
from jigane.languages.base import Span
from jigane.policy_materials.compact import change_nodes, shared_fragments
from jigane.storage.store import ReviewKind


def test_large_change_nodes_keep_file_flags_and_reconstruct_region_locations(
    tmp_path: Path,
) -> None:
    directory = "src/deeply_nested_folder_for_materials/subfolder_with_escaped_names"
    paths = [f'{directory}/part_{i:03d}_資料".py' for i in range(100)]
    moved, *others = paths
    old = "old.py"
    test = "tests/test_call.py"
    paths.append(test)
    regions = [
        CodeRegion(
            kind=Kind.PUBLIC_INTERFACE,
            id=f"J{number}",
            path=moved,
            qualified_name=f"Owner.{name}",
            key=f"{moved}::Owner.{name}::public_interface" if number != 2 else "legacy-key",
            summary="",
            old_span=Span(number, number),
            new_span=None if number == 2 else Span(number, number),
            old_path=old,
            line_count=1,
            content_hash="",
        ).to_stored_dict()
        for number, name in enumerate(("first", "second", "third"), 1)
    ]
    regions.append({**regions[0], "id": "J4", "path": test, "old_path": None})
    review = make_review(
        tmp_path,
        paths,
        kind=ReviewKind.FEATURE,
        judgment_map={
            "segments": [
                {**whole(moved, 3), "old_path": old},
                *(whole(path, 3) for path in others),
            ],
            "regions": regions,
            "line_counts": {},
        },
    )
    nodes = change_nodes(review, set(paths), dict.fromkeys(others, OMITTED_LIMIT))
    files, _, sites = nodes.text.partition("\n### 判断箇所")
    assert nodes.packed
    decoder = json.JSONDecoder()
    decoded = {}
    for line in files.splitlines():
        if line.startswith("@ "):
            prefix, end = decoder.raw_decode(line[2:])
            suffix, end = decoder.raw_decode(rest := line[2 + end :].lstrip())
            default = rest[end:].strip()
        elif line[0].isdigit():
            number, _, record = line.partition(" ")
            if record.startswith('"'):
                middle, end = decoder.raw_decode(record)
                flags = record[end:].strip()
            else:
                middle, _, flags = record.partition(" ")
            decoded[prefix + middle + suffix] = (int(number), flags or default)
    assert {path: number for path, (number, _) in decoded.items()} == nodes.numbers
    assert f'@ "{directory}/part_" ".py" ~ l' in files
    assert f"{nodes.numbers[old]} old.py s t" in files
    assert decoded[moved][1] == f"> 移動元 {nodes.numbers[old]}"
    assert decoded[old][1] == "s t"
    assert all(decoded[path][1] == "~ l" for path in others)
    assert f'@ {nodes.numbers[moved]} 1\n= "Owner."\n' in sites
    assert f"J1:1 first 旧パス={nodes.numbers[old]}" in sites
    assert f"J2:2^ second 旧パス={nodes.numbers[old]} key=legacy-key" in sites
    assert f"J3:3 third 旧パス={nodes.numbers[old]}" in sites
    assert f"{nodes.numbers[test]} J4:1-1" in sites


def test_small_change_nodes_keep_full_path_and_flags(tmp_path: Path) -> None:
    path = "src/main.py"
    review = make_review(tmp_path, [path], kind=ReviewKind.FEATURE)

    nodes = change_nodes(review, {path}, {})

    assert not nodes.packed
    assert f'1 "{path}" [範囲のみ]' in nodes.text


def test_large_material_fragments_round_trip_with_headings_and_literal_reference() -> None:
    title = "### 判断箇所 %A"
    comparison = 'def execute(options: ReviewOptions = "%B") ⟦旧¦新⟧'
    evidence = "P0->>P1 execute(options: ReviewOptions) [J102]（追加） ×2"
    lines = [f"#{index}:17 {comparison}\n{evidence}" for index in range(200)]
    sections = ["\n".join([title, *lines]), "", "### 旧側\nJ102:24^ 外部::execute key=%A"]
    original = "\n\n".join(filter(None, sections))
    tail = "\n\n### 履歴\n履歴の %A はそのまま残る。"

    packed = shared_fragments(sections) + tail

    table, _, body = packed.partition("\n\n")
    body, end, trailing = body.partition("\n\n### 共通表記の終わり")
    aliases = {}
    for line in table.splitlines():
        key, sep, value = line.partition(" = ")
        if sep and key.startswith("%"):
            aliases[key] = json.loads(value)
    assert aliases
    assert len(packed.encode()) < len((original + tail).encode())
    assert end
    assert trailing == tail
    assert body.startswith(f"{title}\n")
    decoded = [
        line
        if line.startswith("### ")
        else re.sub(
            r"%[A-Za-z%]", lambda match: "%" if match[0] == "%%" else aliases[match[0]], line
        )
        for line in body.split("\n")
    ]
    assert "\n".join(decoded) == original


def test_small_material_fragments_leave_every_row_inline() -> None:
    sections = ["### 変更型", "options: ReviewOptions\noptions: ReviewOptions"]
    assert shared_fragments(sections) == "\n\n".join(sections)
