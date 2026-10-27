"""指摘の選別。方針の区分、実装の指摘のテーマへの束ねと並べ方を決める。"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jigane.findings.records import (
    ScopeBody,
    StoredFinding,
    StoredTheme,
    ThemeUpdate,
    TriagedFinding,
    is_rejected,
    theme_triage,
    triaged,
)
from jigane.groups.definitions import MICRO
from jigane.llm_runs import claude
from jigane.llm_runs.launch_failure import StoredInvocation
from jigane.skills import adversarial, pipeline
from jigane.skills.output_models import ThemesOutput, json_schema_of

NIT_THEME_TITLE = "nit"
THEME_TOOLS = ("Read",)
SEVERITY_RANK = {"critical": 0, "warning": 1, "nit": 2}


def themes_schema() -> dict[str, Any]:
    return json_schema_of(ThemesOutput)


def build_themes_prompt(findings: list[StoredFinding]) -> str:
    common = adversarial.common_prompt()
    entries = [
        {
            "id": f.body.id,
            "viewpoint": f.body.viewpoint,
            "severity": f.body.severity,
            "claim": f.body.claim,
            "evidence": f.body.extra["evidence"],
        }
        for f in findings
    ]
    return adversarial.load_prompt("themes").substitute(
        common=common, findings_json=json.dumps(entries, ensure_ascii=False, indent=2)
    )


@dataclass(frozen=True)
class RawTheme:
    title: str
    finding_ids: tuple[str, ...]


@dataclass(frozen=True)
class VetResult:
    groups: tuple[RawTheme, ...]
    unknown_ids: int
    duplicate_ids: int
    unassigned: int


def vet_themes(raw: list[RawTheme], findings: list[StoredFinding]) -> VetResult:
    """束ねの結果を決定的に検める。

    知らない id は捨てる。二つ以上のテーマに入った指摘は最初のテーマだけに残す。
    どのテーマにも入らなかった指摘は、単独のテーマにする。
    """
    known = {f.body.id: f for f in findings}
    assigned: set[str] = set()
    groups: list[RawTheme] = []
    unknown = duplicate = 0
    for theme in raw:
        ids: list[str] = []
        for finding_id in theme.finding_ids:
            if finding_id not in known:
                unknown += 1
            elif finding_id in assigned:
                duplicate += 1
            else:
                assigned.add(finding_id)
                ids.append(finding_id)
        if ids:
            groups.append(RawTheme(theme.title, tuple(ids)))
        else:
            # 全員が知らない id か二重の指摘なら、題だけが残る。空のテーマは作らない。
            pass
    leftover = [f for f in findings if f.body.id not in assigned]
    groups.extend(RawTheme(f.body.claim, (f.body.id,)) for f in leftover)
    return VetResult(tuple(groups), unknown, duplicate, len(leftover))


def _number(finding_id: str) -> int:
    return int(finding_id[1:])


def order_themes(groups: Sequence[RawTheme], findings: list[StoredFinding]) -> list[RawTheme]:
    """最も重い重大度の高い順、指摘の数の多い順、最小の F 番号の順に並べる。"""
    severity = {f.body.id: SEVERITY_RANK[f.body.severity] for f in findings}

    def key(group: RawTheme) -> tuple[int, int, int]:
        return (
            min(severity[i] for i in group.finding_ids),
            -len(group.finding_ids),
            min(_number(i) for i in group.finding_ids),
        )

    return sorted(groups, key=key)


def _new_theme(number: int, title: str, ids: tuple[str, ...], severity: str) -> StoredTheme:
    # 区分は、所属する指摘の区分が決まった後に theme_triage で埋める
    return StoredTheme(
        id=f"B{number}", title=title, finding_ids=ids, severity=severity, triage=None
    )


@dataclass(frozen=True)
class MicroTriage:
    findings: tuple[TriagedFinding, ...]
    theme_update: ThemeUpdate
    record: StoredInvocation | None
    bundling: dict[str, Any] | None


def triage_micro(
    findings: list[StoredFinding],
    last_theme_number: int,
    *,
    working_directory: Path,
    settings: claude.RunSettings,
) -> MicroTriage:
    """棄却でも範囲外でもない実装の指摘をテーマに束ねる。

    各指摘の theme と triage を埋めた指摘を返す。範囲外の指摘は、テーマにも nit の束にも入れず、
    テーマなしの範囲外の区分で返す。
    """
    current: dict[str, StoredFinding] = {f.body.id: f.with_theme(None) for f in findings}
    done: dict[str, TriagedFinding] = {}
    for finding_id, finding in current.items():
        if is_rejected(finding.body) or isinstance(finding.body, ScopeBody):
            done[finding_id] = triaged(MICRO, finding)
    live = [f for f in current.values() if f.body.id not in done]
    nits = [f for f in live if f.body.severity == "nit"]
    bundled = [f for f in live if f.body.severity != "nit"]

    record: StoredInvocation | None = None
    bundling: dict[str, Any] | None = None
    groups: list[RawTheme] = []
    if bundled:
        raw: list[RawTheme] = []
        failure: str | None = None
        prompt = build_themes_prompt(bundled)
        stamp = adversarial.stamp_prompt("themes", prompt)
        invocation = settings.invocation(
            prompt=prompt,
            json_schema=themes_schema(),
            working_directory=working_directory,
            tools=THEME_TOOLS,
        )
        try:
            result = settings.invoke(invocation)
        except claude.ClaudeInvocationError as error:
            failure = str(error)
            record = pipeline.stored_invocation("themes", None, error.record, stamp)
        else:
            record = pipeline.stored_invocation("themes", None, result.record, stamp)
            try:
                output = result.structured_output or {}
                parsed = ThemesOutput.model_validate(output)
                raw = [RawTheme(t.title, tuple(t.finding_ids)) for t in parsed.themes]
            except ValidationError as error:
                failure = f"束ねの出力がスキーマに適合しません: {error.errors()[0]['msg']}"
        vetted = vet_themes(raw, bundled)
        bundling = {
            "failed": failure is not None,
            "failure": failure,
            "unknown_ids": vetted.unknown_ids,
            "duplicate_ids": vetted.duplicate_ids,
            "unassigned": vetted.unassigned,
        }
        groups = order_themes(vetted.groups, bundled)

    themes: list[StoredTheme] = []
    for position, group in enumerate(groups):
        last_theme_number += 1
        members = [current[i] for i in group.finding_ids]
        severity = min((m.body.severity for m in members), key=lambda name: SEVERITY_RANK[name])
        theme = _new_theme(last_theme_number, group.title, group.finding_ids, severity)
        decided = [
            triaged(MICRO, m.with_theme(theme.id), theme_position=position) for m in members
        ]
        done.update((d.finding.id, d) for d in decided)
        themes.append(
            replace(
                theme,
                triage=theme_triage(theme, {d.finding.id: d.decision.triage for d in decided}),
            )
        )
    if nits:
        last_theme_number += 1
        theme = _new_theme(
            last_theme_number,
            NIT_THEME_TITLE,
            tuple(f.body.id for f in nits),
            "nit",
        )
        decided = [triaged(MICRO, m.with_theme(theme.id)) for m in nits]
        done.update((d.finding.id, d) for d in decided)
        themes.append(
            replace(
                theme,
                triage=theme_triage(theme, {d.finding.id: d.decision.triage for d in decided}),
            )
        )
    return MicroTriage(
        tuple(done[f.body.id] for f in findings),
        ThemeUpdate(tuple(themes), last_theme_number),
        record,
        bundling,
    )
