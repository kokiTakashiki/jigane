"""群の結果を書く保存物の一覧と、群の結果だけを消す口。

どの保存物が群の結果を持つかはここで持つ。保存物を足す書き手は、この一覧にも足す。
"""

from pathlib import Path

from jigane.findings.records import (
    FINDINGS_FILE,
    HISTORY_FILE,
    IN_PROGRESS_FILE,
    RUNS_FILE,
    read_stored_state,
    save_findings_state,
)
from jigane.groups.definitions import GroupId
from jigane.reviews.selection import SELECTION_FILE

# 群を選ばず、ファイルごと消す保存物。履歴、selection、runs の群の記録、進行中の印
WHOLE_FILE_STATE = (HISTORY_FILE, SELECTION_FILE, RUNS_FILE, IN_PROGRESS_FILE)


def clear_group_state(directory: Path, group: GroupId) -> None:
    """directory から group の指摘、stale の印、履歴、selection、runs、進行中の印を消す。

    findings.json は他の群の指摘を持つため、group の部分だけを消す。
    """
    if (directory / FINDINGS_FILE).is_file():
        save_findings_state(directory, read_stored_state(directory).without_group(group))
    for name in WHOLE_FILE_STATE:
        (directory / name).unlink(missing_ok=True)
