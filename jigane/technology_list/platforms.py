"""配備の下限（最低対応 OS や実行環境の版）を、変更の前後で比べて一覧にする。

読み取りは `manifests.py` の抽出器が担い、ここは比べて変化を付ける。
"""

from collections.abc import Sequence

from jigane.technology_list.changes import change_of
from jigane.technology_list.manifests import Platform


def _grouped(items: Sequence[Platform]) -> dict[tuple[str, str], list[str]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for item in items:
        groups.setdefault((item.path, item.platform), []).append(item.minimum)
    return groups


def model(before: Sequence[Platform] | None, after: Sequence[Platform]) -> list[dict[str, object]]:
    """配備の下限の一覧。値の変化は change に入れる。変更前のツリーが無ければ None。"""
    old = None if before is None else _grouped(before)
    new = _grouped(after)
    result: list[dict[str, object]] = []
    for key in sorted(set(new) | set(old or {})):
        path, platform = key
        current = new.get(key, [])
        previous = None if old is None else old.get(key, [])
        if previous is None:
            # 機能レビュー式には変更前が無く、比べられない
            change = None
        else:
            # 下限を持つ対象は、前後とも変わらないものも一覧に残す
            change = change_of(
                had_before=bool(previous), has_after=bool(current), equal=current == previous
            )
        minimums: list[str | None] = [*current] or [None]
        # 消えた対象は下限が無いので、minimum が None の一行で表す
        result.extend(
            {
                "path": path,
                "platform": platform,
                "minimum": minimum,
                "before_minimum": ", ".join(previous) if previous else None,
                "change": change,
            }
            for minimum in minimums
        )
    return result
