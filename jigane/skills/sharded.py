"""シャードに分けた群（実装、セキュリティ）の共通の実行器。

発見役の起動、並列化、失敗の集計、範囲外の除外、検証、番号付けとスキーマの検証、
`run_state` の組み立て、保存はここだけが持つ。群は `ShardedGroup` で違いだけを渡す。
"""

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, assert_never

from pydantic import BaseModel, ValidationError

from jigane.design.division import division_text
from jigane.findings.discard import KEY_FROM_CONCERN, PassedConcern, ShardConcerns
from jigane.findings.records import (
    GroupResult,
    StoredFinding,
    ThemeUpdate,
    Triage,
    TriagedFinding,
    concerns_section,
    load_findings_state,
    passed_concerns,
    read_stored_state,
    triaged,
)
from jigane.groups.definitions import GROUP_BY_ID, MACRO, MICRO, GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.launch_failure import (
    DISCOVERY_ROLE,
    DiscoveryLaunchFailed,
    Disposition,
    StoredInvocation,
)
from jigane.llm_runs.prompt_stamp import PromptStamp
from jigane.policy_materials.design_doc import (
    SECTION_DOCUMENT,
    SECTION_MAIN_DOCUMENT,
    SECTION_SELECTION,
    SECTION_STRUCTURE,
    attachment_sections,
)
from jigane.reviews.attachments import VersionDrift, drift_notice
from jigane.reviews.lifecycle import Review, scope_files
from jigane.skills import (
    adversarial,
    bundle_disclosed,
    config_head,
    disclosed,
    history,
    input_budget,
    macro,
    order,
    pipeline,
    policy_regex,
    scope,
    shards,
    shared_rules,
)
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import ShardedEstimate
from jigane.skills.mcp import NO_MCP, McpSelection
from jigane.skills.shards import Shard

DIFF_INTRO = macro.DIFF_CHANGES_INTRO
FEATURE_INTRO = "変更前は空。各ファイルの本文を `=== パス ===` の見出しの下に載せる。"
# 設計書の計画の導入文。受け持ちの表の行は群の名前で引き、文は群によらず同じ一か所に置く
DOCUMENT_SHARD_INTRO = (
    "設計書のレビューの設計書のシャードである。担当のファイルは設計書そのもので、"
    "本文は下の「設計書の資料」の節にある。受け持ちの表で{group}の行に当たる懸念のうち、"
    "設計書だけで決まるものを問う。根拠の位置は設計書の行にする。"
)
DESIGN_FILE_SHARD_INTRO = (
    "設計書のレビューのファイルのシャードである。担当のファイルは選んだコードの本文で、"
    "設計書の全文と、そのファイルを名指した節と推しの理由は、下の「設計書の資料」の節にある。"
    "受け持ちの表で{group}の行に当たる懸念のうち、設計書とこのファイルを突き合わせて初めて"
    "分かるものだけを問う。設計書だけで決まる懸念は、設計書のシャードが問うので、ここでは問わない。"
    "根拠の位置は、設計書の行かこのファイルの行にする。"
)


@dataclass(frozen=True)
class Selection:
    """選別の段の結果。findings は区分の確定した指摘で、theme_update は保存へ渡す。

    records と run_state_extra は実行の記録へ足す。
    """

    findings: tuple[TriagedFinding, ...]
    theme_update: ThemeUpdate | None = None
    records: tuple[StoredInvocation, ...] = ()
    run_state_extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SelectContext:
    working_directory: Path
    settings: claude.RunSettings


# 引数は kept（検証を通った指摘）、直前までのテーマ番号、文脈。選別の段が区分を確定して返す
Select = Callable[[list[StoredFinding], int, SelectContext], Selection]


@dataclass(frozen=True)
class ShardedGroup:
    group: GroupId
    schema: dict[str, Any]
    # 入口の検証に使う。schema と同じ形で、location.path の形の決まりだけを外してある
    lenient_model: type[BaseModel]
    tools: tuple[str, ...]
    select: Select
    mcp_config: Path | None = None
    allowed_mcp_tools: tuple[str, ...] = ()
    # 使う MCP。プロンプトに差し込み、実行の記録に残す
    mcp: McpSelection = NO_MCP


def withheld_notice(paths: tuple[str, ...]) -> str | None:
    """送らなかった秘密のファイルを知らせる文。無ければ None。"""
    if not paths:
        return None
    return (
        f"秘密のファイル名に当たるため、レビューの対象から除きました: {', '.join(paths)}。"
        "これらの本文はレビューされません"
    )


@dataclass(frozen=True)
class ShardedRun:
    findings: tuple[TriagedFinding, ...]
    run_state: pipeline.ShardedGroupRun
    # 実行を止めないが利用者へ知らせる、保存した版との食い違い。出し方は呼び側（CLI）が決める
    drifts: tuple[VersionDrift, ...] = ()
    # 秘密のファイル名に当たるため、プロンプトに載せず送らなかったパス
    withheld: tuple[str, ...] = ()

    @property
    def notice(self) -> str | None:
        notices = [drift_notice(self.drifts), withheld_notice(self.withheld)]
        present = [notice for notice in notices if notice]
        return "\n".join(present) if present else None

    @property
    def failed_splits(self) -> int:
        return self.run_state.failed_splits or 0

    @property
    def failed_shards(self) -> int:
        return self.run_state.failed_shards


@dataclass(frozen=True)
class _ShardResult:
    shard_index: int
    discovered: tuple[dict[str, Any], ...] | None
    record: claude.InvocationRecord | None
    stamp: PromptStamp
    # 入力の見積もりで起動しなかったときの記録。起動したシャードは None
    stop: input_budget.BudgetStop | None = None
    # 発見役が失敗したときの理由。起動しなかったシャードと成功したシャードは None
    failure: DiscoveryLaunchFailed | None = None


def _evidence_paths(finding: TriagedFinding) -> set[str]:
    return {location["path"] for location in finding.finding.extra.get("evidence", [])}


def _document_sections(
    shard: shards.DesignDocumentShard | shards.DesignFileShard,
) -> list[tuple[str, str]]:
    """主の設計書の全文と、付属の文書。付属があるときは主の見出しを替えて見分ける。"""
    title = SECTION_MAIN_DOCUMENT if shard.attachments else SECTION_DOCUMENT
    return [(title, shard.document), *attachment_sections(shard.attachments)]


def _design_text(titled: list[tuple[str, str]]) -> str:
    body = "\n\n".join(f"#### {title}\n\n{text}" for title, text in titled)
    return f"\n\n### 設計書の資料\n\n{body}"


def _design_insert(shard: Shard) -> str:
    """雛形の `$design` に差し込む設計書の資料。設計書の計画以外は空。"""
    match shard:
        case shards.DesignDocumentShard():
            return _design_text(
                [
                    *_document_sections(shard),
                    (SECTION_STRUCTURE, shard.structure),
                    ("受け持ちの表", division_text()),
                ]
            )
        case shards.DesignFileShard():
            return _design_text(
                [
                    *_document_sections(shard),
                    ("受け持ちの表", division_text()),
                    (SECTION_SELECTION, shard.selection),
                ]
            )
        case shards.DiffShard() | shards.FullTextShard():
            return ""
        case _:
            assert_never(shard)


def _shard_receives(
    finding: TriagedFinding, shard: shards.DesignDocumentShard | shards.DesignFileShard
) -> bool:
    """設計書の計画で、懸念を受けるシャード。根拠のファイルを持つファイルのシャードだけが受ける。

    根拠がファイルのシャードのどれにも当たらない懸念は、設計書のシャードが受ける。
    """
    paths = _evidence_paths(finding)
    match shard:
        case shards.DesignFileShard():
            return bool(paths & set(shard.paths))
        case shards.DesignDocumentShard():
            return not paths & shard.covered
        case _:
            assert_never(shard)


def prompt_name_of(group: GroupId) -> str:
    """シャードに分ける群の雛形の名前。雛形の名前は群から一か所で引く。"""
    match group:
        case GroupId.MICRO:
            return "micro"
        case GroupId.SECURITY:
            return "security"
        case GroupId.MACRO:
            raise ValueError("方針の群はシャードに分けません")
        case _:
            assert_never(group)


def shard_concern_findings(group: GroupId, review: Review, shard: Shard) -> list[TriagedFinding]:
    """このシャードが受ける、方針の結果の指摘。懸念を渡さない群は空。

    プロンプトの節と実行の記録は、この一度の読み込みの結果から作る。
    """
    match group:
        case GroupId.MICRO:
            pass
        case GroupId.SECURITY:
            # セキュリティは方針の群から懸念を受け取らない。雛形に節の置き場も無い
            return []
        case GroupId.MACRO:
            raise ValueError("方針の群はシャードに分けません")
        case _:
            assert_never(group)
    macro_findings = list(load_findings_state(review.id.directory()).groups.get(MACRO, ()))
    match shard:
        case shards.DesignDocumentShard() | shards.DesignFileShard():
            macro_findings = [f for f in macro_findings if _shard_receives(f, shard)]
        case shards.DiffShard() | shards.FullTextShard():
            # 設計書の計画ではないので、懸念を振り分けるシャードの種類が無い。すべて受ける
            pass
        case _:
            assert_never(shard)
    return macro_findings


def _shard_intro(shard: Shard, group: GroupId) -> str:
    label = GROUP_BY_ID[group].label
    match shard:
        case shards.DiffShard():
            return DIFF_INTRO
        case shards.FullTextShard():
            return FEATURE_INTRO
        case shards.DesignDocumentShard():
            return DOCUMENT_SHARD_INTRO.format(group=label)
        case shards.DesignFileShard():
            return DESIGN_FILE_SHARD_INTRO.format(group=label)
        case _:
            assert_never(shard)


def _history_text(group: GroupId, assembly: AssemblyInput, shard: Shard) -> str:
    """前の回の実装の指摘のうち、担当のファイルに関わるものだけ。無ければ空。"""
    match group:
        case GroupId.MICRO:
            return history.shard_history_text(assembly, MICRO, shard.paths)
        case GroupId.SECURITY:
            # 雛形に節の置き場が無い。置き場があるのは実装のレビューだけ
            return history.NO_HISTORY_TEXT
        case GroupId.MACRO:
            raise ValueError("方針の群はシャードに分けません")
        case _:
            assert_never(group)


def mcp_note(mcp: McpSelection) -> str:
    """プロンプトの $mcp_note に差し込む、MCP の有無の説明。置き場の無い雛形では使われない。"""
    if mcp.servers:
        return (
            f"次の MCP が使える: {', '.join(mcp.names)}。体系の項目はこれらの MCP の道具で引いて"
            "本文を読み、確かめた識別子を references に書く。記憶だけで書かない。"
        )
    return (
        "MCP は渡されていない。体系の識別子は知識から書いてよいが、確かめられない項目は書かない。"
        "識別子を示せない指摘は出さない。"
    )


def mcp_guide_note(mcp: McpSelection) -> str:
    """プロンプトの $mcp_guide に差し込む、MCP の使い分けの節。無ければ空で、行も残さない。"""
    # load が全てに入れるか全て None にするので、先頭を見れば足りる
    if not mcp.servers or mcp.servers[0].use_for is None:
        return ""
    lines = "\n".join(f"- {server.name}: {server.use_for}" for server in mcp.servers)
    return f"\n\n### MCP の使い分け\n\n{lines}"


def shard_concerns(group: GroupId, review: Review, shard: Shard) -> ShardConcerns:
    """このシャードに渡す懸念。findings.json を読み、渡す懸念を選ぶ。"""
    return passed_concerns(shard_concern_findings(group, review, shard))


def build_shard_prompt(
    group: GroupId,
    assembly: AssemblyInput,
    shard: Shard,
    number: int,
    total: int,
    *,
    concerns: ShardConcerns,
    mcp: McpSelection = NO_MCP,
) -> str:
    """concerns はこのシャードに渡す懸念。shard_concerns が一度だけ組み立てた値を受ける。"""
    review = assembly.review
    prompt_name = prompt_name_of(group)
    common = adversarial.common_prompt()
    ordered = sorted(shard.units, key=lambda unit: unit.path)
    content = "\n".join(unit.text for unit in ordered)
    match shard:
        case shards.DiffShard():
            heads = config_head.config_heads_section(Path(review.repository_root), shard.paths)
            if heads:
                content += "\n" + heads
        case shards.FullTextShard() | shards.DesignFileShard() | shards.DesignDocumentShard():
            # 全文と設計書のシャードは、ファイルを冒頭ごと渡している。冒頭を欠くのは差分だけ
            pass
        case _:
            assert_never(shard)
    return adversarial.load_prompt(prompt_name).substitute(
        common=common,
        **shared_rules.values_for(prompt_name),
        mode=review.mode,
        shard_number=number,
        shard_total=total,
        shard_files="\n".join(f"- {path}" for path in shard.paths),
        shard_intro=_shard_intro(shard, group),
        shard_content=content,
        shard_checks=policy_regex.shard_checks(prompt_name, review, shard.paths),
        design=_design_insert(shard),
        macro_concerns=concerns_section(concerns),
        history=_history_text(group, assembly, shard),
        mcp_note=mcp_note(mcp),
        mcp_guide=mcp_guide_note(mcp),
    )


def build_shard_prompts(
    group: GroupId,
    assembly: AssemblyInput,
    shard_list: Sequence[Shard],
    concern_list: list[ShardConcerns],
    mcp: McpSelection,
) -> list[str]:
    """シャードごとの発見役のプロンプト。実行と見積もりが同じ組み立てを使うための入口。"""
    total = len(shard_list)
    return [
        build_shard_prompt(
            group, assembly, shard, index + 1, total, concerns=concern_list[index], mcp=mcp
        )
        for index, shard in enumerate(shard_list)
    ]


def estimate_sharded[E: ShardedEstimate](
    estimate_type: type[E],
    assembly: AssemblyInput,
    group: GroupId,
    note: str,
    mcp: McpSelection = NO_MCP,
) -> E:
    """シャードを持つ群の見積もり。prompt_bytes は実行と同じ build_shard_prompts で求める。"""
    review = assembly.review
    plan = shards.build_plan(review, group)
    shard_list = plan.shards
    sizes = [shard.size for shard in shard_list]
    concern_list = [shard_concerns(group, review, shard) for shard in shard_list]
    prompts = build_shard_prompts(group, assembly, shard_list, concern_list, mcp)
    return estimate_type(
        discovery_input_tokens=claude.estimate_input_tokens(sum(sizes)),
        past_run=pipeline.past_run(review, group),
        note=note,
        policy_notice=order.policy_notice(review),
        target_files=len(plan.units),
        shard_bytes=tuple(sizes),
        prompt_bytes=tuple(len(prompt.encode("utf-8")) for prompt in prompts),
    )


def _discovery_failure(index: int, kind: claude.FailureKind, reason: str) -> DiscoveryLaunchFailed:
    # 発見役は走らせ直さない。一時の失敗でも 1 回で打ち切る
    return DiscoveryLaunchFailed(index, DISCOVERY_ROLE, kind, reason, 1, Disposition.NOT_RERUN)


def _discover_shard(
    spec: ShardedGroup,
    review: Review,
    prompt: str,
    index: int,
    settings: claude.RunSettings,
) -> _ShardResult:
    stamp = adversarial.stamp_prompt(prompt_name_of(spec.group), prompt)
    check = input_budget.check_input_budget(prompt, settings, role=DISCOVERY_ROLE, index=index)
    if check.stop is not None:
        return _ShardResult(index, None, None, stamp, check.stop)
    invocation = settings.invocation(
        prompt=prompt,
        json_schema=spec.schema,
        working_directory=Path(review.repository_root),
        tools=spec.tools,
        mcp_config=spec.mcp_config,
        allowed_mcp_tools=spec.allowed_mcp_tools,
    )
    try:
        result = settings.invoke(invocation)
    except claude.ClaudeInvocationError as error:
        failure = _discovery_failure(index, error.kind, str(error))
        return _ShardResult(
            index,
            None,
            error.record,
            stamp,
            failure=failure,
        )
    output = result.structured_output or {}
    try:
        validated = spec.lenient_model.model_validate(output)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        failure = _discovery_failure(
            index,
            claude.FailureKind.BAD_OUTPUT,
            f"出力が schema に合いません: {location}: {first['msg']}",
        )
        return _ShardResult(index, None, result.record, stamp, failure=failure)
    # 下流は辞書を前提にする。辞書に戻すのはここ一度だけで、MISSING の欄はキーが無くなる
    checked = validated.model_dump(mode="json")
    return _ShardResult(index, tuple(checked["findings"]), result.record, stamp)


def _in_scope(finding: dict[str, Any], paths: set[str]) -> bool:
    return any(location["path"] in paths for location in finding["evidence"])


def _drop_unsent_concern(
    finding: dict[str, Any], sent: tuple[PassedConcern, ...], shard_number: int
) -> dict[str, Any]:
    """そのシャードで渡していない番号の from_concern を落とす。"""
    claimed = finding.get(KEY_FROM_CONCERN)
    if claimed is None or claimed in (concern.id for concern in sent):
        return finding
    return pipeline.without_from_concern(
        finding, f"シャード {shard_number} の指摘が、渡していない懸念 {claimed} を書きました"
    )


def _finder_keys(schema: dict[str, Any]) -> frozenset[str]:
    """発見役に見せたスキーマが、指摘に許す鍵。"""
    return frozenset(schema["properties"]["findings"]["items"]["properties"])


def _model_written_only(finding: dict[str, Any], finder_keys: frozenset[str]) -> dict[str, Any]:
    """発見役に見せたスキーマの鍵だけを残す。

    保存の形の欄（nit_reason、triage_reason、out_of_scope など）は、ここで落とす。
    発見役の出力から保存の形へ入る境はここだけで、nit_reason は disclosed_as_nit が作る。
    """
    return {key: value for key, value in finding.items() if key in finder_keys}


def _prepare(
    results: list[_ShardResult],
    shard_list: Sequence[Shard],
    working_directory: Path,
    drops: pipeline.DropCounts,
    sent_concerns: list[tuple[PassedConcern, ...]],
    *,
    finder_keys: frozenset[str],
) -> tuple[list[dict[str, Any]], int]:
    candidates: list[dict[str, Any]] = []
    outside = 0
    for result in results:
        if result.discovered is None:
            continue
        paths = shards.scope_paths(shard_list[result.shard_index])
        prepared = pipeline.prepare_candidates(
            [_model_written_only(finding, finder_keys) for finding in result.discovered],
            working_directory,
            has_bot_findings=False,
            drops=drops,
        )
        for finding in map(disclosed.disclosed_as_nit, prepared):
            if _in_scope(finding, paths):
                candidates.append(
                    _drop_unsent_concern(
                        finding, sent_concerns[result.shard_index], result.shard_index + 1
                    )
                )
            else:
                outside += 1
    return candidates, outside


def _changes_with_config_heads(review: Review, plan: shards.ShardPlan) -> str:
    """検証役に渡す変更の節。差分の計画では、設定ファイルの冒頭の設定を添える。"""
    if plan.kind is not shards.ShardPlanKind.DIFF:
        # 全文と設計書の計画は、ファイルを冒頭ごと渡している
        return macro.changes_prompt_text(review)
    changes = macro.verifier_changes_text(review)
    heads = config_head.config_heads_section(
        Path(review.repository_root), [unit.path for unit in plan.units]
    )
    return f"{changes}\n\n{heads}" if heads else changes


def run_sharded(
    spec: ShardedGroup,
    assembly: AssemblyInput,
    settings: claude.RunSettings,
) -> ShardedRun:
    """assembly の履歴は縁が読んだ値で、資料の組み立てで読み直さない。"""
    review = assembly.review
    started = time.monotonic()
    working_directory = Path(review.repository_root)
    plan = shards.build_plan(review, spec.group)
    units = plan.units
    shard_list = plan.shards
    total = len(shard_list)

    shard_concern_list = [shard_concerns(spec.group, review, shard) for shard in shard_list]
    sent_concerns = [item.passed for item in shard_concern_list]
    results: list[_ShardResult] = []
    if total:
        prompts = build_shard_prompts(
            spec.group, assembly, shard_list, shard_concern_list, spec.mcp
        )
        with ThreadPoolExecutor(max_workers=shards.MAX_SHARDS) as pool:
            futures = [
                pool.submit(_discover_shard, spec, review, prompt, index, settings)
                for index, prompt in enumerate(prompts)
            ]
            results = [future.result() for future in futures]
    failed = [r for r in results if r.discovered is None]
    stops = [r.stop for r in results if r.stop is not None]
    if total and len(failed) == total:
        if stops:
            # 起動しなかった理由が失敗の原因なので、一つめの文を添える。
            # 全シャードが同じ上限で止まる
            raise pipeline.InputBudgetError(
                spec.group,
                f"全 {total} シャードの発見役が失敗しました"
                f"（{len(stops)} 個は起動しませんでした）。" + stops[0].message(),
                stops[0],
            )
        raise pipeline.DiscoveryError(spec.group, f"全 {total} シャードの発見役が失敗しました")

    drops = pipeline.DropCounts()
    candidates, outside = _prepare(
        results,
        shard_list,
        working_directory,
        drops,
        sent_concerns,
        finder_keys=_finder_keys(spec.schema),
    )
    # nit は検証の費用に見合わない（実装のレビューの指摘の 66% が nit）ので、検証役を起動せず
    # nit のまま示す。adjudication が None なのは「検証していない」の値で、方針の群と同じ。
    # 範囲外の指摘も検証役にかけない。方針の群は検証を行わず、
    # 範囲外は直す前に人が判断する印なので、
    # 検証の費用に見合わない。adjudication が None の範囲外のまま示す。
    review_scope = scope_files(review)
    split = scope.split_by_scope(candidates, review_scope)
    out_of_scope_candidates = [outside.shaped() for outside in split.out_of_scope]
    in_scope_candidates = split.in_scope
    nit_candidates = [c for c in in_scope_candidates if c["severity"] == "nit"]
    to_verify = [c for c in in_scope_candidates if c["severity"] != "nit"]
    outcome = adversarial.verify(
        to_verify,
        group=spec.group,
        changes=_changes_with_config_heads(review, plan),
        working_directory=working_directory,
        settings=settings,
    )

    kept = pipeline.keep_valid(
        review,
        [
            *(pipeline.ValidatedCandidate(v.candidate, v.adjudication) for v in outcome.findings),
            *(pipeline.ValidatedCandidate(c, None) for c in nit_candidates),
            *(pipeline.ValidatedCandidate(c, None) for c in out_of_scope_candidates),
        ],
        drops,
        lambda candidate: {
            **candidate,
            "group": spec.group,
            "theme": None,
            "triage": None,
        },
    )
    # 選別の前に置く。テーマに束ねる段は nit を一つの束にするだけで、同じ限界は見分けないため
    bundled = bundle_disclosed.bundle_disclosed(
        kept, working_directory=working_directory, settings=settings
    )
    selection = spec.select(
        list(bundled.findings),
        read_stored_state(review.id.directory()).last_theme_number,
        SelectContext(working_directory, settings),
    )

    # 判定の付かなかった候補は、選別に通さず未検証の指摘として F 番号を振って残す。
    # 重さが nit の候補は検証にかけないので、ここには入らない
    unverified_findings = [
        triaged(spec.group, finding)
        for finding in pipeline.keep_valid(
            review,
            [pipeline.ValidatedCandidate(u.candidate, None) for u in outcome.unverified],
            drops,
            lambda candidate: {
                **candidate,
                "group": spec.group,
                "theme": None,
                "triage": Triage.UNVERIFIED.value,
            },
        )
    ]

    failed_indexes = {r.shard_index for r in failed}
    reviewed_files = sum(
        len(shard.units) for i, shard in enumerate(shard_list) if i not in failed_indexes
    )
    records = [
        pipeline.stored_invocation(
            DISCOVERY_ROLE,
            r.shard_index,
            r.record,
            r.stamp,
            concerns=shard_concern_list[r.shard_index],
        )
        for r in results
        if r.record is not None
    ]
    head = pipeline.run_state_head(
        pipeline.ShardedGroupRun,
        settings=settings,
        invocations=[
            *records,
            *(
                pipeline.stored_invocation(
                    item.role, item.split_index, item.record, item.stamp, item.rerun
                )
                for item in outcome.invocations
            ),
            *selection.records,
            *([] if isinstance(bundled, bundle_disclosed.TooFewTargets) else [bundled.record]),
        ],
        discovered=sum(len(r.discovered or []) for r in results),
        drops=drops,
        elapsed_s=time.monotonic() - started,
    )
    run_state = replace(
        head,
        others={
            **selection.run_state_extra,
            **(
                {}
                if isinstance(bundled, bundle_disclosed.TooFewTargets)
                else {"disclosed_bundling": bundled.to_dict()}
            ),
        },
        dropped_outside_shard=outside,
        failed_splits=outcome.failed_splits,
        launch_failures=(
            *(r.failure for r in failed if r.failure is not None),
            *outcome.failures,
        ),
        unverified_candidates=outcome.unverified_candidates,
        mcp=pipeline.McpRecord(spec.mcp.names),
        shards=total,
        succeeded_shards=total - len(failed),
        target_files=len(units),
        reviewed_files=reviewed_files,
        budget_stops=(
            *(stop.as_record() for stop in stops),
            *outcome.budget_stops,
        ),
    )
    pipeline.save_run(
        review,
        GroupResult((*selection.findings, *unverified_findings), selection.theme_update),
        run_state,
        spec.group,
    )
    findings = (*selection.findings, *unverified_findings)
    return ShardedRun(findings, run_state, plan.drifts, plan.withheld)
