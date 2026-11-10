import hashlib
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from jigane.text.terminal import sanitize_text

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def is_sha256_hex(digest: str) -> bool:
    """sha256 の 16 進（小文字 64 桁）の書式か。"""
    return _SHA256_HEX.fullmatch(digest) is not None


class RecordError(ValueError):
    """配布の RECORD から中身を数えられない。RECORD が無い、載るファイルが無い、読めない。"""


@dataclass(frozen=True)
class FileDigest:
    """RECORD の 1 ファイルの相対パスと、中身の sha256 の 16 進。"""

    path: str
    sha256: str


@dataclass(frozen=True)
class ApprovedContent:
    """承認した時点の配布の中身。files は path の順に並べる。"""

    files: tuple[FileDigest, ...]


@dataclass(frozen=True)
class LegacyApproval:
    """中身を記録する前の形の承認。読めるが、承認としては使わない。"""


Approval = ApprovedContent | LegacyApproval
# 承認の記録を（正規化した配布名, 版）ごとにまとめたもの。同じ組の承認は複数ありうる
ApprovalsByPair = dict[tuple[str, str], set[Approval]]


def _scan_record(dist: metadata.Distribution) -> tuple[dict[str, str], list[tuple[str, OSError]]]:
    """RECORD に載るファイルの sha256 を数える。読めなかったファイルは出会った順に返す。"""
    files = dist.files
    if files is None:
        raise RecordError("配布に RECORD が無く、中身を数えられません")
    hashes: dict[str, str] = {}
    failures: list[tuple[str, OSError]] = []
    for file in files:
        if file.name == "RECORD" and file.parent.name.endswith(".dist-info"):
            continue
        if file.parent.name == "__pycache__" and file.suffix == ".pyc":
            continue
        try:
            body = Path(str(file.locate())).read_bytes()
        except OSError as error:
            failures.append((file.as_posix(), error))
            continue
        hashes[file.as_posix()] = hashlib.sha256(body).hexdigest()
    return hashes, failures


def _unreadable_message(path: str, error: OSError) -> str:
    return f"RECORD に載る {path} を読めません: {error}"


def read_content(dist: metadata.Distribution) -> ApprovedContent:
    """配布の RECORD に載るファイルの相対パスと sha256 を、ディスク上の中身から数える。

    RECORD が持つハッシュは書き換えられるので信じない。RECORD 自身は自分のハッシュを持てないので
    除く。__pycache__ の pyc は import のたびに作られ、承認の後で変わるので除く。
    """
    hashes, failures = _scan_record(dist)
    if failures:
        path, error = failures[0]
        raise RecordError(_unreadable_message(path, error)) from error
    return ApprovedContent(
        tuple(FileDigest(path, digest) for path, digest in sorted(hashes.items()))
    )


# 理由に並べるパスの上限。RECORD は外の配布が決めるので、全部は並べない
_MAX_LISTED_PATHS = 5
_LEGACY_APPROVAL_REASON = "旧い形の承認記録です（中身の照合ができません）"


@dataclass(frozen=True)
class _ContentChange:
    """承認した中身と今の中身の差。パスは昇順。"""

    changed: tuple[str, ...]
    missing: tuple[str, ...]
    added: tuple[str, ...]

    def size(self) -> int:
        return len(self.changed) + len(self.missing) + len(self.added)


def _content_change(
    approved: ApprovedContent, actual: dict[str, str], absent: Iterable[str]
) -> _ContentChange:
    """absent は RECORD に載るがディスクに無いファイルで、欠けたに数える。"""
    before = {file.path: file.sha256 for file in approved.files}
    return _ContentChange(
        changed=tuple(sorted(p for p in before.keys() & actual.keys() if before[p] != actual[p])),
        missing=tuple(sorted((before.keys() - actual.keys()) | set(absent))),
        added=tuple(sorted(actual.keys() - before.keys())),
    )


def _describe_content_change(change: _ContentChange) -> str:
    """変わった、欠けた、増えたファイルのパスを、合わせて最大 5 件まで種類つきで示す。"""
    budget = _MAX_LISTED_PATHS
    parts: list[str] = []
    for label, paths in (
        ("変わった", change.changed),
        ("欠けた", change.missing),
        ("増えた", change.added),
    ):
        shown = paths[:budget]
        budget -= len(shown)
        if shown:
            parts.append(f"{label}: {', '.join(sanitize_text(p) for p in shown)}")
    if change.size() > _MAX_LISTED_PATHS:
        parts.append(f"ほか {change.size() - _MAX_LISTED_PATHS} 件")
    return f"承認のあと、配布の中身が変わっています（{'／'.join(parts)}）"


def reapproval_reason(
    approvals: Iterable[Approval], actual: dict[str, str], absent: Iterable[str] = ()
) -> str:
    """承認し直しを求める理由の文。呼ぶ側は、approvals のどれも今の中身と一致しないと確かめてある。

    中身つきの承認が複数あれば、差の最も少ないものと比べる。
    """
    recorded = [approval for approval in approvals if isinstance(approval, ApprovedContent)]
    if not recorded:
        return _LEGACY_APPROVAL_REASON
    absent = tuple(absent)
    closest = min(
        (_content_change(approved, actual, absent) for approved in recorded),
        key=lambda change: (change.size(), change.changed, change.missing, change.added),
    )
    return _describe_content_change(closest)


def content_error(dist: metadata.Distribution, approvals: Collection[Approval]) -> str | None:
    """配布の今の中身が、その配布と版の承認の記録と違う理由。同じなら None。"""
    if not any(isinstance(approval, ApprovedContent) for approval in approvals):
        # 旧い形の記録は配布を読まずに断る。RECORD の有無や読めないファイルで理由を変えない
        return _LEGACY_APPROVAL_REASON
    try:
        hashes, failures = _scan_record(dist)
    except RecordError as no_record:
        # RECORD の無い配布は、差を示す元の一覧が無い
        return f"配布の中身が承認時と違います（{no_record}）"
    unreadable = [
        (path, error) for path, error in failures if not isinstance(error, FileNotFoundError)
    ]
    if unreadable:
        # ディスクにあるのに読めないファイルは、変わった、欠けた、増えたのどれにも数えない
        path, error = unreadable[0]
        return f"配布の中身が承認時と違います（{_unreadable_message(path, error)}）"
    absent = [path for path, _ in failures]
    actual = ApprovedContent(
        tuple(FileDigest(path, digest) for path, digest in sorted(hashes.items()))
    )
    if not absent and actual in approvals:
        return None
    return reapproval_reason(approvals, hashes, absent)
