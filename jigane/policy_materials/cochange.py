"""git の履歴から、一緒に変わったファイルの対を数える。

数え方は code-maat に倣う。マージのコミットと、多くのファイルを変えたコミットは数えない。
移動したファイルは、移動の前のパスを移動の後のパスへ寄せて一つに数える。
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from jigane.git import process

# これを超える数のファイルを変えたコミットは、一括の整形や移動なので数えない
MAX_FILES_PER_COMMIT = 30

# 表に残す対の下限。既定値は code-maat の coupling 解析の既定値（強さは 30%）に倣う
# https://github.com/adamtornhill/code-maat#readme
MIN_REVISIONS = 5
MIN_SHARED_COMMITS = 5
MIN_STRENGTH = 0.30

# 表に出す行の上限。超えた分は強い順に切る
MAX_ROWS = 20

_RECORD_START = "\x1e"
_RENAME_OR_COPY = ("R", "C")
# 移動とコピーの行は、状態、移動の前、移動の後の三つ組
_RENAME_FIELD_COUNT = 3


@dataclass(frozen=True)
class CoChange:
    """起点のファイルと相手のファイルが、一緒に変わった回数と強さ。"""

    origin: str
    partner: str
    shared_commits: int
    strength: float
    origin_revisions: int
    partner_revisions: int


def _history_records(git_dir: Path, revision: str) -> list[list[str]]:
    """revision から辿れるコミットを新しい順に、各コミットが触れたパスの並びで返す。"""
    output = process.run_command(
        [
            "git",
            "-c",
            "core.quotepath=off",
            "log",
            "--no-merges",
            "-M",
            "--name-status",
            # 制御文字は引数に直接渡せないので、git に展開させる
            "--format=%x1e",
            revision,
            # revision がファイル名と取り違えられないよう区切る
            "--",
        ],
        cwd=git_dir,
    )
    return [
        [line for line in block.splitlines() if line] for block in output.split(_RECORD_START)[1:]
    ]


def _commit_files(lines: list[str], canonical: dict[str, str]) -> set[str]:
    """コミットが触れたファイルを、最新のパスに寄せて返す。canonical に移動の対応を足す。"""
    files: set[str] = set()
    for line in lines:
        fields = line.split("\t")
        status = fields[0]
        if status.startswith(_RENAME_OR_COPY) and len(fields) == _RENAME_FIELD_COUNT:
            old_path, new_path = fields[1], fields[2]
            latest = canonical.get(new_path, new_path)
            if status.startswith("R"):
                # 古い側のコミットを読むときに、移動の前のパスが移動の後のパスへ寄る
                canonical[old_path] = latest
            files.add(latest)
        else:
            path = fields[-1]
            files.add(canonical.get(path, path))
    return files


def count_co_changes(
    git_dir: Path,
    origins: frozenset[str],
    *,
    revision: str = "HEAD",
    max_files_per_commit: int = MAX_FILES_PER_COMMIT,
) -> list[CoChange]:
    """起点のファイルごとに、一緒に変わった相手を数える。

    強さは、一緒に変わった回数を、二つのファイルの変更回数の平均で割った値。
    変更回数は、数えるコミットの中での回数。数えるのは revision から辿れる履歴だけ。
    並びは強さ、回数の降順で、同点は名前順。
    """
    canonical: dict[str, str] = {}
    commits: list[set[str]] = []
    for lines in _history_records(git_dir, revision):
        files = _commit_files(lines, canonical)
        if len(files) > max_files_per_commit:
            # 数えない。ただし移動の対応は上の呼び出しで足し終えており、古い側へ引き継がれる
            continue
        commits.append(files)
    revisions: Counter[str] = Counter(path for files in commits for path in files)
    shared: Counter[tuple[str, str]] = Counter()
    for files in commits:
        for origin in files & origins:
            for partner in files - {origin}:
                shared[(origin, partner)] += 1
    rows = [
        CoChange(
            origin,
            partner,
            count,
            count / ((revisions[origin] + revisions[partner]) / 2),
            revisions[origin],
            revisions[partner],
        )
        for (origin, partner), count in shared.items()
    ]
    return sorted(
        rows, key=lambda row: (-row.strength, -row.shared_commits, row.origin, row.partner)
    )


def select_strong(
    rows: list[CoChange],
    *,
    min_revisions: int = MIN_REVISIONS,
    min_shared_commits: int = MIN_SHARED_COMMITS,
    min_strength: float = MIN_STRENGTH,
    max_rows: int = MAX_ROWS,
) -> list[CoChange]:
    """変更回数、共変更の回数、強さの下限を満たす対を、渡された並びのまま上限の行数まで残す。

    変更回数の下限は、起点と相手の両方に掛ける。
    """
    return [
        row
        for row in rows
        if min(row.origin_revisions, row.partner_revisions) >= min_revisions
        and row.shared_commits >= min_shared_commits
        and row.strength >= min_strength
    ][:max_rows]
