"""ゲートの結果（違反 G と警告 W）の番号の割り当てと保存。"""

import difflib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jigane.flaky.catalog import ANNOTATION_ID, Item, load_catalog
from jigane.flaky.detector import AnnotationDetection, Detection, QueryDetection, detect
from jigane.flaky.explanation import explain
from jigane.flaky.gates_v1 import upgrade_v1
from jigane.gates.entry import (
    GATES_FORMAT,
    FlakyDetail,
    GateEntry,
    GateKind,
    Gates,
    GateSeverity,
    NumberLedger,
)
from jigane.gates.line_scope import AllLines, ChangedLines, LineScope
from jigane.git.tree_files import read_tree_bytes
from jigane.judgment_map.allow_annotation import split_rows
from jigane.judgment_map.classification import Segment
from jigane.regex.gate_tags import REGEX_ANNOTATION_ID, annotation_entry, detect_tags, regex_entry
from jigane.storage.store import write_text_atomic
from jigane.storage.store_format import Upgrade, upgrade_mapping

GATES_FILE = "gates.json"


def _read_lines(root: Path, path: str) -> list[str] | None:
    source = read_tree_bytes(root, path)
    return None if source is None else split_rows(source.decode("utf-8", errors="replace"))


def _changed_lines(segments: Sequence[Segment], base_root: Path, root: Path) -> ChangedLines:
    """変更後の側で実際に変えた行（追加と置き換え）。追加したファイルは全体。"""
    old_paths = {s.new_path: s.old_path for s in segments if s.new_path is not None}
    changed: dict[str, frozenset[int]] = {}
    for path, old_path in old_paths.items():
        new_lines = _read_lines(root, path)
        if new_lines is None:
            continue
        old_lines = (_read_lines(base_root, old_path) if old_path is not None else None) or []
        rows: set[int] = set()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
        for tag, _, _, new_start, new_end in matcher.get_opcodes():
            if tag in ("insert", "replace"):
                rows.update(range(new_start + 1, new_end + 1))
        changed[path] = frozenset(rows)
    return ChangedLines(changed)


def _entry(number: str, detection: Detection, by_id: dict[str, Item]) -> GateEntry:
    explanation = explain(
        detection.item_id,
        by_id,
        detection.spec if isinstance(detection, QueryDetection) else None,
    ).to_json()
    match detection:
        case QueryDetection(spec=spec):
            item = by_id[detection.item_id]
            detail = FlakyDetail(
                reason=detection.reason,
                cause=item.cause,
                confidence=item.confidence,
                origin=item.source,
                legitimate=item.legitimate,
                detector_note=spec.note or "",
                equivalent=spec.equivalent.to_entry(),
            )
        case AnnotationDetection():
            # 注記の違反は目録の項目も query も持たないので、相当する規則の欄は出さない
            detail = FlakyDetail(
                reason=detection.reason,
                cause="Annotation",
                confidence="direct",
                origin="jigane: allow-flaky の注記の書式",
                legitimate="",
            )
    return GateEntry(
        number,
        detection.item_id,
        detection.path,
        detection.line,
        detection.text,
        explanation,
        GateSeverity.FLAKY,
        detail,
    )


def gate_kind(detection: Detection, by_id: Mapping[str, Item]) -> GateKind:
    """兆候の型と、宛先を確かめられない当たりは警告 W で、それ以外は違反 G。"""
    if detection.item_id != ANNOTATION_ID and (
        by_id[detection.item_id].confidence == "proxy" or detection.undetermined
    ):
        return GateKind.WARNING
    return GateKind.VIOLATION


def update_gates(
    directory: Path,
    root: Path,
    paths: Sequence[str],
    segments: Sequence[Segment],
    catalog: list[Item],
    *,
    renames: Mapping[str, str],
    base_root: Path | None = None,
) -> Gates:
    """検出し直して番号を割り当て、gates.json に保存する。番号は再利用しない。"""
    previous = load_gates(directory)
    ledger = (previous.ledger if previous is not None else NumberLedger()).allocator()
    scope: LineScope = (
        _changed_lines(segments, base_root, root) if base_root is not None else AllLines()
    )
    by_id = {item.id: item for item in catalog}
    violations: list[GateEntry] = []
    warnings: list[GateEntry] = []
    for detection in detect(root, paths, catalog, scope):
        base = NumberLedger.base_key(detection.path, detection.item_id, detection.text)
        kind = gate_kind(detection, by_id)
        entry = _entry(ledger.assign(base, kind), detection, by_id)
        (warnings if kind is GateKind.WARNING else violations).append(entry)
    # 正規表現の札は違反 G と同じ番号の列に載せる。区分は札の強さが持つので W にはならない
    scan = detect_tags(root, paths, renames, base_root, scope)
    for found in scan.detections:
        literal = found.literal
        base = NumberLedger.base_key(literal.path, found.tag.rule, literal.pattern)
        violations.append(regex_entry(ledger.assign(base, GateKind.VIOLATION), found))
    for annotation in scan.violations:
        base = NumberLedger.base_key(annotation.path, REGEX_ANNOTATION_ID, annotation.excerpt)
        violations.append(annotation_entry(ledger.assign(base, GateKind.VIOLATION), annotation))
    gates = Gates(tuple(violations), tuple(warnings), ledger.settle())
    text = json.dumps(gates.to_json(), ensure_ascii=False, indent=2)
    write_text_atomic(directory / GATES_FILE, text)
    return gates


def _upgrade_gates_v1(state: dict[str, Any]) -> dict[str, Any]:
    """目録は版 1 のファイルを読むときだけ引く。版 2 のファイルでは読み込まない。"""
    return upgrade_v1(state, {item.id: item for item in load_catalog()})


# 保存された版から次の版への移し。load_gates が GATES_FORMAT まで順に回す
_UPGRADES: dict[int, Upgrade] = {1: _upgrade_gates_v1}


def load_gates(directory: Path) -> Gates | None:
    path = directory / GATES_FILE
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    stored = upgrade_mapping(GATES_FILE, raw, current=GATES_FORMAT, upgrades=_UPGRADES)
    # upgrade_mapping は format を外して返す。Gates.from_json は format を検査するため戻す
    return Gates.from_json({**stored, "format": GATES_FORMAT})
