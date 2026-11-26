"""敵対的検証の流れ。発見役の候補を、別の起動の役で順に検証する。"""

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any, Literal, cast
from unicodedata import normalize

from pydantic import BaseModel, ValidationError

from jigane.findings.records import Adjudication, Verdict
from jigane.git.tree_files import read_tree_file
from jigane.groups import viewpoints
from jigane.groups.definitions import GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.claude_inputs import Role
from jigane.llm_runs.claude_schema import json_schema_of
from jigane.llm_runs.finding_model import KEY_DISCLOSED_AT
from jigane.llm_runs.launch_failure import (
    CandidatesUnverified,
    Disposition,
    RerunKind,
    StoredFailure,
    VerificationLaunchFailed,
)
from jigane.llm_runs.prompt_stamp import PromptStamp
from jigane.preconditions.integers import positive_int
from jigane.skills import disclosed, input_budget, pipeline, shard_material, shared_rules
from jigane.skills.output_models import (
    AlternativeItem,
    DefenseItem,
    JudgeAlternativeItem,
    JudgeRebuttalItem,
    RebuttalItem,
    RoleItem,
    role_output_model,
)

LOGGER = logging.getLogger(__name__)

MAX_CANDIDATES_PER_INVOCATION = 10
# 検証の分割 1 件を走らせる回数の上限。最初の 1 回と、一時の失敗のあとの 1 回
MAX_SPLIT_ATTEMPTS = 2
ALTERNATIVE_VIEWPOINTS = frozenset(
    viewpoint.number
    for viewpoint in (
        viewpoints.PREMISE,
        viewpoints.PATH,
        viewpoints.STRUCTURE,
        viewpoints.BOT_RESPONSE,
    )
)

# 実装とセキュリティの群の発見役が書く欄。保存の形に欄が増えても、足さない限り見せない。
# deepens は方針の群だけの概念で、この二群の雛形も取り込みも使わない。
MODEL_WRITTEN = (
    "claim",
    "body",
    "severity",
    "viewpoint",
    "evidence",
    "references",
    "judgment_region",
    "declaration_ref",
    "from_concern",
)


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    stamp: PromptStamp


@dataclass(frozen=True)
class RoleInvocationRecord:
    role: str
    split_index: int
    record: claude.InvocationRecord
    stamp: PromptStamp
    # 最初の実行か、どちらの走らせ直しか
    rerun: RerunKind = RerunKind.INITIAL


@dataclass(frozen=True)
class VerifiedCandidate:
    """検証を終えた候補。候補は辞書のまま（スキーマの検査まで辞書で運ぶ）、裁定は型で持つ。"""

    candidate: dict[str, Any]
    adjudication: Adjudication


class UnverifiedReason(StrEnum):
    """候補が未検証のまま残った理由の種類。"""

    # 分割が入力の見積もりで止まり、役を起動しなかった
    INPUT_ESTIMATE_STOPPED = "input_estimate_stopped"
    # 分割の起動に失敗し、走らせ直さなかったか、走らせ直しても失敗した
    LAUNCH_FAILED = "launch_failed"
    # 役の出力に欠け、走らせ直しても欠けたままだった
    STILL_MISSING = "still_missing"
    MATERIALS_MISSING = "materials_missing"


@dataclass(frozen=True)
class UnverifiedCandidate:
    """判定の付かなかった候補。候補は辞書のまま（スキーマの検査まで辞書で運ぶ）、理由は型で持つ。"""

    candidate: dict[str, Any]
    reason: UnverifiedReason


@dataclass(frozen=True)
class VerificationOutcome:
    findings: tuple[VerifiedCandidate, ...]
    invocations: tuple[RoleInvocationRecord, ...] = ()
    failed_splits: int = 0
    # 入力の見積もりで起動しなかった役の記録（StoredBudgetStop）
    budget_stops: tuple[input_budget.StoredBudgetStop, ...] = ()
    # 失敗の記録。run_state の launch_failures へそのまま入る
    failures: tuple[StoredFailure, ...] = ()
    # 判定の付かなかった候補。失敗した分割の全候補と、欠けたまま残った候補。
    # run_sharded が指摘として残し、未検証の件数もここから数える
    unverified: tuple[UnverifiedCandidate, ...] = ()

    @property
    def unverified_candidates(self) -> int:
        """未検証の候補の総数。"""
        return len(self.unverified)


@dataclass(frozen=True)
class SplitVerification:
    """分割 1 件の検証が確定した結果。"""

    findings: tuple[VerifiedCandidate, ...]
    # この試行の中で確定した失敗の記録。試行が失敗したときは呼び出し側へ渡らない
    failures: tuple[StoredFailure, ...]
    # 欠けたまま残った候補
    unverified: tuple[UnverifiedCandidate, ...] = ()


# 裁定役の名前。起動の記録の role に書く値で、プロンプトの名前は形ごとに別（Role.JUDGE_*）
_JUDGE_ROLE = "judge"

# 検証の形。反証の形か代案の形のどちらか
type Form = Literal[Role.REBUTTAL, Role.ALTERNATIVE]


def role_schemas() -> dict[str, dict[str, Any]]:
    return {role: json_schema_of(role_output_model(item)) for role, item in _ROLE_ITEMS.items()}


_ROLE_ITEMS: dict[str, type[RoleItem]] = {
    Role.REBUTTAL: RebuttalItem,
    Role.ALTERNATIVE: AlternativeItem,
    Role.DEFENSE: DefenseItem,
    Role.JUDGE_REBUTTAL: JudgeRebuttalItem,
    Role.JUDGE_ALTERNATIVE: JudgeAlternativeItem,
}


def uses_alternative_form(candidate: dict[str, Any]) -> bool:
    return candidate["viewpoint"] in ALTERNATIVE_VIEWPOINTS


def split_candidates(
    candidates: list[dict[str, Any]],
) -> list[tuple[Form, list[dict[str, Any]]]]:
    splits: list[tuple[Form, list[dict[str, Any]]]] = []
    by_form: list[tuple[Form, list[dict[str, Any]]]] = [
        (Role.REBUTTAL, [c for c in candidates if not uses_alternative_form(c)]),
        (Role.ALTERNATIVE, [c for c in candidates if uses_alternative_form(c)]),
    ]
    for form, selected in by_form:
        splits.extend(
            (form, selected[start : start + MAX_CANDIDATES_PER_INVOCATION])
            for start in range(0, len(selected), MAX_CANDIDATES_PER_INVOCATION)
        )
    return splits


def template_text(name: str) -> str:
    return shared_rules.prompt_text(name)


def load_prompt(name: str) -> Template:
    return Template(template_text(name))


def prompt_version(name: str) -> str:
    # 順序は common、差し込む部品（shared_rules の表の順）、役の順に固定する。
    # 順が変わると同じ内容でも版が変わるため。
    digest = hashlib.sha256()
    parts = ("common", *(rule.file for rule in shared_rules.rules_for(name)), name)
    for part in parts:
        digest.update(template_text(part).encode("utf-8"))
    digest.update(shared_rules.values_for(name).get("viewpoints", "").encode("utf-8"))
    return digest.hexdigest()[:12]


def common_prompt() -> str:
    """全プロンプトに共通の前置き（prompts/common.md）。"""
    return (
        resources.files("jigane.skills")
        .joinpath("prompts/common.md")
        .read_text(encoding="utf-8")
        .strip()
    )


def stamp_prompt(name: str, text: str) -> PromptStamp:
    return PromptStamp(prompt_version(name), len(text))


def render_prompt(name: str, changes: str, **values: object) -> RenderedPrompt:
    common = common_prompt()
    # JSON の本文に $ が含まれても再展開されないよう、置換は 1 回だけ行う。
    text = load_prompt(name).substitute(
        common=common,
        changes=changes,
        **shared_rules.values_for(name),
        **{key: json.dumps(value, ensure_ascii=False, indent=2) for key, value in values.items()},
    )
    return RenderedPrompt(text, stamp_prompt(name, text))


class _SplitFailedError(Exception):
    def __init__(self, reason: str, kind: claude.FailureKind, *, role: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        # 投げる地点が決める。走らせ直すのは一時の種類だけで、出力の形の不正や上限の超過は
        # 走らせ直しても同じ
        self.kind = kind
        self.role = role


@dataclass(frozen=True)
class VerificationChanges:
    text: str
    excluded_excerpt_paths: frozenset[str]


class _SplitStoppedError(Exception):
    """入力の見積もりで起動しなかった分割。起動の失敗ではないので failures には残さない。"""


def _roles_for(form: Form) -> list[tuple[str, str, tuple[str, ...]]]:
    # (役の名前, プロンプトの名前, 渡す前の役の出力)。出力の名前は
    # プロンプトの置換名と同じにしてある。
    if form is Role.REBUTTAL:
        return [
            (Role.REBUTTAL, Role.REBUTTAL, ()),
            (_JUDGE_ROLE, Role.JUDGE_REBUTTAL, (Role.REBUTTAL,)),
        ]
    return [
        (Role.ALTERNATIVE, Role.ALTERNATIVE, ()),
        (Role.DEFENSE, Role.DEFENSE, (Role.ALTERNATIVE,)),
        (
            _JUDGE_ROLE,
            Role.JUDGE_ALTERNATIVE,
            (Role.ALTERNATIVE, Role.DEFENSE),
        ),
    ]


# 判定役へ渡す抜粋の上限。1 位置の行数は、反証役が確かめた範囲を判定役が足りると
# 見なせる大きさとして 40 行、1 候補の位置数は 3 件、1 候補の合計は 6000 文字とする。
# 超えた分は切り、足りない判定役は Read で読む。
EXCERPT_MAX_LINES = 40
EXCERPT_MAX_LOCATIONS = 3
EXCERPT_MAX_CHARS = 6000

_PLACEHOLDER_NAMES: dict[str, str] = {
    Role.REBUTTAL: "challenges_json",
    Role.ALTERNATIVE: "alternatives_json",
    Role.DEFENSE: "defenses_json",
}


def _line_range(location: object) -> tuple[int, int, str] | None:
    """位置の開始行、終了行、パスを取り出す。型や範囲が合わなければ None。"""
    if not isinstance(location, dict):
        return None
    start = positive_int(location.get("start_line"))
    end = positive_int(location.get("end_line"))
    raw = location.get("path")
    if start is None or end is None or not isinstance(raw, str):
        return None
    if end < start:
        return None
    return start, end, raw


def _readable_lines(path: str, working_directory: Path) -> list[str] | None:
    """作業ディレクトリの中のファイルを行に分けて返す。外、秘密、読めないものは None。"""
    root = working_directory.resolve()
    target = (root / path).resolve()
    # 示された名前でも symlink の先の名前でも、秘密のファイルは読まない。抜粋は外へ送る
    if claude.is_secret_file(path) or claude.is_secret_file(target.name):
        return None
    # レビュー対象のリンクは辿らず、上限を超えるファイルは読まない。None なら呼ぶ側の _excerpt が
    # その位置の抜粋を作らず、反証役の確かめた位置から外す
    data = read_tree_file(root, path)
    if data is None:
        return None
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _excerpt_path_key(path: str) -> str:
    # 大文字小文字や Unicode の合成の違いを同じファイルとして扱うファイルシステムがある。
    return normalize("NFC", path).casefold()


def _excerpt(
    location: object,
    working_directory: Path,
    excluded_path_keys: frozenset[str],
    presented_lines: shard_material.EvidenceLines | None,
) -> dict[str, Any] | None:
    """反証役が示した位置の実物を、jigane が読んで抜粋にする。読めない位置は None。"""
    parsed = _line_range(location)
    if parsed is None:
        return None
    start, end, raw = parsed
    path = claude.normalize_path(raw, working_directory)
    if path is None or _excerpt_path_key(path) in excluded_path_keys:
        return None
    end = min(end, start + EXCERPT_MAX_LINES - 1)
    if presented_lines is None:
        lines = _readable_lines(path, working_directory)
        if lines is None or start > len(lines):
            return None
        end = min(end, len(lines))
        text = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
    else:
        # 提示資料の索引には根拠行の照合と同じパスを使う。除外は正規化後のパスで済ませる。
        indexed = presented_lines.get(raw, {})
        if not _covers(indexed, start, end):
            return None
        text = "\n".join(f"{n}: {indexed[n]}" for n in range(start, end + 1))
    return {"path": path, "start_line": start, "end_line": end, "text": text}


def _checked_excerpts(
    rebuttals: dict[int, dict[str, Any]],
    working_directory: Path,
    excluded_path_keys: frozenset[str],
    presented_lines: shard_material.EvidenceLines | None,
) -> list[dict[str, Any]]:
    """候補ごとに、反証役が確かめた位置の抜粋を作る。反証役の書いた文字列は使わない。"""
    result: list[dict[str, Any]] = []
    for i, item in rebuttals.items():
        checked = item.get("checked")
        excerpts: list[dict[str, Any]] = []
        used = 0
        for location in checked if isinstance(checked, list) else []:
            if len(excerpts) >= EXCERPT_MAX_LOCATIONS or used >= EXCERPT_MAX_CHARS:
                break
            excerpt = _excerpt(location, working_directory, excluded_path_keys, presented_lines)
            if excerpt is None:
                continue
            excerpt["text"] = excerpt["text"][: EXCERPT_MAX_CHARS - used]
            used += len(excerpt["text"])
            excerpts.append(excerpt)
        result.append({"index": i, "checked": excerpts})
    return result


def _indexed(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"index": i, **candidate} for i, candidate in enumerate(candidates)]


@cache
def _output_model(prompt_name: str) -> type[BaseModel]:
    return role_output_model(_ROLE_ITEMS[prompt_name])


def _index_items(output: dict[str, Any], role: str, prompt_name: str) -> dict[int, RoleItem]:
    """役の出力を役の型で検証し、index で引けるようにする。

    index を欠く候補の判断は呼ぶ側が持つ。
    """
    try:
        validated = _output_model(prompt_name).model_validate(output)
    except ValidationError as error:
        # 失敗の記録に出力の値を写さないよう、最初の誤りの場所と文面だけを残す
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise _SplitFailedError(
            f"{role} の出力が役の型に合いません: {location}: {first['msg']}",
            claude.FailureKind.BAD_OUTPUT,
            role=role,
        ) from error
    items: list[RoleItem] = validated.items  # type: ignore[attr-defined]
    by_index: dict[int, RoleItem] = {}
    for item in items:
        by_index.setdefault(item.index, item)
    return by_index


def _missing_reason(role: str, missing: list[int]) -> str:
    return f"{role} の出力に index {missing} がありません"


@dataclass(frozen=True)
class _Split:
    """検証の分割 1 件。index は分割の並びの中の位置。"""

    index: int
    form: Form
    candidates: tuple[dict[str, Any], ...]
    group: GroupId
    presented_lines: shard_material.EvidenceLines | None = None
    excluded_excerpt_path_keys: frozenset[str] = frozenset()


def _covers(lines: dict[int, str], start: int, end: int) -> bool:
    return end - start < len(lines) and all(n in lines for n in range(start, end + 1))


def _presented(location: object, lines: shard_material.EvidenceLines) -> bool:
    position = _line_range(location)
    if position is None:
        return False
    start, end, path = position
    return _covers(lines.get(path, {}), start, end)


def _static_output_issue(item: RoleItem, lines: shard_material.EvidenceLines) -> str | None:
    if isinstance(item, RebuttalItem):
        locations = item.checked
    elif isinstance(item, JudgeRebuttalItem):
        if not item.verified:
            return "提示資料で候補の核心を確認できませんでした"
        locations = item.recheck
    else:
        return None
    if any(not _presented(location.model_dump(mode="json"), lines) for location in locations):
        return "提示資料に含まれない根拠行です"
    return None


def _missing_candidates(
    candidates: tuple[dict[str, Any], ...], lines: shard_material.EvidenceLines | None
) -> list[int]:
    if lines is None:
        return []
    return [
        number
        for number, candidate in enumerate(candidates)
        if not (locations := candidate.get("evidence"))
        or not all(_presented(location, lines) for location in locations)
    ]


def _checked_role_items(
    indexed: dict[int, RoleItem],
    local: dict[int, int],
    lines: shard_material.EvidenceLines | None,
    missing_material: Callable[[int, str], None],
) -> dict[int, RoleItem]:
    checked = {local[position]: item for position, item in indexed.items() if position in local}
    if lines is not None:
        for number, item in tuple(checked.items()):
            if (issue := _static_output_issue(item, lines)) is not None:
                missing_material(number, issue)
                del checked[number]
    return checked


def _render_checked(
    prompt_name: str,
    changes: str,
    values: dict[str, object],
    settings: claude.RunSettings,
    role: str,
    split: _Split,
    budget_stops: list[input_budget.StoredBudgetStop],
) -> RenderedPrompt:
    rendered = render_prompt(prompt_name, changes, **values)
    check = input_budget.check_input_budget(rendered.text, settings, role=role, index=split.index)
    if check.stop is not None:
        budget_stops.append(check.stop.stored())
        raise _SplitStoppedError
    return rendered


def _rebuttal_material(
    rebuttals: dict[int, dict[str, Any]],
    working_directory: Path,
    split: _Split,
    *,
    allow_tools: bool,
) -> dict[str, object]:
    if not allow_tools and split.presented_lines is None:
        # 差分以外の静的な資料では、座標を資料と一緒に判定役へ渡す。
        return {"challenges_json": list(rebuttals.values()), "excerpts_json": []}
    # 位置と抜粋は excerpts_json に移し、challenges_json からは外す。
    return {
        "challenges_json": [
            {k: v for k, v in item.items() if k != "checked"} for item in rebuttals.values()
        ],
        "excerpts_json": _checked_excerpts(
            rebuttals, working_directory, split.excluded_excerpt_path_keys, split.presented_lines
        ),
    }


def _role_values(
    prompt_name: str,
    candidates: list[dict[str, Any]],
    shown: dict[str, dict[int, dict[str, Any]]],
    working_directory: Path,
    split: _Split,
    *,
    allow_tools: bool,
) -> dict[str, object]:
    values: dict[str, object] = {"candidates_json": _indexed(candidates)}
    for name, items in shown.items():
        values[_PLACEHOLDER_NAMES[name]] = list(items.values())
    if prompt_name == Role.JUDGE_REBUTTAL:
        values.update(
            _rebuttal_material(
                shown[Role.REBUTTAL], working_directory, split, allow_tools=allow_tools
            )
        )
    return values


def _missing_output_records(
    split_index: int, role: str, missing: list[int], still_missing: list[int], recovered: list[int]
) -> list[StoredFailure]:
    if still_missing:
        return [
            CandidatesUnverified(
                split_index,
                role,
                claude.FailureKind.BAD_OUTPUT,
                _missing_reason(role, still_missing),
                2,
                Disposition.RERUN_FAILED,
                candidates=tuple(still_missing),
            )
        ]
    if recovered:
        return [
            CandidatesUnverified(
                split_index,
                role,
                claude.FailureKind.BAD_OUTPUT,
                _missing_reason(role, missing),
                1,
                Disposition.RECOVERED,
                candidates=tuple(recovered),
            )
        ]
    return []


def _verify_split(
    split: _Split,
    *,
    changes: str,
    working_directory: Path,
    settings: claude.RunSettings,
    invocations: list[RoleInvocationRecord],
    budget_stops: list[input_budget.StoredBudgetStop],
    attempt: int = 1,
) -> SplitVerification:
    """分割を検証し、確定した結果を返す。

    役の出力が一部の候補の index を欠いたときは、欠けた候補だけでその役を一度走らせ直す。
    走らせ直しても欠けた候補は以後の役へ進めず、未検証として数える。
    """
    form, split_index, candidates = split.form, split.index, split.candidates
    schemas = role_schemas()
    # この試行の失敗の記録は、分割が確定するまで呼び出し側へ渡さない
    failures: list[StoredFailure] = []
    initial_kind = RerunKind.INITIAL if attempt == 1 else RerunKind.SPLIT
    # 分割の中の候補の番号。役ごとの出力はこの番号で持つ
    active = list(range(len(candidates)))
    outputs: dict[str, dict[int, RoleItem]] = {}
    left_unverified: list[UnverifiedCandidate] = []
    without_materials: set[int] = set()
    presented_lines = split.presented_lines

    def missing_material(number: int, role: str, message: str) -> None:
        failures.append(
            CandidatesUnverified(
                split_index,
                role,
                claude.FailureKind.OTHER,
                message,
                attempt,
                Disposition.NOT_RERUN,
                candidates=(number,),
            )
        )
        without_materials.add(number)
        left_unverified.append(
            UnverifiedCandidate(candidates[number], UnverifiedReason.MATERIALS_MISSING)
        )

    def available(numbers: Iterable[int]) -> list[int]:
        return [number for number in numbers if number not in without_materials]

    for number in _missing_candidates(candidates, presented_lines):
        missing_material(number, form, "提示資料に候補の根拠行がありません")

    def run_role(
        role: str, prompt_name: str, previous: tuple[str, ...], subset: list[int], kind: RerunKind
    ) -> dict[int, RoleItem]:
        """subset の候補だけで役を一度起動する。結果は分割の中の番号で返す。"""
        # プロンプトの index は subset の中の位置で振り、結果を返すときに分割の番号へ戻す
        local = dict(enumerate(subset))
        shown: dict[str, dict[int, dict[str, Any]]] = {
            name: {
                position: {**outputs[name][number].model_dump(mode="json"), "index": position}
                for position, number in local.items()
            }
            for name in previous
        }
        values = _role_values(
            prompt_name,
            [candidates[number] for number in subset],
            shown,
            working_directory,
            split,
            allow_tools=settings.allow_tools,
        )
        rendered = _render_checked(
            prompt_name, changes, values, settings, role, split, budget_stops
        )
        invocation = settings.invocation(
            prompt=rendered.text,
            json_schema=schemas[prompt_name],
            working_directory=working_directory,
        )
        try:
            result = settings.invoke(invocation)
        except claude.ClaudeInvocationError as error:
            invocations.append(
                RoleInvocationRecord(
                    role,
                    split_index,
                    error.record,
                    rendered.stamp,
                    kind,
                )
            )
            raise _SplitFailedError(str(error), error.kind, role=role) from error
        invocations.append(
            RoleInvocationRecord(role, split_index, result.record, rendered.stamp, kind)
        )
        indexed = _index_items(result.structured_output or {}, role, prompt_name)
        return _checked_role_items(
            indexed,
            local,
            presented_lines,
            lambda number, issue: missing_material(number, role, issue),
        )

    for role, prompt_name, previous in _roles_for(form):
        active = available(active)
        if not active:
            # 前の役で全ての候補が未検証になった。起動する候補が無い
            break
        outputs[role] = run_role(role, prompt_name, previous, active, initial_kind)
        missing = available(number for number in active if number not in outputs[role])
        if missing:
            rerun = RerunKind.MISSING_CANDIDATES
            outputs[role].update(run_role(role, prompt_name, previous, missing, rerun))
            still_missing = available(number for number in missing if number not in outputs[role])
            recovered = [number for number in missing if number in outputs[role]]
            failures.extend(
                _missing_output_records(split_index, role, missing, still_missing, recovered)
            )
            # 走らせ直しても欠けた候補は棄却も確定もせず、未検証のまま以後の役から外す
            left_unverified += [
                UnverifiedCandidate(candidates[number], UnverifiedReason.STILL_MISSING)
                for number in still_missing
            ]
            active = [number for number in active if number not in still_missing]

    findings = _adjudicate_active(form, outputs, available(active), candidates, working_directory)
    return SplitVerification(tuple(findings), tuple(failures), tuple(left_unverified))


def _adjudicate_active(
    form: Form,
    outputs: dict[str, dict[int, RoleItem]],
    active: list[int],
    candidates: tuple[dict[str, Any], ...],
    working_directory: Path,
) -> list[VerifiedCandidate]:
    """未検証に落ちなかった候補ごとに裁定を作る。

    役の出力は入口で役の型により検証済みなので、裁定のモデルに合わない値はふつう来ない。
    位置の正規化の後に合わなくなった値は、入口の検証の失敗と同じく分割の失敗にする。
    """
    findings: list[VerifiedCandidate] = []
    for number in active:
        try:
            adjudication = pipeline.adjudication_from_model_output(
                _debate_output(form, outputs, number), working_directory
            )
        except ValidationError as error:
            first = error.errors()[0]
            location = ".".join(str(part) for part in first["loc"])
            raise _SplitFailedError(
                f"裁定が裁定の型に合いません: {location}: {first['msg']}",
                claude.FailureKind.BAD_OUTPUT,
                role=_JUDGE_ROLE,
            ) from error
        findings.append(
            VerifiedCandidate(
                _as_nit_if_disclosed(
                    candidates[number], adjudication, outputs[_JUDGE_ROLE][number]
                ),
                adjudication,
            )
        )
    return findings


def _as_nit_if_disclosed(
    candidate: dict[str, Any], adjudication: Adjudication, verdict_item: RoleItem
) -> dict[str, Any]:
    """棄却の判定が開示の場所を添えたとき、候補を種類つきの nit に直して返す。

    disclosed_at は rejected のときだけ読む。ほかの verdict に付いた分は、verdict が
    指摘の扱いを決めているので読み捨てる。
    """
    place = cast("JudgeRebuttalItem | JudgeAlternativeItem", verdict_item).disclosed_at
    # 欠けた欄は MISSING で、書かれた欄は空でない文字列
    if adjudication.verdict is not Verdict.REJECTED or not isinstance(place, str):
        return candidate
    return disclosed.disclosed_as_nit({**candidate, KEY_DISCLOSED_AT: place})


def _debate_output(form: Form, outputs: dict[str, dict[int, RoleItem]], i: int) -> dict[str, Any]:
    if form is Role.REBUTTAL:
        rebuttal = cast("RebuttalItem", outputs[Role.REBUTTAL][i])
        judged = cast("JudgeRebuttalItem", outputs[_JUDGE_ROLE][i])
        return {
            "kind": Role.REBUTTAL,
            "challenge": rebuttal.challenge,
            "recheck": [place.model_dump() for place in judged.recheck],
            "verdict": judged.verdict,
            "reason": judged.reason,
            "verified": judged.verified,
            "tool_claim": judged.tool_claim.model_dump(mode="json"),
        }
    alternative = cast("AlternativeItem", outputs[Role.ALTERNATIVE][i])
    defense = cast("DefenseItem", outputs[Role.DEFENSE][i])
    judged_alternative = cast("JudgeAlternativeItem", outputs[_JUDGE_ROLE][i])
    return {
        "kind": Role.ALTERNATIVE,
        "alternative": alternative.alternative,
        "defense": defense.defense,
        "verdict": judged_alternative.verdict,
        "reason": judged_alternative.reason,
    }


def verify(
    candidates: list[dict[str, Any]],
    *,
    group: GroupId,
    changes: str | VerificationChanges | Callable[[list[dict[str, Any]]], str],
    working_directory: Path,
    settings: claude.RunSettings,
    presented_lines: shard_material.EvidenceLines | None = None,
) -> VerificationOutcome:
    findings: list[VerifiedCandidate] = []
    invocations: list[RoleInvocationRecord] = []
    failed_splits = 0
    unverified: list[UnverifiedCandidate] = []
    budget_stops: list[input_budget.StoredBudgetStop] = []
    failures: list[StoredFailure] = []
    excluded_path_keys: frozenset[str] = frozenset()
    if isinstance(changes, VerificationChanges):
        excluded_path_keys = frozenset(map(_excerpt_path_key, changes.excluded_excerpt_paths))
        changes = changes.text
    for split_index, (form, split) in enumerate(split_candidates(candidates)):
        materials = changes(split) if callable(changes) else changes
        # 走らせ直しの契機になった失敗。次の実行の結果で扱いが決まるまで確定しない
        pending: _SplitFailedError | None = None
        for attempt in range(1, MAX_SPLIT_ATTEMPTS + 1):
            try:
                verified = _verify_split(
                    _Split(
                        split_index,
                        form,
                        tuple(split),
                        group,
                        presented_lines,
                        excluded_path_keys,
                    ),
                    changes=materials,
                    working_directory=working_directory,
                    settings=settings,
                    invocations=invocations,
                    budget_stops=budget_stops,
                    attempt=attempt,
                )
            except _SplitStoppedError:
                # 停止の記録は budget_stops に済んでいる。起動していないので停止そのものには
                # 失敗の記録を付けず、発見役の同じ停止と同じく、
                # 分割を失敗に数えて候補を未検証にする。
                # 走らせ直しの途中の停止なら、契機の失敗は停止に隠れるので、ここで残す
                _record_pending(
                    failures, split_index, attempt - 1, pending, Disposition.RERUN_STOPPED
                )
                failed_splits += 1
                unverified += [
                    UnverifiedCandidate(c, UnverifiedReason.INPUT_ESTIMATE_STOPPED) for c in split
                ]
            except _SplitFailedError as error:
                # 走らせ直しは一時の失敗に限り、分割ごとに 1 回まで。途中まで走った役の起動は
                # invocations に残るので、金額と起動の回数に走らせ直した分が入る
                _record_pending(
                    failures, split_index, attempt - 1, pending, Disposition.RERUN_FAILED
                )
                if error.kind.transient and attempt < MAX_SPLIT_ATTEMPTS:
                    pending = error
                    continue
                failures.append(_failure(split_index, attempt, error, Disposition.NOT_RERUN))
                failed_splits += 1
                unverified += [
                    UnverifiedCandidate(c, UnverifiedReason.LAUNCH_FAILED) for c in split
                ]
            else:
                findings += verified.findings
                failures += verified.failures
                unverified += verified.unverified
                if verified.unverified:
                    # 一部の候補が未検証で残った分割は、被覆の不完全に数える
                    failed_splits += 1
                _record_pending(failures, split_index, attempt - 1, pending, Disposition.RECOVERED)
            # 成功したとき、および走らせ直さない失敗のあとは、この分割を終える
            break
    return VerificationOutcome(
        tuple(findings),
        tuple(invocations),
        failed_splits,
        tuple(budget_stops),
        tuple(failures),
        tuple(unverified),
    )


def _record_pending(
    failures: list[StoredFailure],
    split_index: int,
    attempt: int,
    pending: _SplitFailedError | None,
    disposition: Disposition,
) -> None:
    """走らせ直しの契機になった失敗があれば、扱いを添えて残す。"""
    if pending is None:
        # 1 回目の試行の結果なので、契機の失敗は無い。
        return
    failures.append(_failure(split_index, attempt, pending, disposition))


def _failure(
    split_index: int,
    attempt: int,
    error: _SplitFailedError,
    disposition: Disposition,
) -> VerificationLaunchFailed:
    return VerificationLaunchFailed(
        split_index,
        error.role,
        error.kind,
        error.reason,
        attempt,
        disposition,
    )
