"""ゲートの結果（違反 G と警告 W）の番号の割り当てと保存。"""

import difflib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from jigane.flaky.catalog import ANNOTATION_ID, Item
from jigane.flaky.checks import Certainty
from jigane.flaky.detector import AnnotationDetection, Detection, QueryDetection, detect
from jigane.flaky.explanation import explain
from jigane.flaky.unreadable_explanation import explanation_of
from jigane.gates.entry import (
    GATES_FORMAT,
    FlakyDetail,
    GateEntry,
    GateKind,
    Gates,
    GateSeverity,
    NumberLedger,
    UnreadableDetail,
)
from jigane.gates.line_scope import AllLines, ChangedLines, LineScope
from jigane.git.tree_files import read_tree_bytes
from jigane.judgment_map.allow_annotation import split_rows
from jigane.judgment_map.classification import Segment
from jigane.judgment_map.unreadable_constructs import Occurrence, unreadable_constructs
from jigane.regex.gate_tags import REGEX_ANNOTATION_ID, annotation_entry, detect_tags, regex_entry
from jigane.storage.atomic_write import write_text_atomic
from jigane.storage.review_access import read_stored_file_if_present
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import require_current_version

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
    if detection.item_id == ANNOTATION_ID:
        return GateKind.VIOLATION
    is_proxy_item = by_id[detection.item_id].confidence == "proxy"
    is_target_undetermined = detection.certainty is Certainty.UNDETERMINED
    if is_proxy_item or is_target_undetermined:
        return GateKind.WARNING
    return GateKind.VIOLATION


def update_gates(  # noqa: PLR0913 -- 呼び側が揃える入力で、束ねる型を新設しても読む手間が増えるだけ
    directory: Path,
    root: Path,
    paths: Sequence[str],
    segments: Sequence[Segment],
    catalog: list[Item],
    *,
    renames: Mapping[str, str],
    base_root: Path | None = None,
    unreadable_segments: Sequence[Segment],
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
    # 読めない記述は行で絞らず、案内と同じ集合を警告 W として載せる。検出する区間は呼び側が渡す
    for item in unreadable_constructs(unreadable_segments, root, base_root).occurrences:
        base = NumberLedger.base_key(item.path, item.kind.entry_id, f"{item.side}\0{item.excerpt}")
        warnings.append(_unreadable_entry(ledger.assign(base, GateKind.WARNING), item))
    gates = Gates(tuple(violations), tuple(warnings), ledger.settle())
    text = json.dumps(gates.to_json(), ensure_ascii=False, indent=2)
    write_text_atomic(directory / GATES_FILE, text)
    return gates


def _unreadable_entry(number: str, item: Occurrence) -> GateEntry:
    """一件の読めない記述から警告の GateEntry を組む。"""
    return GateEntry(
        number,
        item.kind.entry_id,
        item.path,
        item.line,
        item.excerpt,
        explanation_of(item.wording).to_json(),
        GateSeverity.UNREADABLE,
        UnreadableDetail(item.kind, item.side),
    )


def load_gates(directory: Path) -> Gates | None:
    text = read_stored_file_if_present(directory, GATES_FILE)
    if text is None:
        return None
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ReviewError(f"{GATES_FILE} の最上位が辞書ではありません")
    stored = require_current_version(GATES_FILE, raw, current=GATES_FORMAT)
    return Gates.from_json(stored)
