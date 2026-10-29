"""SARIF を副コマンドから取り込み、static_findings.json に保存し、地図に重ねる指摘へ直す。"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jigane.git.repository import identify_git
from jigane.judgment_map.classification import FindingTouch
from jigane.static_analysis.models import SarifFileRecord, SarifImport, StaticFinding
from jigane.static_analysis.reader import SarifError, read_sarif
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import Upgrade, upgrade_mapping

STATIC_FINDINGS_FILE = "static_findings.json"
# static_findings.json の形の版。形を変えたら一つ上げ、_UPGRADES に前の版からの移しを足す
STATIC_FINDINGS_FORMAT = 1
RAISING_LEVELS = frozenset({"error", "warning"})


@dataclass(frozen=True)
class SarifRequest:
    """副コマンドへ渡された SARIF と、二つの根（絶対パス）。"""

    paths: tuple[Path, ...]
    analysis_root: Path
    repository_root: Path


def sarif_request(
    sarif: Sequence[Path] | None, sarif_root: Path | None, working_directory: Path
) -> SarifRequest | None:
    """引数から要求を作る。--sarif が無ければ None。既定の解析の根は git のリポジトリの根。"""
    if not sarif:
        if sarif_root is not None:
            raise ReviewError("--sarif-root は --sarif と一緒に渡してください")
        return None
    repository = identify_git(working_directory)
    repository_root = repository.root if repository is not None else working_directory.resolve()
    analysis_root = (
        (working_directory / sarif_root).resolve() if sarif_root is not None else repository_root
    )
    paths = tuple(working_directory / path for path in sarif)
    return SarifRequest(paths, analysis_root, repository_root)


@dataclass(frozen=True)
class StaticFindingsStore:
    """保存する指摘。元の SARIF は持たない。dropped は 6.4 の表示のために件数を残す。"""

    analyzed_commit: str
    files: tuple[SarifFileRecord, ...]
    dropped: Mapping[str, int]
    findings: tuple[StaticFinding, ...]

    def touches(self) -> list[FindingTouch]:
        return [
            FindingTouch(
                finding.id,
                finding.path,
                finding.start_line,
                finding.end_line,
                finding.level in RAISING_LEVELS,
            )
            for finding in self.findings
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": STATIC_FINDINGS_FORMAT,
            "analyzed_commit": self.analyzed_commit,
            "files": [item.model_dump(mode="json") for item in self.files],
            "dropped": dict(self.dropped),
            "findings": [item.model_dump(mode="json") for item in self.findings],
        }


@dataclass(frozen=True)
class NoStatic:
    """SARIF を渡しておらず、保存した指摘も無い。"""


@dataclass(frozen=True)
class StoredStatic:
    """保存した指摘が変更後のコミットと一致するので、書き換えずに重ねる。"""

    store: StaticFindingsStore


@dataclass(frozen=True)
class ImportedStatic:
    """SARIF を読んだ。保存し、重ねる。新規作成と --sarif での置き換えがこれにあたる。"""

    store: StaticFindingsStore
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StaleStatic:
    """保存した指摘が別のコミットのものなので、重ねず、書き換えもしない。"""

    warnings: tuple[str, ...]


# 一回の組み立てで使う指摘。種類は isinstance か match で分ける
StaticSelection = NoStatic | StoredStatic | ImportedStatic | StaleStatic

NO_STATIC = NoStatic()


def static_touches(selection: StaticSelection) -> list[FindingTouch]:
    """地図に重ねる指摘。"""
    match selection:
        case StoredStatic(store) | ImportedStatic(store):
            return store.touches()
        case NoStatic() | StaleStatic():
            return []


def static_warnings(selection: StaticSelection) -> tuple[str, ...]:
    match selection:
        case ImportedStatic(_, warnings) | StaleStatic(warnings):
            return warnings
        case NoStatic() | StoredStatic():
            return ()


def static_to_save(selection: StaticSelection) -> StaticFindingsStore | None:
    """static_findings.json へ書く指摘。None は書かない。"""
    match selection:
        case ImportedStatic(store):
            return store
        case NoStatic() | StoredStatic() | StaleStatic():
            return None


def import_sarif(request: SarifRequest, commit: str) -> ImportedStatic:
    """SARIF を読み、revisionId を変更後のコミットと照らす。違えば ReviewError で止める。"""
    try:
        imported: SarifImport = read_sarif(
            request.paths, request.analysis_root, request.repository_root
        )
    except SarifError as error:
        raise ReviewError(f"SARIF を読み込めません: {error}") from error
    warnings: list[str] = []
    for record in imported.files:
        if not record.revision_ids:
            warnings.append(
                f"SARIF {record.path} に revisionId が無く、解析したコミットを確かめられません。"
                f"変更後のコミット {commit[:8]} を解析したコミットとして保存します"
            )
            continue
        mismatched = [revision for revision in record.revision_ids if revision != commit]
        if mismatched:
            raise ReviewError(
                f"SARIF {record.path} の revisionId（{mismatched[0]}）が、レビューの変更後の"
                f"コミット（{commit}）と違います。そのコミットで走らせた SARIF を渡してください"
            )
    store = StaticFindingsStore(
        commit,
        imported.files,
        {str(reason): count for reason, count in imported.dropped.items()},
        imported.findings,
    )
    return ImportedStatic(store, tuple(warnings))


def select_for_refresh(
    directory: Path, commit: str, replacement: ImportedStatic | None
) -> StaticSelection:
    """作り直しで使う指摘。置き換えがあればそれ、無ければ保存した指摘をコミット一致のときだけ重ねる。"""
    if replacement is not None:
        return replacement
    stored = read_static_findings(directory)
    if stored is None:
        return NO_STATIC
    if stored.analyzed_commit == commit:
        return StoredStatic(stored)
    return StaleStatic(
        (
            (
                f"静的検査の指摘は {stored.analyzed_commit[:8]} を解析したもので、"
                f"変更後のコミット {commit[:8]} とは違うため重ねていません。"
                "新しいコミットで走らせた SARIF を --sarif で渡し直してください"
            ),
        ),
    )


# 版 1 が最初の版なので、移しはまだ無い
_UPGRADES: dict[int, Upgrade] = {}


def load_static_findings(raw: Mapping[str, Any]) -> StaticFindingsStore:
    """static_findings.json を読む境の一か所。保存された版から今の版まで移してから型へ読む。"""
    stored = upgrade_mapping(
        STATIC_FINDINGS_FILE, raw, current=STATIC_FINDINGS_FORMAT, upgrades=_UPGRADES
    )
    try:
        return StaticFindingsStore(
            stored["analyzed_commit"],
            tuple(SarifFileRecord.model_validate(item) for item in stored["files"]),
            dict(stored["dropped"]),
            tuple(StaticFinding.model_validate(item) for item in stored["findings"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewError(f"{STATIC_FINDINGS_FILE} の中身が読めません: {error!r}") from error


def read_static_findings(directory: Path) -> StaticFindingsStore | None:
    """置き場の static_findings.json を読む。SARIF を渡さなかったレビューは None。"""
    path = directory / STATIC_FINDINGS_FILE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReviewError(f"{STATIC_FINDINGS_FILE} が読めません: {error!r}") from error
    if not isinstance(raw, dict):
        raise ReviewError(f"{STATIC_FINDINGS_FILE} の最上位が辞書ではありません")
    return load_static_findings(raw)
