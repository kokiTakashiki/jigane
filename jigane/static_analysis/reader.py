"""SARIF のファイルを、上限の中で読み、検証して、正規化した指摘にする。"""

import hashlib
import json
import os
import stat
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from jigane.languages.plugins import sanitize_text
from jigane.static_analysis.models import (
    DropReason,
    SarifDriver,
    SarifFileRecord,
    SarifImport,
    SarifLog,
    SarifResult,
    StaticFinding,
)
from jigane.static_analysis.paths import resolve_uri

_MEGABYTE = 1024 * 1024


class SarifError(ValueError):
    """SARIF を読み込まずに止める誤り。文は、どのファイルの何が原因かを含む。"""


@dataclass(frozen=True)
class SarifLimits:
    max_files: int = 20
    max_file_bytes: int = 20 * _MEGABYTE
    max_total_bytes: int = 50 * _MEGABYTE
    max_findings: int = 10_000
    max_tool_name: int = 100
    max_tool_version: int = 100
    max_rule_id: int = 200
    max_message: int = 2_000
    max_revision_id: int = 200


DEFAULT_LIMITS = SarifLimits()


@dataclass
class _Roots:
    analysis: Path
    repository: Path


def read_sarif(
    sarif_paths: Sequence[Path],
    analysis_root: Path,
    repository_root: Path,
    limits: SarifLimits = DEFAULT_LIMITS,
) -> SarifImport:
    """SARIF を読み、指摘を渡したファイル、run、結果の順に S1、S2… と番号を振って返す。

    二つの根は絶対パスで渡す。変更したファイルでは絞らない。
    """
    _check_before_reading(sarif_paths, limits)
    roots = _Roots(analysis_root, repository_root)
    findings: list[StaticFinding] = []
    dropped: Counter[DropReason] = Counter()
    records: list[SarifFileRecord] = []
    result_count = 0
    remaining = limits.max_total_bytes
    for path in sarif_paths:
        data = _read_capped(path, min(limits.max_file_bytes, remaining))
        remaining -= len(data)
        log = _parse(path, data)
        result_count += sum(len(run.results or []) for run in log.runs)
        if result_count > limits.max_findings:
            raise SarifError(
                f"SARIF の指摘の合計が {limits.max_findings} 件を超えました（{path} まで）"
            )
        for run in log.runs:
            for result in run.results or []:
                outcome = _normalize(result, run.tool.driver, roots, limits, len(findings) + 1)
                if isinstance(outcome, DropReason):
                    dropped[outcome] += 1
                else:
                    findings.append(outcome)
        records.append(
            SarifFileRecord(
                path=str(path),
                sha256=hashlib.sha256(data).hexdigest(),
                revision_ids=_revision_ids(log, limits),
            )
        )
    return SarifImport(findings=tuple(findings), dropped=dict(dropped), files=tuple(records))


def _check_before_reading(sarif_paths: Sequence[Path], limits: SarifLimits) -> None:
    if len(sarif_paths) > limits.max_files:
        raise SarifError(
            f"SARIF は {limits.max_files} ファイルまでです"
            f"（{len(sarif_paths)} ファイル渡されました）"
        )
    total = 0
    for path in sarif_paths:
        total += _size_of_regular_file(path, limits.max_file_bytes)
        if total > limits.max_total_bytes:
            raise SarifError(
                f"SARIF の大きさの合計が {limits.max_total_bytes} バイトを超えました"
                f"（{path} まで）"
            )


def _revision_ids(log: SarifLog, limits: SarifLimits) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for run in log.runs:
        for provenance in run.version_control_provenance or []:
            if provenance.revision_id is not None:
                found[_clean(provenance.revision_id, limits.max_revision_id)] = None
    return tuple(found)


def _size_of_regular_file(path: Path, max_bytes: int) -> int:
    try:
        status = path.stat()
    except OSError as error:
        raise SarifError(f"{path} を調べられません: {error.strerror or error}") from error
    _require_regular(path, status.st_mode)
    if status.st_size > max_bytes:
        raise SarifError(f"{path} は {max_bytes} バイトを超えています（{status.st_size} バイト）")
    return status.st_size


def _require_regular(path: Path, mode: int) -> None:
    if not stat.S_ISREG(mode):
        raise SarifError(
            f"{path} は通常のファイルではありません（FIFO、デバイス、ディレクトリは読みません）"
        )


def _read_capped(path: Path, limit: int) -> bytes:
    # O_NONBLOCK は、確かめた後に FIFO へ差し替えられても open が書き手を待たないための指定
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SarifError(f"{path} を開けません: {error.strerror or error}") from error
    with os.fdopen(descriptor, "rb") as stream:
        _require_regular(path, os.fstat(stream.fileno()).st_mode)
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise SarifError(f"{path} は読み込みの上限（{limit} バイト）を超えています")
    return data


def _parse(path: Path, data: bytes) -> SarifLog:
    try:
        document = json.loads(data)
    except RecursionError as error:
        raise SarifError(f"{path} は JSON の入れ子が深すぎて読めません") from error
    except ValueError as error:
        # JSONDecodeError と UnicodeDecodeError は ValueError の下にある
        raise SarifError(f"{path} は JSON として読めません: {error}") from error
    try:
        return SarifLog.model_validate(document)
    except ValidationError as error:
        # 入力の値は文に入れない。巨大な値や制御文字が端末に出るのを避けるため
        first = error.errors(include_input=False, include_url=False)[0]
        where = ".".join(str(part) for part in first["loc"])
        raise SarifError(
            f"{path} の SARIF が型に合いません"
            f"（{error.error_count()} 件、最初は {where}: {first['msg']}）"
        ) from error


def _clean(text: str, limit: int) -> str:
    return sanitize_text(text)[:limit]


def _normalize(
    result: SarifResult,
    driver: SarifDriver,
    roots: _Roots,
    limits: SarifLimits,
    number: int,
) -> StaticFinding | DropReason:
    location = result.locations[0] if result.locations else None
    physical = location.physical_location if location else None
    uri = physical.artifact_location.uri if physical and physical.artifact_location else None
    region = physical.region if physical else None
    if uri is None or region is None or region.start_line is None:
        return DropReason.NO_LOCATION
    path = resolve_uri(uri, roots.analysis, roots.repository)
    if isinstance(path, DropReason):
        return path
    return StaticFinding(
        id=f"S{number}",
        tool_name=_clean(driver.name, limits.max_tool_name),
        tool_version=_clean(driver.version or "", limits.max_tool_version),
        rule_id=_clean(result.rule_id or "", limits.max_rule_id),
        level=result.level or "warning",
        message=_clean(result.message.text, limits.max_message),
        path=path,
        start_line=region.start_line,
        end_line=max(region.start_line, region.end_line or region.start_line),
    )
