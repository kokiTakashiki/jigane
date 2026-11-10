"""git に位置引数として渡せるコミットの型。"""

import re
from dataclasses import dataclass
from pathlib import Path

from jigane.git import process

_COMMIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class CommitSha:
    """形を確かめた完全なコミットの SHA。どの経路で作っても形が合わなければ ValueError。"""

    value: str

    def __post_init__(self) -> None:
        if _COMMIT_SHA_PATTERN.fullmatch(self.value) is None:
            raise ValueError(
                f"コミットの SHA（16 進の 40 桁か 64 桁）ではありません: {self.value[:80]!r}"
            )

    def __str__(self) -> str:
        return self.value


def parse_commit_sha(label: str, text: str) -> CommitSha:
    """16 進の 40 桁か 64 桁なら CommitSha を返し、合わなければ ValueError を投げる。"""
    try:
        return CommitSha(text)
    except ValueError:
        # repr は改行と制御文字を逸らすので、外から来た値を一行のまま表に出せる
        raise ValueError(
            f"{label} がコミットの SHA（16 進の 40 桁か 64 桁）ではありません: {text[:80]!r}"
        ) from None


def resolve_commit_sha(git_dir: Path, revision: str) -> CommitSha:
    """ref 名や `<SHA>~9` のような式を、完全なコミットの SHA に解く。解けなければ CommandError。"""
    output = process.run_command(
        ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"],
        cwd=git_dir,
    )
    return parse_commit_sha("rev-parse の出力", output.strip())
