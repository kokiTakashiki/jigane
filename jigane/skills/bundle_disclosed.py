"""開示済みの限界の nit の束ね。シャードをまたぐ同じ限界を一件にまとめる役を一回起動する。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jigane.findings.discard import DisclosedNitReason
from jigane.findings.records import StoredFinding
from jigane.llm_runs import claude
from jigane.llm_runs.launch_failure import StoredInvocation
from jigane.skills import adversarial, disclosed, pipeline
from jigane.skills.output_models import BundleGroups, json_schema_of

ROLE = "bundle_disclosed"
MIN_TARGETS = 2
# 懸念の文だけで束ねられるので、コードは読ませない
BUNDLE_TOOLS: tuple[str, ...] = ()


def groups_schema() -> dict[str, Any]:
    return json_schema_of(BundleGroups)


def target_positions(findings: list[StoredFinding]) -> list[int]:
    """束ねの対象の、findings の中の位置。検証の後の、開示済みの限界の nit。"""
    return [
        index
        for index, finding in enumerate(findings)
        if isinstance(finding.body.nit_reason, DisclosedNitReason)
    ]


def build_prompt(targets: list[StoredFinding]) -> str:
    entries = [
        {"index": index, "concern": finding.body.nit_reason.claim}
        for index, finding in enumerate(targets)
        if finding.body.nit_reason is not None
    ]
    return adversarial.load_prompt(ROLE).substitute(
        findings_json=json.dumps(entries, ensure_ascii=False, indent=2)
    )


@dataclass(frozen=True)
class TooFewTargets:
    """対象が足りず、役を起動しなかった。起動の記録も実行の記録に載せる値も持たない。"""

    findings: tuple[StoredFinding, ...]


@dataclass(frozen=True)
class LaunchFailed:
    """役の起動が失敗した。指摘はそのまま返す。"""

    findings: tuple[StoredFinding, ...]
    record: StoredInvocation
    targets: int
    failure: str

    def to_dict(self) -> dict[str, Any]:
        return _failed_dict(self.targets, self.failure)


@dataclass(frozen=True)
class InvalidOutput:
    """役の出力がスキーマに合わなかった。指摘はそのまま返す。"""

    findings: tuple[StoredFinding, ...]
    record: StoredInvocation
    targets: int
    failure: str

    def to_dict(self) -> dict[str, Any]:
        return _failed_dict(self.targets, self.failure)


@dataclass(frozen=True)
class Bundled:
    """束ねた。組が無い場合（groups が 0）も、役が空の結果を返したこの状態に入る。"""

    findings: tuple[StoredFinding, ...]
    record: StoredInvocation
    targets: int
    groups: int
    discarded_groups: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "failed": False,
            "failure": None,
            "groups": self.groups,
            "discarded_groups": self.discarded_groups,
        }


BundleOutcome = TooFewTargets | LaunchFailed | InvalidOutput | Bundled


def _failed_dict(targets: int, failure: str) -> dict[str, Any]:
    return {
        "targets": targets,
        "failed": True,
        "failure": failure,
        "groups": 0,
        "discarded_groups": 0,
    }


def bundle_disclosed(
    findings: list[StoredFinding],
    *,
    working_directory: Path,
    settings: claude.RunSettings,
) -> BundleOutcome:
    """対象が 2 件以上のときだけ役を一回起動し、同じ限界を指す組を一件にまとめる。

    起動が失敗したときと出力がスキーマに合わないときは、まとめずに指摘をそのまま返す。
    """
    positions = target_positions(findings)
    if len(positions) < MIN_TARGETS:
        return TooFewTargets(tuple(findings))
    targets = [findings[i] for i in positions]
    prompt = build_prompt(targets)
    stamp = adversarial.stamp_prompt(ROLE, prompt)
    invocation = settings.invocation(
        prompt=prompt,
        json_schema=groups_schema(),
        working_directory=working_directory,
        tools=BUNDLE_TOOLS,
    )
    try:
        result = settings.invoke(invocation)
    except claude.ClaudeInvocationError as error:
        record = pipeline.stored_invocation(ROLE, None, error.record, stamp)
        return LaunchFailed(tuple(findings), record, len(targets), str(error))
    record = pipeline.stored_invocation(ROLE, None, result.record, stamp)
    try:
        output = BundleGroups.model_validate(result.structured_output or {})
    except ValidationError as error:
        failure = f"束ねの出力がスキーマに適合しません: {error.errors()[0]['msg']}"
        return InvalidOutput(tuple(findings), record, len(targets), failure)
    raw = output.groups
    vetted = disclosed.vet_groups(raw, len(targets))
    merged = iter(disclosed.merge_groups(targets, vetted.groups))
    target_set = set(positions)
    absorbed = {positions[i] for group in vetted.groups for i in group[1:]}
    # merge_groups の戻りは対象の順のまま、吸収された指摘だけが抜ける。対象の位置に順に差し戻す
    result_list = [
        next(merged) if index in target_set else finding
        for index, finding in enumerate(findings)
        if index not in absorbed
    ]
    return Bundled(tuple(result_list), record, len(targets), len(vetted.groups), vetted.discarded)
