"""方針のスキル（群の名前は macro）。道具を渡さない一回の起動で、図と表だけから外形を問う。"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, assert_never

from pydantic import BaseModel, ValidationError

from jigane.design.review_documents import read_attachments, read_document, version_drifts
from jigane.findings.discard import (
    KEY_FROM_CONCERN,
    Considered,
    DiscardedCandidate,
)
from jigane.findings.discarded_nits import discarded_nit_candidate
from jigane.findings.records import GroupResult, TriagedFinding, triaged
from jigane.groups.definitions import GROUP_BY_ID, MACRO
from jigane.groups.policy_runs import PolicyShape, role_of
from jigane.judgment_map.changes import CHANGES_FILE, without_whole_deletions
from jigane.judgment_map.declaration import demote_headings
from jigane.llm_runs import claude
from jigane.llm_runs.finding_model import (
    RoleShape,
    role_findings_model,
)
from jigane.llm_runs.launch_failure import (
    DiscoveryLaunchFailed,
    Disposition,
    FailedInvocation,
    RunRecordKind,
    StoredInvocation,
)
from jigane.llm_runs.prompt_stamp import PromptStamp
from jigane.policy_materials import changed_files
from jigane.policy_materials.design_doc import design_materials
from jigane.policy_materials.missing import MissingMaterial, missing_texts
from jigane.pull_request_context.background import (
    COMMIT_ABBREVIATION_LENGTH,
    OWNERLESS_TEXT,
    PastCommit,
    PastPullRequest,
    has_owners,
)
from jigane.pull_request_context.bots import read_bot_comments
from jigane.reviews.attachments import drift_notice
from jigane.reviews.lifecycle import (
    DesignDocOrigin,
    FeatureOrigin,
    PullRequestOrigin,
    RangeOrigin,
    Review,
    ReviewError,
    load_pairs,
    scope_files,
)
from jigane.sequence import review_diagrams
from jigane.skills import (
    adversarial,
    history,
    input_budget,
    macro_shared,
    output_estimate,
    pipeline,
    policy_figures,
    policy_repeats,
    policy_tree,
)
from jigane.skills import scope as scope_marks
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import (
    DesignPolicyEstimate,
    DiagramPolicyEstimate,
    PolicyEstimate,
)
from jigane.skills.macro_design import build_design_prompt, stamp_design_prompt
from jigane.skills.output_models import json_schema_of
from jigane.storage.store import review_lock
from jigane.technology_list import materials as technology_materials

GROUP = MACRO
# 発見役と検証役を持つ他の群（micro、security）が、道具の組を借りる
DISCOVERY_TOOLS = ("Read", "Grep", "Glob")

DIAGRAM_PROMPT = "macro_diagram"
DIAGRAM_TOOLS = macro_shared.DIAGRAM_TOOLS
SECTION_HISTORY = macro_shared.SECTION_HISTORY
NO_HISTORY_TEXT = history.NO_HISTORY_TEXT
VIEWPOINTS = ("M0", "M1", "M2", "M3", "M4", "M5", "M6")
DECLARATION_TOP_LEVEL = 4
NO_DECLARATION_TEXT = macro_shared.NO_DECLARATION_TEXT
NO_BOT_FINDINGS_TEXT = "無い。M5 は出さない。"
FEATURE_CHANGES_TEXT = "変更前は空。対象のファイルを作業ディレクトリから読むこと。"
DIFF_CHANGES_INTRO = (
    "変更前は merge-base、変更後は作業ディレクトリのツリー。差分に無い文脈は"
    "作業ディレクトリから読むこと。`---` は変更前、`+++` は変更後の見出し。"
)


@dataclass(frozen=True)
class MacroRun:
    findings: tuple[TriagedFinding, ...]
    run_state: pipeline.PolicyGroupRun
    # 実行を止めないが利用者へ知らせる文。出し方は呼び側（CLI）が決める
    notice: str | None = None


def _heading(text: str) -> str:
    # 本文が空の抜粋は見出しが無いため、位置だけを挙げる
    lines = text.splitlines()
    return lines[0] if lines else ""


def declaration_prompt_text(review: Review) -> str:
    """発見役のプロンプトに入れる宣言。抜粋は本文を埋め込まず、位置と見出しだけを挙げる。"""
    declaration = review.declaration
    if declaration.intent is not None:
        # 本文の見出しは、プロンプトの節の見出し（###）より下の階層から始める
        return demote_headings(declaration.intent, DECLARATION_TOP_LEVEL)
    if not declaration.excerpts:
        return NO_DECLARATION_TEXT
    # 抜粋の本文はレビュアーが作業ディレクトリから読めるため、
    # 同じ内容をプロンプトで重ねて送らない
    entries = "\n".join(
        f"- {excerpt.path}:{excerpt.start_line}-{excerpt.end_line} {_heading(excerpt.text)}"
        for excerpt in declaration.excerpts
    )
    return (
        "宣言の本文はプロンプトに埋め込まない。次の位置を、作業ディレクトリから "
        "Read で読むこと。各行は出典の位置（パスと行の範囲）と節の見出し。\n" + entries
    )


def bot_findings(review: Review) -> str | None:
    """bot_comments.json から作る、レビューボットの指摘の一覧。無ければ None。"""
    stored = read_bot_comments(review.id.directory())
    if stored is None or not stored.judgments:
        return None
    entries: list[str] = []
    for item in stored.judgments:
        comment = item.judgment.comment
        first = comment.original_start_line or comment.original_line
        lines = (
            str(first) if first == comment.original_line else f"{first}-{comment.original_line}"
        )
        keys = "、".join(item.region_keys) or "なし"
        entries.append(
            f"- id {comment.id}（{comment.user}）{comment.path}:{lines}"
            f"（付いた時点の行）\n  関わる判断の箇所: {keys}\n"
            f"  本文: {comment.body}"
        )
    return "\n".join(entries)


def changes_prompt_text(review: Review) -> str:
    """発見役と検証役のプロンプトに入れる、変更の節。"""
    if not review.is_diff:
        return f"### 変更\n\n{FEATURE_CHANGES_TEXT}"
    diff = (review.id.directory() / CHANGES_FILE).read_text(encoding="utf-8")
    return diff_changes_text(diff)


def diff_changes_text(diff: str) -> str:
    return f"### 変更\n\n{DIFF_CHANGES_INTRO}\n\n{diff}"


def verifier_changes_text(review: Review) -> str:
    """実装の検証役に渡す、差分の変更の節。丸ごと削除したファイルの本文は外し、パスだけを添える。

    発見役は丸ごと削除したファイルを単位に入れない。検証役だけが本文を受けると、大きな削除で
    入力が発見役の何倍にもなる。
    """
    diff = (review.id.directory() / CHANGES_FILE).read_text(encoding="utf-8")
    kept, removed = without_whole_deletions(diff, load_pairs(review.id))
    text = diff_changes_text(kept)
    if not removed:
        return text
    listing = "\n".join(f"- {path}" for path in removed)
    return f"{text}\n### 丸ごと削除したファイル（本文は省いた）\n\n{listing}\n"


NO_BACKGROUND_TEXT = "背景の材料は無い。"
FEATURE_BACKGROUND_TEXT = "機能レビュー式では背景の材料を集めない。"


def background_prompt_text(review: Review) -> str:
    """発見役のプロンプトに入れる、背景の節。"""
    if not review.is_diff:
        return f"### 背景\n\n{FEATURE_BACKGROUND_TEXT}"
    background = review.background
    if background is None or not (
        background.documents or background.past_changes or has_owners(background)
    ):
        return f"### 背景\n\n{NO_BACKGROUND_TEXT}"
    parts: list[str] = []
    if background.documents:
        entries = "\n".join(f"- {path}" for path in background.documents)
        parts.append(
            "#### PR の本文が参照する文書\n\n"
            "本文はプロンプトに埋め込まない。次のパスを、作業ディレクトリから "
            "Read で読むこと。\n" + entries
        )
    past_pull_requests = [c for c in background.past_changes if isinstance(c, PastPullRequest)]
    if past_pull_requests:
        entries = "\n".join(
            f"- #{pr.number} {pr.title}（{', '.join(pr.files)}）" for pr in past_pull_requests
        )
        parts.append("#### 同じファイルに触れた過去の PR\n\n" + entries)
    past_commits = [c for c in background.past_changes if isinstance(c, PastCommit)]
    if past_commits:
        entries = "\n".join(
            f"- {commit.sha[:COMMIT_ABBREVIATION_LENGTH]} {commit.title}"
            f"（{', '.join(commit.files)}）"
            for commit in past_commits
        )
        parts.append("#### 同じファイルに触れた過去のコミット\n\n" + entries)
    if has_owners(background):
        entries = "\n".join(
            f"- {item.path}: {', '.join(item.owners) or OWNERLESS_TEXT}"
            for item in background.owners
        )
        parts.append("#### 変更したパスの所有者（CODEOWNERS）\n\n" + entries)
    return "### 背景\n\n" + "\n\n".join(parts)


SECTION_REPEATS = "同じ形の箇所"


@dataclass(frozen=True)
class PolicySections:
    """方針のプロンプトに載せる、事前に組んだ節。

    図の節は `PrebuiltSection` で引く。同じ形の箇所と技術選定は置き場を持たず、図の後ろに続く。
    """

    figures: Mapping[changed_files.PrebuiltSection, str]
    repeats: str
    technology: str

    @property
    def figures_missing(self) -> bool:
        """描ける図が一つも無いか。他の図はどれも変更したファイルから作るので、ツリー図の節で決まる。"""
        return changed_files.tree_section_is_empty(
            self.figures[changed_files.PrebuiltSection.TREE]
        )


def policy_sections(review: Review) -> PolicySections:
    """方針のプロンプトの節を組む。重いので一回の実行で一度だけ作る。

    外形だけに絞る。インターフェースは名前と位置、同じ形の箇所は短い塊だけ行の本文あり、技術選定は変化のあった技術だけ。
    """
    trees = policy_tree.trees_of(review)
    prebuilt = changed_files.materials_sections(
        review, review_diagrams.sequence_figure(review), folded=True
    )
    return PolicySections(
        figures=prebuilt,
        repeats=f"### {SECTION_REPEATS}\n\n"
        + policy_repeats.repeats_text(
            review, trees.after, line_limit=policy_repeats.POLICY_LINE_LIMIT
        ),
        technology=technology_materials.short_section_text(review),
    )


def missing_material(review: Review, sections: PolicySections) -> tuple[MissingMaterial, ...]:
    """資料が空のとき、何が無かったかの種類。資料があれば空の組。

    図がすべて空で、宣言も空のときだけ空とする。背景や前の回の指摘は数えない。
    """
    declaration = review.declaration
    if not sections.figures_missing or declaration.intent is not None or declaration.excerpts:
        return ()
    return (MissingMaterial.FIGURES, MissingMaterial.DECLARATION)


def history_text(assembly: AssemblyInput) -> str:
    """前の回からさかのぼった方針の指摘。組み方は実装の群と共有する（history.history_text）。"""
    return history.history_text(assembly, GROUP)


def diagram_materials_text(assembly: AssemblyInput, sections: PolicySections) -> str:
    """図だけの形の資料。

    図の節は `policy_figures.REGISTRY` の置き場と順で並ぶ。その後ろに、同じ形の箇所、技術選定、
    前の回の指摘が続く。
    """
    review = assembly.review
    ordered = [
        *policy_figures.prompt_figure_texts(review, sections.figures),
        sections.repeats,
        sections.technology,
        f"### {SECTION_HISTORY}\n\n{history_text(assembly)}",
    ]
    return "\n\n".join(ordered)


prompt_parts = macro_shared.prompt_parts
stamp_template = macro_shared.stamp_template


def build_diagram_prompt(
    assembly: AssemblyInput,
    sections: PolicySections | None = None,
) -> str:
    """図だけの形のプロンプト。コードの本文を含めず、道具の使い方も書かない。"""
    review = assembly.review
    sections = policy_sections(review) if sections is None else sections
    return adversarial.load_prompt(DIAGRAM_PROMPT).substitute(
        mode=review.mode,
        target_files="\n".join(f"- {path}" for path in review.paths),
        declaration=declaration_prompt_text(review),
        background=background_prompt_text(review),
        materials=diagram_materials_text(assembly, sections),
        scope="\n".join(f"- {path}" for path in sorted(scope_files(review))),
        bot_findings=bot_findings(review) or NO_BOT_FINDINGS_TEXT,
        **prompt_parts(),
    )


def check_input_budget(prompt: str, settings: claude.RunSettings) -> str | None:
    """入力だけで 1 回の起動の上限の費用に達する見込みなら、起動の前に止める。切り詰めはしない。

    方針は 1 回の起動が実行の全体で、往復も 群の表の DIAGRAM_SHAPE.max_turns で絞るため、
    次の一往復の余裕は引かず、上限そのものと比べる（RunSettings.budget.reserve_margin が偽）。
    """
    check = input_budget.check_input_budget(prompt, settings)
    if check.stop is not None:
        raise pipeline.InputBudgetError(GROUP, check.stop.message(), check.stop)
    return check.notice


def design_expected(review: Review, prompt: str, model: str) -> output_estimate.OutputEstimate:
    """設計書の形の、出力と推論の分を含めた見込み。"""
    tokens = claude.estimate_input_tokens(len(prompt.encode("utf-8")))
    return output_estimate.estimate_output(
        Path(review.repository_root),
        pipeline.past_run(review, GROUP, PolicyShape.DESIGN),
        claude.input_cost_usd(model, tokens).usd,
    )


def expected_output(
    review: Review, shape: PolicyShape, prompt: str, model: str
) -> output_estimate.OutputEstimate | None:
    """出力と推論の分を含めた見込み。見込みを持つのは設計書の形だけで、他の形は None。"""
    if shape is not PolicyShape.DESIGN:
        return None
    return design_expected(review, prompt, model)


def expected_budget_warning(
    review: Review, shape: PolicyShape, prompt: str, settings: claude.RunSettings
) -> str | None:
    """見込みに関わる警告の文。目安に達するとき、使用量の表に倍率の根拠にしなかった行があるとき。

    上限は目安であって約束ではないので、起動は止めない。入力だけで上限に達する場合の止めは
    check_input_budget が行う。
    """
    expected = expected_output(review, shape, prompt, settings.budget.model)
    if expected is None:
        return None
    budget = (
        output_estimate.warning_message(expected, settings.budget.max_budget_usd)
        if expected.total_usd >= settings.budget.max_budget_usd
        else None
    )
    return join_notices(budget, output_estimate.skipped_rows_message(expected.skipped))


def stamp_diagram_prompt(text: str) -> PromptStamp:
    """版は、プロンプトの雛形の文面と共有の部品から作る。"""
    return stamp_template(DIAGRAM_PROMPT, text)


def conclude_schema() -> dict[str, Any]:
    """結論の役の出力。findings は発見役の指摘と同じ形で、まとめた組と捨てた指摘を添える。"""
    return json_schema_of(role_findings_model(_conclude_shape()))


def _conclude_shape() -> RoleShape:
    return RoleShape(viewpoints=VIEWPOINTS, require_scope_fields=True, conclusion=True)


def lenient_conclude_model() -> type[BaseModel]:
    return role_findings_model(_conclude_shape(), lenient=True)


class LaunchFailed(pipeline.DiscoveryError):
    """方針の一回の起動が失敗した。実行の記録に残す失敗（額、出所、起動の記録を含む）を運ぶ。"""

    def __init__(self, message: str, failure: DiscoveryLaunchFailed) -> None:
        super().__init__(GROUP, message)
        self.failure = failure


def _failed_run(
    review: Review,
    settings: claude.RunSettings,
    started: float,
    plan: "MacroPlan",
    failed: LaunchFailed,
) -> None:
    """起動が失敗した実行を runs.json の方針の記録に残す。指摘は触らない（前の回の結果が残る）。

    前の回の実行の記録（時刻、捨てた候補、起動）が残る指摘を生んだ回のものなら、それを保って
    失敗だけを足す。前の失敗は消さずに積む。保存の ReplacedRun がこの時刻を前の指摘の回として
    読むため。
    初回の失敗など、指摘を生んだ回が無ければ失敗だけの記録にする。
    """
    failure = failed.failure
    with review_lock(review.id.directory()):
        runs = pipeline.read_runs(review.id.directory())
        previous = runs.get(GROUP)
        if (
            isinstance(previous, pipeline.PolicyGroupRun)
            and RunRecordKind.of_record(previous.to_json()).has_previous_run
        ):
            new_run = replace(
                previous,
                launch_failures=(failure,),
                record_kind=RunRecordKind.SUCCEEDED_THEN_FAILED.value,
            )
        else:
            new_run = replace(
                pipeline.run_state_head(
                    pipeline.PolicyGroupRun,
                    settings=settings,
                    invocations=[],
                    discovered=0,
                    drops=pipeline.DropCounts(),
                    elapsed_s=time.monotonic() - started,
                ),
                launch_failures=(failure,),
                record_kind=RunRecordKind.FAILED_ONLY.value,
                shape=plan.shape,
            )
        runs[GROUP] = new_run if previous is None else previous.next_round(new_run)
        pipeline.write_runs(review.id.directory(), runs)


@dataclass(frozen=True)
class _Outcome:
    """結果の処理の前に出すもの。final は区分を付ける前の最終の指摘。"""

    final: tuple[dict[str, Any], ...]
    invocations: tuple[StoredInvocation, ...]
    considered: Considered
    notice: str | None = None


def _outcome(
    review: Review,
    settings: claude.RunSettings,
    working_directory: Path,
    plan: "MacroPlan",
    drops: pipeline.DropCounts,
    *,
    has_bot_findings: bool,
) -> _Outcome:
    prompt = plan.build_prompt()
    notice = join_notices(
        check_input_budget(prompt, settings),
        expected_budget_warning(review, plan.shape, prompt, settings),
    )
    invocation = settings.invocation(
        prompt=prompt,
        json_schema=conclude_schema(),
        working_directory=working_directory,
        tools=DIAGRAM_TOOLS,
    )
    try:
        result = settings.invoke(invocation)
    except claude.ClaudeInvocationError as error:
        # 起動の上限に達して止まったときもここに来る。部分の結果は使えず、指摘は保存しない。
        # 払った分と失敗の理由は run_macro が実行の記録だけに残す
        role = role_of(plan.shape).value
        stamp = plan.stamp(prompt)
        raise LaunchFailed(
            f"一回の起動が失敗しました。{error}",
            DiscoveryLaunchFailed(
                0,
                role,
                error.kind,
                str(error),
                1,
                Disposition.NOT_RERUN,
                ran_at=datetime.now(UTC).isoformat(timespec="seconds"),
                invocation=FailedInvocation(error.record, stamp.version, stamp.chars),
            ),
        ) from error
    output = result.structured_output or {}
    try:
        validated = lenient_conclude_model().model_validate(output)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise pipeline.DiscoveryError(
            GROUP, f"一回の起動の出力がスキーマに適合しません: {location}: {first['msg']}"
        ) from error
    # 下流（prepare_candidates など）は辞書を前提にする。検証した値を辞書に戻すのはここ一度だけ。
    # モデルは実行時に組み立てるため属性を静的に読めず、MISSING の欄は戻した辞書でキーが無くなる
    checked = validated.model_dump(mode="json")
    prepared = pipeline.prepare_candidates(
        checked["findings"], working_directory, has_bot_findings=has_bot_findings, drops=drops
    )
    # from_concern は実装の群が方針の懸念から作った指摘に付く欄で、方針の群の指摘には意味が無い
    final = [
        pipeline.without_from_concern(finding, "方針の群の指摘が from_concern を書きました")
        if KEY_FROM_CONCERN in finding
        else finding
        for finding in prepared
    ]
    record = pipeline.stored_invocation(
        role_of(plan.shape), None, result.record, plan.stamp(prompt)
    )
    discarded = tuple(DiscardedCandidate.from_json(item) for item in checked["discarded"])
    considered = Considered(len(checked["findings"]) + len(discarded), discarded)
    return _Outcome(tuple(final), (record,), considered, notice)


def discarded_as_nits(discarded: Sequence[DiscardedCandidate]) -> list[dict[str, Any]]:
    """捨てた候補を、主張が捨てた理由で本文と根拠の位置が空の nit の候補に写す。

    区分は shape_finding が通常の nit と同じ規則で付ける（残りで、必読にも関門にも数えない）。
    前の回の指摘が直った理由は nit にしない。写し方は discarded_nit_candidate が持つ。
    """
    candidates = (discarded_nit_candidate(item) for item in discarded)
    return [candidate for candidate in candidates if candidate is not None]


@dataclass(frozen=True)
class MacroPlan:
    """一回の起動の中身のうち、出所によって変わるもの。起動、選別、保存は出所に依らず共通。"""

    build_prompt: Callable[[], str]
    stamp: Callable[[str], PromptStamp]
    # 指摘の根拠の位置が範囲の内か外かを分ける、作業ディレクトリからの相対パスの集合
    scope: set[str]
    missing: tuple[MissingMaterial, ...]
    estimate_note: str
    # runs.json に書く形。見積もりの過去の実額は同じ形の実行だけから引く
    shape: PolicyShape
    # 実行を止めないが利用者へ知らせる文
    warning: str | None = None


ESTIMATE_NOTE = (
    "図だけの形は 1 回の起動で、道具を渡しません。入力は宣言、背景、図と表、前の回の指摘で、"
    "見積もりはそのプロンプトの大きさで数えます。道具の往復が無いので文脈の送り直しは無く、"
    "倍率は掛けません。出力と推論の分は含みません。"
)


DESIGN_ESTIMATE_NOTE = (
    "設計書の形は 1 回の起動で、道具を渡しません。"
    "入力は宣言、設計書の全文と構造、名指しの照合の表、"
    "選んだファイル、いまの形の図、前の回の指摘で、見積もりはそのプロンプトの大きさで数えます。"
    "道具の往復が無いので文脈の送り直しはありません。入力の費用は出力と推論の分を含まず、"
    "出力と推論の分は過去の実額の倍率で別に見込みます（expected.total_usd）。"
)


def diagram_plan(assembly: AssemblyInput) -> MacroPlan:
    review = assembly.review
    sections = policy_sections(review)
    return MacroPlan(
        build_prompt=lambda: build_diagram_prompt(assembly, sections),
        stamp=stamp_diagram_prompt,
        scope=scope_files(review),
        # 資料が空なら、読ませるものが無いので起動の前に止める
        missing=missing_material(review, sections),
        estimate_note=ESTIMATE_NOTE,
        shape=PolicyShape.DIAGRAM,
    )


def design_plan(assembly: AssemblyInput, origin: DesignDocOrigin) -> MacroPlan:
    """設計書の本文は出所のパスから作業ディレクトリで読む。保存した版と違えば、refresh を促す。"""
    review = assembly.review
    root = Path(review.repository_root)
    document = root / origin.document_path
    if not document.is_file():
        raise ReviewError(f"設計書がありません: {origin.document_path}")
    read = read_document(document)
    attached = read_attachments(review)
    drifts = version_drifts(review, read, attached)
    materials = design_materials(
        root,
        origin.document_path,
        read.text,
        origin.selection,
        attached,
    )
    return MacroPlan(
        build_prompt=lambda: build_design_prompt(
            materials, history_text(assembly), declaration_prompt_text(review)
        ),
        stamp=stamp_design_prompt,
        # 設計書そのものも根拠になりうるので、選んだファイルと並べて範囲に入れる
        scope={*review.paths, origin.document_path, *(item.path for item in origin.attachments)},
        missing=materials.missing if materials.is_empty else (),
        estimate_note=DESIGN_ESTIMATE_NOTE,
        shape=PolicyShape.DESIGN,
        warning=drift_notice(drifts),
    )


def policy_shape(review: Review) -> PolicyShape:
    """出所の型から方針の形への振り分け。起動の中身は作らない。"""
    match review.origin:
        case DesignDocOrigin():
            return PolicyShape.DESIGN
        case PullRequestOrigin() | RangeOrigin() | FeatureOrigin():
            return PolicyShape.DIAGRAM
        case _:
            assert_never(review.origin)


def macro_plan(assembly: AssemblyInput) -> MacroPlan:
    """出所の型から方針の起動の中身への振り分けの、唯一の置き場。"""
    origin = assembly.review.origin
    match origin:
        case DesignDocOrigin():
            return design_plan(assembly, origin)
        case PullRequestOrigin() | RangeOrigin() | FeatureOrigin():
            return diagram_plan(assembly)
        case _:
            assert_never(origin)


def join_notices(*notices: str | None) -> str | None:
    present = [notice for notice in notices if notice]
    return "\n".join(present) if present else None


def _no_material_run(
    review: Review,
    settings: claude.RunSettings,
    started: float,
    missing: tuple[MissingMaterial, ...],
    warning: str | None,
    *,
    shape: PolicyShape,
) -> MacroRun:
    """モデルを呼ばずに、材料なしの実行として保存する。起動が無いので費用も起動の記録も無い。"""
    run_state = replace(
        pipeline.run_state_head(
            pipeline.PolicyGroupRun,
            settings=settings,
            invocations=[],
            discovered=0,
            drops=pipeline.DropCounts(),
            elapsed_s=time.monotonic() - started,
        ),
        dropped_without_bot_findings=0,
        discarded=(),
        shape=shape,
        no_material=tuple(kind.value for kind in missing),
    )
    pipeline.save_run(review, GroupResult(()), run_state, GROUP)
    notice = f"材料なし: {'、'.join(missing_texts(missing))}。モデルは呼びませんでした"
    return MacroRun((), run_state, join_notices(warning, notice))


def run_macro(assembly: AssemblyInput, settings: claude.RunSettings) -> MacroRun:
    """assembly の履歴は縁が読んだもので、資料の組み立てで読み直さない。"""
    review = assembly.review
    started = time.monotonic()
    working_directory = Path(review.repository_root)
    plan = macro_plan(assembly)
    # 資料が空なら、読ませるものが無いので起動の前に止める。入力の見積もりも要らない
    if plan.missing:
        return _no_material_run(
            review, settings, started, plan.missing, plan.warning, shape=plan.shape
        )
    drops = pipeline.DropCounts()
    has_bot_findings = bot_findings(review) is not None
    try:
        outcome = _outcome(
            review, settings, working_directory, plan, drops, has_bot_findings=has_bot_findings
        )
    except LaunchFailed as failed:
        _failed_run(review, settings, started, plan, failed)
        raise

    def shape_finding(candidate: dict[str, Any]) -> dict[str, Any]:
        # in_scope と scope_reason は起動そのものが書く。
        # 無い結果は機械の判断に任せる
        return {
            **scope_marks.classify(candidate, plan.scope).shaped(),
            "group": GROUP,
            "theme": None,
            "triage": None,
        }

    kept = pipeline.keep_valid(
        review,
        # 方針の群は検証を行わないので、裁定は持たない
        [
            pipeline.ValidatedCandidate(c, None)
            for c in (*outcome.final, *discarded_as_nits(outcome.considered.discarded))
        ],
        drops,
        shape_finding,
    )
    final = [triaged(GROUP, f) for f in kept]
    run_state = replace(
        pipeline.run_state_head(
            pipeline.PolicyGroupRun,
            settings=settings,
            invocations=list(outcome.invocations),
            discovered=outcome.considered.discovered,
            drops=drops,
            elapsed_s=time.monotonic() - started,
        ),
        dropped_without_bot_findings=drops.without_bot_findings,
        discarded=outcome.considered.discarded,
        record_kind=RunRecordKind.SUCCEEDED.value,
        shape=plan.shape,
    )
    pipeline.save_run(review, GroupResult(tuple(final)), run_state, GROUP)
    return MacroRun(tuple(final), run_state, join_notices(plan.warning, outcome.notice))


def estimate_macro(assembly: AssemblyInput) -> PolicyEstimate:
    review = assembly.review
    plan = macro_plan(assembly)
    prompt = plan.build_prompt()
    prompt_bytes = len(prompt.encode("utf-8"))
    discovery_input_tokens = claude.estimate_input_tokens(prompt_bytes)
    past_run = pipeline.past_run(review, GROUP, plan.shape)
    largest_sections = tuple(input_budget.largest_sections(prompt))
    match plan.shape:
        case PolicyShape.DIAGRAM:
            return DiagramPolicyEstimate(
                discovery_input_tokens=discovery_input_tokens,
                past_run=past_run,
                note=plan.estimate_note,
                largest_sections=largest_sections,
                materials_bytes=prompt_bytes,
                planned_invocations=1,
            )
        case PolicyShape.DESIGN:
            return DesignPolicyEstimate(
                discovery_input_tokens=discovery_input_tokens,
                past_run=past_run,
                note=plan.estimate_note,
                largest_sections=largest_sections,
                materials_bytes=prompt_bytes,
                planned_invocations=1,
                expected=design_expected(review, prompt, GROUP_BY_ID[GROUP].model),
            )
        case PolicyShape.SINGLE | PolicyShape.FULL:
            # 廃止した形は過去の記録を読むためだけに残り、新しい計画は立てない
            raise ValueError(f"廃止した形の見積もりは作れません: {plan.shape}")
