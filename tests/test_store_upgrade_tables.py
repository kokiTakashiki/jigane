"""保存物ごとの移しの表の鍵が、1 から今の版の一つ手前まで揃っていることを縛る。"""

from collections.abc import Callable, Mapping
from typing import Any

import pytest
from store_format_support import check_upgrades

from jigane.pull_request_context import background, bots
from jigane.reviews import review_formats, selection
from jigane.skills import run_record

type Table = Mapping[int, Callable[[Any], dict[str, Any]]]

CASES: list[tuple[str, int, Table]] = [
    ("selection", selection.SELECTION_FORMAT, selection._UPGRADES),
    ("background", background.BACKGROUND_FORMAT, background._UPGRADES),
    ("bots", bots.BOT_COMMENTS_FORMAT, bots._UPGRADES),
    ("review", review_formats.REVIEW_FORMAT, review_formats._REVIEW_UPGRADES),
    ("regions", review_formats.REGIONS_FORMAT, review_formats._REGIONS_UPGRADES),
    ("pairs", review_formats.PAIRS_FORMAT, review_formats._PAIRS_UPGRADES),
    ("in_progress", run_record.IN_PROGRESS_FORMAT, run_record._IN_PROGRESS_UPGRADES),
    ("stopped", run_record.STOPPED_FORMAT, run_record._STOPPED_UPGRADES),
]


@pytest.mark.parametrize(("name", "current", "table"), CASES, ids=[case[0] for case in CASES])
def test_upgrade_table_keys_are_complete(name: str, current: int, table: Table) -> None:
    check_upgrades(name, current, table)
