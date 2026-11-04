"""保存を重ねる試験の支え。読みを揃えてから保存する経路を、錠の有無で組み分ける。"""

import threading
from collections.abc import Callable
from pathlib import Path

from review_fakes import stored_finding

from jigane.findings.records import read_stored_state, save_findings_state
from jigane.groups.definitions import GroupId
from jigane.storage.store import review_lock


def run_in_threads(targets: list[Callable[[], object]]) -> None:
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def overlapping_merge(
    directory: Path, group: str, *, locked: bool, barrier: threading.Barrier | None
) -> None:
    """読んだ後、保存の前に、バリアで他方の読みを待つ保存。ロックが無ければ二つの読みは必ず重なる。"""

    def save() -> None:
        state = read_stored_state(directory)
        if barrier is not None:
            barrier.wait(timeout=10)
        state.groups[GroupId(group)] = (
            stored_finding(
                {
                    "id": group,
                    "severity": "warning",
                    "viewpoint": "M2",
                    "claim": "C",
                    "triage": "must_read",
                }
            ),
        )
        save_findings_state(directory, state)

    if locked:
        with review_lock(directory):
            save()
    else:
        save()
