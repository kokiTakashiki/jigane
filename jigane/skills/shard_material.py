"""通常の差分に提示した新側の根拠行を索引にする。"""

import re
from collections.abc import Sequence

from jigane.markdown.fence import one_line
from jigane.skills import shards

_HUNK = re.compile(r"@@ -\d+(?:,(?P<old_count>\d+))? \+(?P<start>\d+)(?:,(?P<new_count>\d+))? @@")

type EvidenceLines = dict[str, dict[int, str]]


def presented_lines(units: Sequence[shards.Unit], changes: str) -> EvidenceLines:
    """発見時の差分と検証に送る保存差分が一致するファイルの行だけを返す。"""
    result: EvidenceLines = {}
    # 変更の節の最初の柵にある差分だけを照合する。後続の設定冒頭は含めない。
    opening, _, rest = changes.partition("\n```")
    diff = ""
    if rest:
        extra_ticks, _, fenced = rest.partition("\n")
        if not opening.endswith("\n") or extra_ticks.strip("`"):
            return result
        diff = "\n" + fenced.partition("\n```" + extra_ticks)[0]
    for unit in units:
        section = "\n".join(unit.diff_lines)
        if f"\n{section}\n" in diff:
            result[unit.path] = _new_lines(unit)
    return result


def _new_lines(unit: shards.Unit) -> dict[int, str]:
    result: dict[int, str] = {}
    lines = unit.diff_lines[2:]
    cursor = 0
    while cursor < len(lines):
        header = lines[cursor]
        match = _HUNK.fullmatch(header)
        if match is None:
            raise ValueError(f"{one_line(unit.path)}: ハンクの見出しを確認できません")
        position = int(match["start"])
        old, new = int(match["old_count"] or 1), int(match["new_count"] or 1)
        cursor += 1
        while cursor < len(lines) and (old or new):
            line = lines[cursor]
            if not line or line[0] not in " +-":
                break
            old -= line[0] != "+"
            if line[0] != "-":
                new -= 1
                result[position] = line[1:]
                position += 1
            cursor += 1
        if old or new:
            raise ValueError(f"{one_line(unit.path)} {header}: 旧新行の数が一致しません")
    return result
