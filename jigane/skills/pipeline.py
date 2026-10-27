"""実装と方針のスキルが共有する、候補の正規化と実行の保存。"""

import copy
import json
import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import MISSING, TypeAdapter, ValidationError

from jigane.findings.discard import (
    KEY_FROM_CONCERN,
    Considered,
    DiscardedCandidate,
    ShardConcerns,
    discard_summary,
)
from jigane.findings.records import (
    RUNS_FILE,
    Adjudication,
    AdjudicationKind,
    Alternative,
    GroupResult,
    Rebuttal,
    ReplacedRun,
    StoredFinding,
    ToolClaim,
    merge_group,
    read_verification,
    reserve_numbers,
)
from jigane.groups.definitions import GroupId
from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude, finding_model
from jigane.llm_runs.claude import StoredInvocationRecord
from jigane.llm_runs.cost import BudgetOutcome, Cost, total_by_source
from jigane.llm_runs.finding_model import Finding
from jigane.llm_runs.launch_failure import (
    LEGACY_FULL_SHAPE_ROLE,
    FailureRound,
    FailureScope,
    RerunKind,
    RunRecordKind,
    Stage,
    StoredFailure,
    StoredInvocation,
    as_earlier,
    current_failures,
    failed_invocation_costs,
    failed_invocations,
    failure_from_record,
)
from jigane.llm_runs.prompt_stamp import PromptStamp
from jigane.reviews.lifecycle import LocatedReview, Review
from jigane.skills.input_budget import BudgetStop, describe_invocation
from jigane.storage.store import review_lock, write_text_atomic
from jigane.storage.store_format import upgrade_mapping

LOGGER = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """発見役が失敗した。group は失敗した群の内部名。"""

    def __init__(self, group: GroupId, message: str) -> None:
        super().__init__(message)
        self.group = group


class InputBudgetError(DiscoveryError):
    """入力だけで 1 回の起動の上限の費用に達する見込みのため、起動せずに止めた。"""

    def __init__(self, group: GroupId, message: str, stop: BudgetStop | None = None) -> None:
        super().__init__(group, message)
        self.stop = stop


def normalize_location(location: dict[str, Any], working_directory: Path) -> dict[str, Any] | None:
    path = claude.normalize_path(location["path"], working_directory)
    if path is None:
        return None
    return {**location, "path": path}


def normalize_locations(
    locations: list[dict[str, Any]], working_directory: Path
) -> list[dict[str, Any]]:
    normalized = (normalize_location(item, working_directory) for item in locations)
    return [item for item in normalized if item is not None]


@dataclass
class DropCounts:
    without_evidence: int = 0
    without_bot_findings: int = 0
    invalid_schema: int = 0


def prepare_candidates(
    discovered: list[dict[str, Any]],
    working_directory: Path,
    *,
    has_bot_findings: bool,
    drops: DropCounts,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for finding in discovered:
        if finding["viewpoint"] == "M5" and not has_bot_findings:
            # M5 はボットの指摘への対応を問う観点で、一覧が無ければ問えない。
            drops.without_bot_findings += 1
            continue
        evidence = normalize_locations(finding["evidence"], working_directory)
        if not evidence:
            drops.without_evidence += 1
            continue
        reference = finding["declaration_ref"]
        candidates.append(
            {
                **finding,
                "evidence": evidence,
                "declaration_ref": (
                    None if reference is None else normalize_location(reference, working_directory)
                ),
            }
        )
    return candidates


def without_from_concern(finding: dict[str, Any], why: str) -> dict[str, Any]:
    """from_concern を落とした指摘を返す。落としたことはログに警告で残す。"""
    LOGGER.warning("%s。from_concern を落とします", why)
    return {key: value for key, value in finding.items() if key != KEY_FROM_CONCERN}


_ADJUDICATION_MODEL: TypeAdapter[finding_model.Rebuttal | finding_model.Alternative] = TypeAdapter(
    finding_model.Adjudication
)


def adjudication_from_model_output(raw: dict[str, Any], working_directory: Path) -> Adjudication:
    """役の出力を束ねた辞書から裁定を作る。モデルの出力を裁定の型にする境はここだけ。

    反証の再確認の位置は、保存の形（作業ツリー相対）へ揃える。
    値が裁定のモデルに合わないときは ValidationError を送出する。
    保存物の読み直しは寛容さが要るので、ここでなく read_adjudication を通る。
    """
    if raw["kind"] == AdjudicationKind.REBUTTAL.value:
        raw = {**raw, "recheck": normalize_locations(raw["recheck"], working_directory)}
    model = _ADJUDICATION_MODEL.validate_python(raw)
    if isinstance(model, finding_model.Alternative):
        return Alternative(
            verdict=model.verdict,
            reason=model.reason,
            alternative=model.alternative,
            defense=model.defense,
        )
    claim = model.tool_claim
    return Rebuttal(
        verdict=model.verdict,
        reason=model.reason,
        challenge=model.challenge,
        recheck=tuple(place.model_dump() for place in model.recheck),
        verified=read_verification(None if model.verified is MISSING else model.verified),
        tool_claim=(
            None
            if claim is MISSING
            else ToolClaim(relies=claim.relies, basis=claim.basis, version=claim.version)
        ),
    )


@dataclass(frozen=True)
class ValidatedCandidate:
    """指摘になる前の候補の本体と、その裁定。裁定が None なのは検証していない候補。"""

    candidate: Mapping[str, Any]
    adjudication: Adjudication | None


def _is_valid_finding(finding: dict[str, Any]) -> bool:
    try:
        Finding.model_validate(finding)
    except ValidationError:
        return False
    return True


def keep_valid(
    review: Review,
    candidates: list[ValidatedCandidate],
    drops: DropCounts,
    shape: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[StoredFinding]:
    """候補に番号を振り、`shape` で指摘の形に整えてから、指摘のモデルで一件ずつ確かめる。

    通った候補は指摘の型で返し、通らなかった候補は捨てて `drops.invalid_schema` に数える。
    確かめるのは `shape` が返した辞書のまま。`Finding` はモデルの出力と同じ形の辞書を
    厳格に受けるため、型へ変換する前の辞書で検証する。裁定は辞書として検証に含め、
    通った指摘には型の値として渡す。捨てた候補の番号は消費したままにする。
    """
    first_number = reserve_numbers(review.id.directory(), len(candidates))
    kept: list[StoredFinding] = []
    for offset, item in enumerate(candidates):
        adjudication = item.adjudication
        finding = shape(
            {
                "id": f"F{first_number + offset}",
                **item.candidate,
                "adjudication": None if adjudication is None else adjudication.to_json(),
            }
        )
        if _is_valid_finding(finding):
            body = {key: value for key, value in finding.items() if key != "adjudication"}
            kept.append(StoredFinding.of_model_output(body, adjudication))
        else:
            # 番号は消費したままにする。再実行でも番号を再利用しないため。
            drops.invalid_schema += 1
    return kept


def run_state_head[RunT: "GroupRun"](
    run_type: type[RunT],
    *,
    settings: claude.RunSettings,
    invocations: list[StoredInvocation],
    discovered: int,
    drops: DropCounts,
    elapsed_s: float,
) -> RunT:
    """群によらず `runs.json` の各群の記録が持つ項目だけを埋めた、run_type の記録。

    種類ごとの項目は None のままで、呼び出し側が replace で足す。

    elapsed_s は実行の開始から記録を作る直前までの経過時間（秒）。
    起動ごとの duration_ms は並行に動く分が重なるため、足しても経過時間にならない。
    """
    return run_type(
        ran_at=datetime.now(UTC).isoformat(timespec="seconds"),
        model=settings.budget.model,
        shape=None,
        discovered=discovered,
        discarded=None,
        invocations=tuple(invocations),
        launch_failures=None,
        elapsed_s=round(elapsed_s, 1),
        effort=settings.effort,
        effort_recorded=True,
        dropped_without_evidence=drops.without_evidence,
        dropped_invalid_schema=drops.invalid_schema,
        others={},
    )


def write_json(path: Path, value: object) -> None:
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2))


class RunKind(StrEnum):
    """実行の記録の種類。群から決まり、記録が持つ項目の組を分ける。"""

    POLICY = "policy"
    SHARDED = "sharded"

    @classmethod
    def of(cls, group: GroupId) -> "RunKind":
        return cls.POLICY if group == GroupId.MACRO else cls.SHARDED


@dataclass(frozen=True)
class GroupRun(ABC):
    """runs.json に保存された、ある群の実行の記録のうち、どの群も持つ頭。

    実体は種類ごとの `PolicyGroupRun` と `ShardedGroupRun`。読むときは `read` で種類を選ぶ。
    項目が無い古い記録も同じ形で読み、書き戻しても項目を足さない（None は「記録に無い」）。
    型にしない未知の鍵は others に残し、往復で失わない。
    """

    ran_at: str | None
    model: str | None
    shape: str | None
    discovered: int | None
    # 捨てた候補を保存する前の古い記録は None
    discarded: tuple[DiscardedCandidate, ...] | None
    invocations: tuple[StoredInvocation, ...] | None
    launch_failures: tuple[StoredFailure, ...] | None
    elapsed_s: float | None
    # 群の既定が effort を持たない実行は null で保存する。null と欄なしを書き戻しで取り違えない
    effort: str | None
    effort_recorded: bool
    dropped_without_evidence: int | None
    dropped_invalid_schema: int | None
    others: Mapping[str, Any]

    # 値をそのまま持つ鍵。型を持つ入れ子の鍵は _HEAD_NESTED_KEYS が別に持つ
    _HEAD_PLAIN_KEYS = (
        "ran_at",
        "model",
        "shape",
        "discovered",
        "elapsed_s",
        "dropped_without_evidence",
        "dropped_invalid_schema",
    )
    _HEAD_NESTED_KEYS = ("discarded", "invocations", "launch_failures")

    @staticmethod
    def read(value: Mapping[str, Any], kind: RunKind) -> "PolicyGroupRun | ShardedGroupRun":
        if kind is RunKind.POLICY:
            return PolicyGroupRun.from_json(value)
        return ShardedGroupRun.from_json(value)

    @classmethod
    def _head_from_json(
        cls, value: Mapping[str, Any], own_keys: tuple[str, ...]
    ) -> dict[str, Any]:
        """頭の項目の引数。型にしない鍵は others へ残す。own_keys は種類の型が持つ鍵。"""
        stored_discarded = value.get("discarded")
        stored_invocations = value.get("invocations")
        stored_failures = value.get("launch_failures")
        typed = {*cls._HEAD_PLAIN_KEYS, *cls._HEAD_NESTED_KEYS, "effort", *own_keys}
        return {
            "ran_at": value.get("ran_at"),
            "model": value.get("model"),
            "shape": value.get("shape"),
            "discovered": value.get("discovered"),
            "discarded": None
            if stored_discarded is None
            else tuple(DiscardedCandidate.from_json(item) for item in stored_discarded),
            "invocations": None
            if stored_invocations is None
            else tuple(StoredInvocation.from_json(item) for item in stored_invocations),
            "launch_failures": None
            if stored_failures is None
            else tuple(failure_from_record(item) for item in stored_failures),
            "elapsed_s": value.get("elapsed_s"),
            "effort": value.get("effort"),
            "effort_recorded": "effort" in value,
            "dropped_without_evidence": value.get("dropped_without_evidence"),
            "dropped_invalid_schema": value.get("dropped_invalid_schema"),
            "others": {key: item for key, item in value.items() if key not in typed},
        }

    def _head_to_json(self) -> dict[str, Any]:
        stored: dict[str, Any] = {
            key: getattr(self, key)
            for key in self._HEAD_PLAIN_KEYS
            if getattr(self, key) is not None
        }
        if self.effort_recorded:
            stored["effort"] = self.effort
        if self.invocations is not None:
            stored["invocations"] = [item.to_json() for item in self.invocations]
        if self.discarded is not None:
            stored["discarded"] = [item.to_json() for item in self.discarded]
        if self.launch_failures is not None:
            stored["launch_failures"] = [item.as_record() for item in self.launch_failures]
        return stored

    @abstractmethod
    def to_json(self) -> dict[str, Any]: ...

    @property
    def considered(self) -> Considered | None:
        """検討の結果。捨てた候補を保存する前の古い記録は何も言えないので None。"""
        if self.discarded is None:
            return None
        return Considered(self.discovered or 0, self.discarded)

    @property
    def cost_usd(self) -> float:
        return total_by_source(invocation_costs(self)).usd

    @property
    def current_failures(self) -> list[StoredFailure]:
        """画面が出す失敗。前の回の失敗は含めない。"""
        return current_failures(self.launch_failures or ())

    def next_round(self, new_run: "GroupRun") -> "GroupRun":
        """この記録の次の回として new_run を保存するときの記録。

        この記録の失敗は前の回のものにして、new_run の launch_failures の先頭へ残す。
        成功した回の記録は前の回の launch_failures を持たないため、そのまま差し替えると前の失敗で
        使った額を usage と費用の集計が数えなくなる。
        """
        earlier = as_earlier(self.launch_failures or ())
        if not earlier:
            return new_run
        return replace(new_run, launch_failures=(*earlier, *(new_run.launch_failures or ())))


@dataclass(frozen=True)
class PolicyGroupRun(GroupRun):
    """方針（macro）の実行の記録。方針だけが持つ項目を足す。"""

    dropped_without_bot_findings: int | None = None
    record_kind: str | None = None
    no_material: tuple[str, ...] | None = None

    _OWN_PLAIN_KEYS = ("dropped_without_bot_findings", "record_kind")

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PolicyGroupRun":
        stored_no_material = value.get("no_material")
        return cls(
            **cls._head_from_json(value, (*cls._OWN_PLAIN_KEYS, "no_material")),
            dropped_without_bot_findings=value.get("dropped_without_bot_findings"),
            record_kind=value.get("record_kind"),
            no_material=None if stored_no_material is None else tuple(stored_no_material),
        )

    def to_json(self) -> dict[str, Any]:
        stored = self._head_to_json()
        for key in self._OWN_PLAIN_KEYS:
            if getattr(self, key) is not None:
                stored[key] = getattr(self, key)
        if self.no_material is not None:
            stored["no_material"] = list(self.no_material)
        return {**stored, **self.others}


@dataclass(frozen=True)
class McpRecord:
    """実行で使った MCP サーバの記録。「不明」は欄の無い古い記録だけが持つ。"""

    servers: tuple[str, ...] = ()
    known: bool = True

    @classmethod
    def unknown(cls) -> "McpRecord":
        return cls((), known=False)

    @classmethod
    def none(cls) -> "McpRecord":
        return cls()

    def label(self) -> str:
        if not self.known:
            return "MCP: 不明"
        return f"MCP: {', '.join(self.servers)}" if self.servers else "MCP: 無し"

    def to_stored(self) -> list[str] | None:
        """保存の形。不明は欄を書かない。"""
        return list(self.servers) if self.known else None

    @classmethod
    def from_stored(cls, stored: list[str] | None) -> "McpRecord":
        return cls.unknown() if stored is None else cls(tuple(stored))


UNKNOWN_MCP = McpRecord.unknown()


@dataclass(frozen=True)
class ShardedGroupRun(GroupRun):
    """実装とセキュリティの実行の記録。シャードで走らせる群だけが持つ項目を足す。

    保存する前の古い記録は、これらの項目の一部だけを持つことがある。各項目は None を許し、
    あるものだけを往復で保つ。
    """

    shards: int | None = None
    succeeded_shards: int | None = None
    target_files: int | None = None
    reviewed_files: int | None = None
    dropped_outside_shard: int | None = None
    failed_splits: int | None = None
    unverified_candidates: int | None = None
    budget_stops: tuple[Mapping[str, Any], ...] | None = None
    # 使った MCP。保存の欄名は mcp_servers のまま（既存の記録を読めるように）
    mcp: McpRecord = UNKNOWN_MCP

    _OWN_PLAIN_KEYS = (
        "shards",
        "succeeded_shards",
        "target_files",
        "reviewed_files",
        "dropped_outside_shard",
        "failed_splits",
        "unverified_candidates",
    )

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "ShardedGroupRun":
        stored_stops = value.get("budget_stops")
        stored_mcp = value.get("mcp_servers")
        return cls(
            **cls._head_from_json(value, (*cls._OWN_PLAIN_KEYS, "budget_stops", "mcp_servers")),
            mcp=McpRecord.from_stored(stored_mcp),
            shards=value.get("shards"),
            succeeded_shards=value.get("succeeded_shards"),
            target_files=value.get("target_files"),
            reviewed_files=value.get("reviewed_files"),
            dropped_outside_shard=value.get("dropped_outside_shard"),
            failed_splits=value.get("failed_splits"),
            unverified_candidates=value.get("unverified_candidates"),
            budget_stops=None if stored_stops is None else tuple(stored_stops),
        )

    def to_json(self) -> dict[str, Any]:
        stored = self._head_to_json()
        for key in self._OWN_PLAIN_KEYS:
            if getattr(self, key) is not None:
                stored[key] = getattr(self, key)
        if self.budget_stops is not None:
            stored["budget_stops"] = [dict(item) for item in self.budget_stops]
        stored_mcp = self.mcp.to_stored()
        if stored_mcp is not None:
            stored["mcp_servers"] = stored_mcp
        return {**stored, **self.others}

    @property
    def failed_shards(self) -> int:
        """発見役が失敗したシャードの数。実行結果の事実の出所はこの記録だけ。"""
        return (self.shards or 0) - (self.succeeded_shards or 0)


def mcp_label(run: GroupRun) -> str:
    """実行で使った MCP の表示。欄の無い古い記録は「不明」。"""
    if not isinstance(run, ShardedGroupRun):
        return McpRecord.unknown().label()
    return run.mcp.label()


@dataclass(frozen=True)
class RunsFile:
    """runs.json の全体。群ごとの記録を持つ。

    版を知らない読み書きなので、本体では load_runs と dump_runs だけが
    from_json と to_json を呼ぶ。
    版の検査と移しはその二つが受け持つ。
    """

    groups: Mapping[GroupId, GroupRun]

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "RunsFile":
        return cls(
            {
                GroupId(group): GroupRun.read(item, RunKind.of(GroupId(group)))
                for group, item in value.items()
            }
        )

    def to_json(self) -> dict[str, Any]:
        return {group: item.to_json() for group, item in self.groups.items()}


# runs.json の形の版。保存物の形を変えたら一つ上げ、_UPGRADES に前の版からの移しを足す
RUNS_FORMAT = 3


def _upgrade_failure_v1(record: dict[str, Any]) -> None:
    """launch_failures の一件を今の版の鍵へ埋める。record は書き換える。"""
    record.setdefault("cost_source", "measured")
    record.setdefault("scope", FailureScope.LAUNCH.value)
    record.setdefault("round", FailureRound.CURRENT.value)
    record.setdefault("ran_at", None)
    if (
        record["scope"] == FailureScope.LAUNCH.value
        and record["stage"] == Stage.DISCOVERY.value
        and record["role"] is None
    ):
        # 廃止した四回の方針の形は、発見の失敗を役なしで書いた
        record["role"] = LEGACY_FULL_SHAPE_ROLE
    if record["scope"] == FailureScope.MISSING_CANDIDATES.value:
        # 候補の番号を持たない古い記録は、番号が分からないので空の列にする
        record.setdefault("candidates", [])


def _upgrade_invocation_v1(row: dict[str, Any]) -> None:
    """invocations の一行を今の版の鍵へ埋める。row は書き換える。"""
    row.setdefault("cost_source", "measured")
    # 旧い旗は、上限を持たない行のうち費用が上限を超えた成功の行だけが持っていた
    if row.pop("over_budget", False) and "max_budget_usd" not in row:
        row["budget_outcome"] = BudgetOutcome.EXCEEDED_AFTER_SUCCESS.value


def _upgrade_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の runs.json を次の版へ移す。鍵の欠けは、移す前の読み口が使っていた値で埋める。"""
    upgraded = copy.deepcopy(dict(raw))
    macro_run = upgraded.get(GroupId.MACRO.value)
    if macro_run is not None:
        # 欄の無い方針の記録は成功した回だけのもの。launch_failures を足す書き手は必ず欄を付けた
        macro_run.setdefault("record_kind", RunRecordKind.SUCCEEDED.value)
    for run in upgraded.values():
        for row in run.get("invocations") or ():
            _upgrade_invocation_v1(row)
        for record in run.get("launch_failures") or ():
            _upgrade_failure_v1(record)
    return upgraded


def _upgrade_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 2 の runs.json を次の版へ移す。行の欄は変わらないので、足す鍵は無い。"""
    return copy.deepcopy(dict(raw))


# 保存された版から次の版への移し。load_runs が RUNS_FORMAT まで順に回す
_UPGRADES: dict[int, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    1: _upgrade_v1,
    2: _upgrade_v2,
}


def load_runs(raw: Mapping[str, Any]) -> RunsFile:
    """runs.json を読む境の一か所。保存された版から今の版まで移してから、今の版の型へ読む。"""
    stored = upgrade_mapping(RUNS_FILE, raw, current=RUNS_FORMAT, upgrades=_UPGRADES)
    return RunsFile.from_json(stored)


def dump_runs(runs: Mapping[GroupId, GroupRun]) -> dict[str, Any]:
    """runs.json へ書く境の一か所。最上位に今の版を刻む。移した結果は書き戻さない。"""
    return {"format": RUNS_FORMAT, **RunsFile(runs).to_json()}


def read_runs(directory: Path) -> dict[GroupId, GroupRun]:
    """置き場の runs.json を群ごとの記録として読む。ファイルが無ければ空。"""
    path = directory / RUNS_FILE
    if not path.is_file():
        return {}
    return dict(load_runs(json.loads(path.read_text(encoding="utf-8"))).groups)


def write_runs(directory: Path, runs: Mapping[GroupId, GroupRun]) -> None:
    write_json(directory / RUNS_FILE, dump_runs(runs))


def save_run(
    review: Review,
    result: GroupResult,
    run_state: "GroupRun",
    group: GroupId,
) -> None:
    """この群の結果と実行の記録を保存する。他の群が実行の間に保存した内容は読み直して残す。"""
    directory = review.id.directory()
    # runs.json はこの保存の後に更新される。いま残る記録は、差し替えられる結果を作った回のもの
    replaced = read_runs(directory).get(group)
    # 捨てた候補を記録しない群の記録は considered が None を返し、記録なしになる
    replaced_run = (
        None
        if replaced is None
        else ReplacedRun(replaced.ran_at, discard_summary(replaced.considered))
    )
    merge_group(directory, group, result, replaced_run)
    with review_lock(directory):
        runs = read_runs(directory)
        previous = runs.get(group)
        runs[group] = run_state if previous is None else previous.next_round(run_state)
        write_runs(directory, runs)


def stored_invocation(
    role: str,
    split_index: int | None,
    record: claude.InvocationRecord,
    stamp: PromptStamp,
    rerun: RerunKind = RerunKind.INITIAL,
    *,
    concerns: ShardConcerns | None = None,
) -> StoredInvocation:
    """concerns は発見役の起動が渡した懸念。発見役でない起動は None で、欄を書かない。"""
    return StoredInvocation.of(
        role,
        split_index,
        record,
        stamp,
        # 走らせ直しの起動だけに付ける。付かない起動は最初の実行
        rerun=None if rerun is RerunKind.INITIAL else rerun,
        concerns=concerns,
    )


def input_token_total(record: StoredInvocationRecord) -> int:
    return (
        record.input_tokens + record.cache_read_input_tokens + record.cache_creation_input_tokens
    )


def all_invocations(run: GroupRun) -> list[StoredInvocation]:
    """保存した起動の行と、launch_failures から導く失敗した起動の行。"""
    return [*(run.invocations or ()), *failed_invocations(run.launch_failures or ())]


def invocation_costs(run: GroupRun) -> list[Cost]:
    """成功した起動と失敗した起動の額。金額の集計は total_by_source だけが数える。"""
    return [
        *(item.record.cost for item in run.invocations or ()),
        *failed_invocation_costs(run.launch_failures or ()),
    ]


def summarize_invocations(run: GroupRun) -> dict[str, Any]:
    """起動の集計。--json へ渡す辞書は、この出力の境で型から作る。"""
    invocations = all_invocations(run)
    totals = total_by_source(invocation_costs(run))
    return {
        "invocation_count": len(invocations),
        "input_tokens": sum(input_token_total(item.record) for item in invocations),
        "cost_usd": totals.usd,
        "substituted_cost_usd": totals.limit_substitute,
        "over_budget": [
            {
                "role": item.role,
                "split_index": item.split_index,
                "cost_usd": total_by_source([item.record.cost]).usd,
                # None は上限を持たない古い記録の行で、上限が無いことを表す
                "max_budget_usd": item.record.max_budget_usd,
                "outcome": item.budget_outcome,
            }
            for item in invocations
            if item.budget_outcome is not None
        ],
        # 上限で見送る起動の記録は、シャードの群だけが持つ
        "budget_stops": list(run.budget_stops or ()) if isinstance(run, ShardedGroupRun) else [],
    }


def over_budget_lines(totals: dict[str, Any]) -> list[str]:
    """上限で止まった起動、上限を超えた起動と、入力の見積もりで起動しなかった役を、一行ずつの文にする。"""
    lines = [
        f"{'上限で停止' if item['outcome'] == BudgetOutcome.STOPPED_AT_LIMIT else '上限超過'}: "
        f"{describe_invocation(item['role'], item['split_index'])}が {item['cost_usd']:.4f} USD"
        + ("" if item["max_budget_usd"] is None else f"（上限 {item['max_budget_usd']:g} USD）")
        for item in totals["over_budget"]
    ]
    lines += [
        f"起動せず: {describe_invocation(item['role'], item['index'])}、"
        f"入力の見積もり {item['estimated_input_tokens']} トークン"
        f"（{item['estimated_input_usd']:.4f} USD）、上限 {item['max_budget_usd']:g} USD"
        for item in totals["budget_stops"]
    ]
    return lines


def format_elapsed(elapsed_s: float) -> str:
    """経過時間を「5 分 16 秒」の形にする。

    1 分未満は「42 秒」。
    秒は 0.5 を切り上げて整数にする。
    """
    minutes, seconds = divmod(math.floor(elapsed_s + 0.5), 60)
    return f"{minutes} 分 {seconds} 秒" if minutes else f"{seconds} 秒"


def read_group_run(located: LocatedReview, group: GroupId) -> GroupRun | None:
    """写した置き場の runs.json から、その群の実行の記録を読む。無ければ None。"""
    return read_runs(located.directory).get(group)


def past_run(
    review: Review, group: GroupId, shape: PolicyShape | None = None
) -> dict[str, Any] | None:
    """その群の過去の実行の実測。過去の実行が無ければ None で、実測との並記はできない。

    shape を渡すと、runs.json の shape が同じ実行だけから引く。形の違う実行は当たらない。

    model は実行時の既定が変わると金額の読み方が変わるため、実測に添える。
    """
    run = read_runs(review.id.directory()).get(group)
    if run is None:
        return None
    if shape is not None and run.shape != shape:
        return None
    return {**summarize_invocations(run), "model": run.model}
