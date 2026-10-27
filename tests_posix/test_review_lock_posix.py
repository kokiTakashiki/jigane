"""錠が無いと書き込みが失われることを、POSIX の原子的な置き換えで確かめる。

Windows の os.replace は、置き換え先に別の置き換えが重なると WinError 5 で落ち、
同じ現象がこの形では出ない。
"""

import threading
from functools import partial
from pathlib import Path

from test_review_lock import _overlapping_merge, _run_in_threads

from jigane.findings.records import load_findings_state


def test_without_lock_a_group_is_lost(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    _run_in_threads(
        [
            partial(_overlapping_merge, tmp_path, g, locked=False, barrier=barrier)
            for g in ("macro", "security")
        ]
    )
    assert len(load_findings_state(tmp_path).groups) == 1
