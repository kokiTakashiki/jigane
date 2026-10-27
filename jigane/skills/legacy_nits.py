"""写す前の古い記録の捨てた候補を、runs.json を読む境で nit の指摘に組み立てる。"""

from collections.abc import Sequence
from pathlib import Path

from jigane.findings.discarded_nits import legacy_discarded_nits
from jigane.findings.records import TriagedFinding
from jigane.groups.definitions import MACRO
from jigane.skills import pipeline


def legacy_nits_of(directory: Path, findings: Sequence[TriagedFinding]) -> list[TriagedFinding]:
    """方針の群の指摘に足す、古い記録の捨てた候補の nit。show の一覧だけがここを通る。"""
    run = pipeline.read_runs(directory).get(MACRO)
    return legacy_discarded_nits(MACRO, findings, None if run is None else run.considered)
