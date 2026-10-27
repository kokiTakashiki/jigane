"""付属の文書の本文と、保存した版との食い違いの注意。review.py にも design/ にも依存しない。

古さの事実の出所は AttachmentContent.stale と VersionDrift の列だけ。
利用者に見せる文は drift_notice が組む。
"""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class AttachmentContent:
    """付属の文書の、いまの作業ディレクトリでの本文。保存はしない。"""

    # リポジトリのルートからの posix の相対パス
    path: str
    text: str
    # 保存した版と、いまの本文の版が違うか
    stale: bool


class DriftedRole(StrEnum):
    """版が食い違った文書の役。値は注意の文にそのまま入る。"""

    MAIN = "設計書"
    ATTACHMENT = "付属の文書"


@dataclass(frozen=True)
class VersionDrift:
    """保存した版といまの本文の版が違う文書。実行を止めず、利用者へ知らせる。"""

    review_id: str
    role: DriftedRole
    path: str


def drift_notice(drifts: tuple[VersionDrift, ...]) -> str | None:
    """食い違いの列から、利用者に見せる注意の文を組む唯一の置き場。無ければ None。"""
    if not drifts:
        return None
    return "。".join(
        f"{drift.role.value} {drift.path} が保存した版と違います。いまの本文で問います。"
        f"版を取り直すには jigane refresh {drift.review_id} を実行してください"
        for drift in drifts
    )
