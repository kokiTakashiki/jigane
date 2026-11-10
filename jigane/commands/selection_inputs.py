"""採否の入口が、レビューの錠の中で呼ぶ読み関数を組み立てる。"""

from pathlib import Path

from jigane.findings.records import load_history_checked
from jigane.reviews import selection as selection_module
from jigane.skills import pipeline


def past_round_reader(directory: Path) -> selection_module.PastRoundReader:
    """採否の操作がレビューの錠の中で呼ぶ、履歴と群ごとの今の結果の実行時刻を読む関数。"""

    def read() -> selection_module.PastRoundInputs:
        ran_at = {group: run.ran_at for group, run in pipeline.read_runs(directory).items()}
        return selection_module.PastRoundInputs(load_history_checked(directory), ran_at)

    return read
