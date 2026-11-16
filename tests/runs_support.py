"""今の版の runs.json を試験が直接書くための補助。"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jigane.findings.records import RUNS_FILE
from jigane.groups.definitions import GroupId
from jigane.llm_runs.launch_failure import RunRecordKind
from jigane.skills.pipeline import RUNS_FORMAT


def with_record_kind(group: str, run: Mapping[str, Any]) -> dict[str, Any]:
    """方針の群の記録に、成功した回の種類を補う。run が種類を持てばそれを使う。"""
    if group != GroupId.MACRO:
        return dict(run)
    return {"record_kind": RunRecordKind.SUCCEEDED.value, **run}


def write_runs_json(directory: Path, runs: Mapping[str, Any]) -> None:
    """群ごとの記録を、今の版の runs.json として書く。方針の群の記録の種類は成功した回で補う。"""
    stamped = {group: with_record_kind(group, run) for group, run in runs.items()}
    (directory / RUNS_FILE).write_text(
        json.dumps({"format": RUNS_FORMAT, **stamped}), encoding="utf-8"
    )
