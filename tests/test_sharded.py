import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fakes import RECORD, FakeInvoke, MicroFake, d_finding, settings_for, with_budget
from pydantic import BaseModel, ConfigDict
from shard_prompt_support import shard_prompt

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings.discard import (
    DiscardKind,
    ExtractedConcern,
    NoConcern,
    OrdinaryNitReason,
    PassedConcern,
    ShardConcerns,
)
from jigane.findings.records import (
    FilledFindings,
    RemainingDecision,
    StoredFinding,
    Triage,
    TriagedFinding,
    concerns_section,
    load_findings_state,
    triaged_dicts,
)
from jigane.groups.definitions import MICRO, GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.launch_failure import Disposition, Stage
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import (
    adversarial,
    history,
    input_budget,
    micro,
    pipeline,
    security,
    sharded,
    shards,
)
from jigane.skills.assembly import AssemblyInput

RUN_STATE_KEYS = {
    "ran_at",
    "elapsed_s",
    "model",
    "effort",
    "invocations",
    "discovered",
    "dropped_without_evidence",
    "dropped_outside_shard",
    "dropped_invalid_schema",
    "failed_splits",
    "launch_failures",
    "unverified_candidates",
    "shards",
    "succeeded_shards",
    "target_files",
    "reviewed_files",
    "budget_stops",
    "mcp_servers",
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    for name in ("a", "b"):
        (root / "src" / f"{name}.py").write_text(
            f"# {name}\n" + "x = 1\n" * 3500, encoding="utf-8"
        )
    monkeypatch.chdir(root)
    return root


class DiscoveryFake(FakeInvoke):
    def __init__(
        self,
        failing_shards: tuple[str, ...],
        kind: claude.FailureKind = claude.FailureKind.OTHER,
        invalid_shards: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.failing_shards = failing_shards
        self.kind = kind
        self.invalid_shards = invalid_shards

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        if invocation.tools != micro.DISCOVERY_TOOLS:
            return super().__call__(invocation)
        match = re.search(r"担当のシャード: (\d+) / (\d+)", invocation.prompt)
        assert match
        if match.group(1) in self.failing_shards:
            raise claude.ClaudeInvocationError("boom", RECORD, self.kind)
        if match.group(1) in self.invalid_shards:
            return claude.InvocationResult({"findings": "not-a-list"}, RECORD)
        path = re.search(r"^- (src/\S+)$", invocation.prompt, re.MULTILINE)
        assert path
        return claude.InvocationResult({"findings": [d_finding("D1", path.group(1))]}, RECORD)


def spec_with(calls: list[list[StoredFinding]], group: GroupId = MICRO) -> sharded.ShardedGroup:
    def select(
        kept: list[StoredFinding], _last_theme_number: int, _context: sharded.SelectContext
    ) -> sharded.Selection:
        calls.append(kept)
        decided = [TriagedFinding(f.body, RemainingDecision()) for f in kept]
        return sharded.Selection(tuple(decided), run_state_extra={"extra_key": 1})

    return sharded.ShardedGroup(
        group=group,
        schema=micro.discovery_schema(),
        lenient_model=micro.lenient_discovery_model(),
        tools=micro.DISCOVERY_TOOLS,
        select=select,
    )


def test_selection_stage_runs_and_run_state_has_the_shared_keys(project: Path) -> None:
    calls: list[list[StoredFinding]] = []
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with(calls),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(("1",)), max_budget_usd=0.5),
    )
    assert triaged_dicts(run.findings)
    assert len(calls) == 1
    assert [f.body.id for f in calls[0]] == [f["id"] for f in triaged_dicts(run.findings)]
    assert [f["triage"] for f in triaged_dicts(run.findings)] == ["remaining"] * len(
        triaged_dicts(run.findings)
    )
    assert set(run.run_state.to_json()) == RUN_STATE_KEYS | {"extra_key"}
    assert run.failed_shards == 1
    assert run.run_state.succeeded_shards == (run.run_state.shards or 0) - 1


def test_findings_are_saved_under_the_group_name(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    assert triaged_dicts(run.findings)
    saved = triaged_dicts(load_findings_state(review.id.directory()).groups[GroupId("micro")])
    assert [f["id"] for f in saved] == [f["id"] for f in triaged_dicts(run.findings)]
    assert {f["group"] for f in saved} == {"micro"}


def test_all_shards_failing_raises_a_discovery_error_with_the_group(project: Path) -> None:
    calls: list[list[StoredFinding]] = []
    review = create_feature_review(project, ["src"], "p")
    with pytest.raises(pipeline.DiscoveryError) as raised:
        sharded.run_sharded(
            spec_with(calls),
            AssemblyInput.load(review),
            settings_for(DiscoveryFake(("1", "2", "3", "4")), max_budget_usd=0.5),
        )
    assert raised.value.group == "micro"
    assert "全" in str(raised.value)
    assert calls == []


def stored_launch_failures(review: Review) -> list[dict[str, Any]]:
    stored = json.loads((review.id.directory() / "runs.json").read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = stored["micro"]["launch_failures"]
    return failures


@pytest.mark.parametrize(
    "kind", [claude.FailureKind.RATE_LIMIT, claude.FailureKind.BUDGET_EXCEEDED]
)
def test_discovery_launch_failure_is_recorded_without_rerun(
    project: Path, kind: claude.FailureKind
) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = DiscoveryFake(("2",), kind=kind)
    run = sharded.run_sharded(
        spec_with([]), AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5)
    )
    expected = [
        {
            "stage": Stage.DISCOVERY.value,
            "index": 1,
            "role": "discovery",
            "kind": kind.value,
            "reason": "boom",
            "attempt": 1,
            "disposition": Disposition.NOT_RERUN.value,
            "scope": "launch",
            "round": "current",
            # 発見役の失敗は金額も時刻も持たない
            "cost_usd": 0.0,
            "cost_source": "unreported",
            "ran_at": None,
        }
    ]
    assert run.run_state.to_json()["launch_failures"] == expected
    assert stored_launch_failures(review) == expected
    assert run.run_state.to_json()["budget_stops"] == []


def test_discovery_schema_violation_is_recorded_as_bad_output(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = DiscoveryFake((), invalid_shards=("1",))
    run = sharded.run_sharded(
        spec_with([]), AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5)
    )
    [failure] = stored_launch_failures(review)
    assert failure["stage"] == "discovery"
    assert failure["index"] == 0
    assert failure["role"] == "discovery"
    assert failure["kind"] == claude.FailureKind.BAD_OUTPUT.value
    assert failure["reason"].startswith("出力が schema に合いません: findings")
    assert failure["attempt"] == 1
    assert failure["disposition"] == "not_rerun"
    assert run.failed_shards == 1


def test_input_budget_stop_is_in_budget_stops_only(project: Path) -> None:
    (project / "src" / "a.py").write_text("x = 1\n" * 20000, encoding="utf-8")
    review = create_feature_review(project, ["src"], "p")
    shard_list = shards.assign_shards(shards.build_units(review), shards.FullTextShard)
    total = len(shard_list)
    costs = [
        claude.estimate_input_tokens(
            len(
                shard_prompt(MICRO, AssemblyInput.load(review), shard, i + 1, total).encode(
                    "utf-8"
                )
            )
        )
        * (10 / 1_000_000)
        for i, shard in enumerate(shard_list)
    ]
    assert max(costs) > 2 * min(costs)
    # 最大のシャードだけが、次の一往復の余裕を引くと上限の半分を割る
    settings = settings_for(DiscoveryFake(()), max_budget_usd=max(costs) + min(costs))
    run = sharded.run_sharded(spec_with([]), AssemblyInput.load(review), settings)
    assert run.run_state.to_json()["launch_failures"] == []
    assert stored_launch_failures(review) == []
    [stop] = run.run_state.to_json()["budget_stops"]
    assert (stop["role"], stop["index"]) == ("discovery", costs.index(max(costs)))
    assert run.failed_shards == 1


class OmitSecondRebuttal(DiscoveryFake):
    """反証役の出力から、いつも先頭の候補（index 0）を欠く。"""

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        result = super().__call__(invocation)
        if "反証役です" not in invocation.prompt:
            return result
        items = (result.structured_output or {})["items"]
        return claude.InvocationResult({"items": [i for i in items if i["index"] != 0]}, RECORD)


def test_unverified_candidate_is_shown_with_the_recorded_kind_and_reason(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(OmitSecondRebuttal(()), max_budget_usd=0.5),
    )
    assert run.failed_splits == 1
    assert run.run_state.unverified_candidates == 1
    [failure] = run.run_state.to_json()["launch_failures"]
    assert failure["kind"] == "bad_output"
    assert failure["disposition"] == "rerun_failed"
    capsys.readouterr()
    assert main(["show", "shop:p", "micro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "bad_output" in out
    assert "rebuttal の出力に index [0] がありません" in out
    assert "標準エラーを読む" not in out
    assert main(["show", "shop:p", "micro", "--json"]) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    (line,) = (view["text"] for view in body["run"]["failures"])
    assert "bad_output" in line
    assert "rebuttal の出力に index [0] がありません" in line
    assert "標準エラーを読む" not in json.dumps(body, ensure_ascii=False)


def test_shard_failure_without_a_record_still_carries_the_limit_as_its_cost(
    project: Path,
) -> None:
    fake = DiscoveryFake(())
    failed = False

    def timing_out(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("claude", 1)

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        nonlocal failed
        if invocation.tools == micro.DISCOVERY_TOOLS and not failed:
            failed = True
            # 記録の無い失敗は claude.run が投げる地点で上限の額に代える
            return claude.run(invocation, runner=timing_out)
        return fake(invocation)

    review = create_feature_review(project, ["src"], "p")
    sharded.run_sharded(
        spec_with([]), AssemblyInput.load(review), settings_for(invoke, max_budget_usd=0.5)
    )
    stored = json.loads((review.id.directory() / "runs.json").read_text(encoding="utf-8"))
    rows = stored["micro"]["invocations"]
    (substituted,) = [row for row in rows if row["cost_source"] == "limit_substitute"]
    assert substituted["cost_usd"] == 0.5


def test_prompt_name_is_drawn_from_the_group_in_one_place() -> None:
    assert sharded.prompt_name_of(GroupId.MICRO) == "micro"
    assert sharded.prompt_name_of(GroupId.SECURITY) == "security"
    with pytest.raises(ValueError, match="方針"):
        sharded.prompt_name_of(GroupId.MACRO)


class NitAndWarningDiscovery(DiscoveryFake):
    """各シャードが、nit と warning を一件ずつ見つける。"""

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        result = super().__call__(invocation)
        if invocation.tools != micro.DISCOVERY_TOOLS:
            return result
        [warning] = (result.structured_output or {})["findings"]
        nit = {**warning, "severity": "nit", "viewpoint": "D2", "claim": "claim nit"}
        return claude.InvocationResult({"findings": [warning, nit]}, RECORD)


def test_nit_candidates_skip_verification_and_stay_nit_without_adjudication(
    project: Path,
) -> None:
    fake = NitAndWarningDiscovery(())
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with([]), AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5)
    )
    nits = [f for f in triaged_dicts(run.findings) if f["severity"] == "nit"]
    verified = [f for f in triaged_dicts(run.findings) if f["severity"] != "nit"]
    assert nits
    assert all(f["adjudication"] is None for f in nits)
    assert verified
    assert all(f["adjudication"] is not None for f in verified)
    # 検証役の起動に渡った候補に nit は入らない
    assert fake.calls
    assert all("claim nit" not in call.prompt for call in fake.calls)


def test_a_verdict_out_of_enum_leaves_candidates_unverified_not_invalid_schema(
    project: Path,
) -> None:
    """語彙の外の verdict は、検証の分割を失敗にして候補を未検証で残す。

    検証の前は、裁定を読めない候補として落とし、スキーマ不正に数えていた（置き換え前の試験）。
    """
    review = create_feature_review(project, ["src"], "p")
    fake = DiscoveryFake(())
    fake.verdict = "maybe"
    run = sharded.run_sharded(
        spec_with([]), AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5)
    )
    assert run.run_state.discovered
    assert run.run_state.dropped_invalid_schema == 0
    assert run.run_state.unverified_candidates == run.run_state.discovered
    assert run.failed_shards == 0


class ConcernClaimingDiscovery(DiscoveryFake):
    """各シャードの指摘が、from_concern に決められた番号を書く。"""

    def __init__(self, claims: dict[str, str]) -> None:
        super().__init__(())
        self.claims = claims

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        result = super().__call__(invocation)
        if invocation.tools != micro.DISCOVERY_TOOLS:
            return result
        match = re.search(r"担当のシャード: (\d+) / (\d+)", invocation.prompt)
        assert match
        output = result.structured_output or {}
        claimed = [{**f, "from_concern": self.claims[match.group(1)]} for f in output["findings"]]
        return claude.InvocationResult({"findings": claimed}, RECORD)


def _write_macro_concern(review: Review) -> None:
    concern = "X が懸念である。"
    reason = OrdinaryNitReason(
        DiscardKind.UNVERIFIABLE_FROM_DIAGRAM, concern, ExtractedConcern(concern)
    )
    nit = {"id": "F1", "claim": reason.claim, "nit_reason": reason.to_json()}
    raw = {"last_number": 1, "groups": {"macro": [nit]}}
    (review.id.directory() / "findings.json").write_text(json.dumps(raw), encoding="utf-8")


def _discovery_rows(review: Review) -> list[dict[str, Any]]:
    stored = json.loads((review.id.directory() / "runs.json").read_text(encoding="utf-8"))
    return [row for row in stored["micro"]["invocations"] if row["role"] == "discovery"]


def test_from_concern_round_trips_and_unsent_numbers_are_dropped(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _write_macro_concern(review)
    fake = ConcernClaimingDiscovery({"1": "F1", "2": "F9"})
    run = sharded.run_sharded(
        spec_with([]), AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5)
    )
    assert sorted(f.finding.from_concern or "" for f in run.findings) == ["", "F1"]
    saved = load_findings_state(review.id.directory()).groups[MICRO]
    assert sorted(f.finding.from_concern or "" for f in saved) == ["", "F1"]
    assert "F9" in caplog.text


def test_invocations_record_the_concern_numbers_handed_to_each_shard(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _write_macro_concern(review)
    sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    rows = _discovery_rows(review)
    assert rows
    expected = {"passed": [{"id": "F1", "text": "X が懸念である。"}], "unspecified": 0}
    assert all(row["concerns"] == expected for row in rows)


def test_invocations_record_empty_concerns_when_none_were_handed_over(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    rows = _discovery_rows(review)
    assert rows
    assert all(row["concerns"] == {"passed": [], "unspecified": 0} for row in rows)


def _show_micro_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    capsys.readouterr()
    assert main(["show", "shop:p", "micro", "--json"]) == EXIT_OK
    body: dict[str, Any] = json.loads(capsys.readouterr().out)
    return body


def test_show_micro_maps_each_concern_to_its_shards_and_findings(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _write_macro_concern(review)
    fake = ConcernClaimingDiscovery({"1": "F1", "2": "F1"})
    sharded.run_sharded(
        spec_with([]), AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5)
    )
    received = _show_micro_json(capsys)["received_concerns"]
    [concern] = received["concerns"]
    assert received["state"] == "received"
    assert (concern["id"], concern["text"]) == ("F1", "X が懸念である。")
    assert concern["shards"] == sorted(concern["shards"]) != []
    assert len(concern["finding_ids"]) >= 1
    assert received["unspecified"] == 0
    capsys.readouterr()
    assert main(["show", "shop:p", "micro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[方針から受け取った懸念]" in out
    assert "F1 X が懸念である。" in out


def test_show_micro_says_a_concern_was_not_made_into_a_finding(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _write_macro_concern(review)
    sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    [concern] = _show_micro_json(capsys)["received_concerns"]["concerns"]
    assert concern["finding_ids"] == []
    capsys.readouterr()
    assert main(["show", "shop:p", "micro"]) == EXIT_OK
    assert "指摘にしませんでした" in capsys.readouterr().out


def test_show_micro_distinguishes_a_run_without_the_concern_record(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    path = review.id.directory() / "runs.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    for row in stored["micro"]["invocations"]:
        row.pop("concerns", None)
    path.write_text(json.dumps(stored), encoding="utf-8")
    received = _show_micro_json(capsys)["received_concerns"]
    assert received == {"state": "not_recorded"}
    capsys.readouterr()
    assert main(["show", "shop:p", "micro"]) == EXIT_OK
    assert "この実行には受けた懸念の記録がありません" in capsys.readouterr().out


def test_show_micro_omits_the_section_when_no_concern_was_handed_over(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    assert _show_micro_json(capsys)["received_concerns"] == {"state": "none"}
    capsys.readouterr()
    assert main(["show", "shop:p", "micro"]) == EXIT_OK
    assert "方針から受け取った懸念" not in capsys.readouterr().out


def test_show_micro_sums_unspecified_concerns_across_shards(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    path = review.id.directory() / "runs.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in stored["micro"]["invocations"] if row["role"] == "discovery"]
    for row in rows:
        row["concerns"] = {"passed": [], "unspecified": 2}
    path.write_text(json.dumps(stored), encoding="utf-8")
    received = _show_micro_json(capsys)["received_concerns"]
    assert received["unspecified"] == 2 * len(rows)
    capsys.readouterr()
    assert main(["show", "shop:p", "micro"]) == EXIT_OK
    assert f"文を取り出せなかった懸念: {2 * len(rows)} 件" in capsys.readouterr().out


def test_findings_are_read_once_per_shard_for_prompt_and_record(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _write_macro_concern(review)
    reads: list[int] = []
    original = load_findings_state

    def counting(directory: Path) -> FilledFindings:
        reads.append(1)
        return original(directory)

    monkeypatch.setattr(sharded, "load_findings_state", counting)
    sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    assert len(reads) == len(_discovery_rows(review))


def test_build_shard_prompt_takes_only_the_given_concerns(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _write_macro_concern(review)

    def unexpected(_directory: object) -> object:
        raise AssertionError("build_shard_prompt が findings.json を読み直した")

    monkeypatch.setattr(sharded, "load_findings_state", unexpected)
    [shard, *_] = shards.build_plan(review, MICRO).shards
    assembly = AssemblyInput.load(review)
    prompt = sharded.build_shard_prompt(MICRO, assembly, shard, 1, 1, concerns=ShardConcerns())
    assert "X が懸念である" not in prompt
    given = ShardConcerns((PassedConcern("F4", "Y が懸念である。"),), 0)
    prompt = sharded.build_shard_prompt(MICRO, assembly, shard, 1, 1, concerns=given)
    assert "- 方針 F4: Y が懸念である。" in prompt


def test_prompt_and_record_are_built_from_the_same_shard_concerns(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _write_macro_concern(review)
    path = review.id.directory() / "findings.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["last_number"] = 2
    vague = OrdinaryNitReason(DiscardKind.UNVERIFIABLE_FROM_DIAGRAM, "理由", NoConcern())
    stored["groups"]["macro"].append(
        {"id": "F2", "claim": vague.claim, "nit_reason": vague.to_json()}
    )
    path.write_text(json.dumps(stored), encoding="utf-8")
    prompts: dict[int, str] = {}

    class Recording(DiscoveryFake):
        def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
            if invocation.tools == micro.DISCOVERY_TOOLS:
                match = re.search(r"担当のシャード: (\d+) / ", invocation.prompt)
                assert match
                prompts[int(match.group(1)) - 1] = invocation.prompt
            return super().__call__(invocation)

    sharded.run_sharded(
        spec_with([]), AssemblyInput.load(review), settings_for(Recording(()), max_budget_usd=0.5)
    )
    rows = _discovery_rows(review)
    assert rows
    assert {row["split_index"] for row in rows} == set(prompts)
    for row in rows:
        recorded = ShardConcerns(
            tuple(PassedConcern(c["id"], c["text"]) for c in row["concerns"]["passed"]),
            row["concerns"]["unspecified"],
        )
        assert recorded == ShardConcerns((PassedConcern("F1", "X が懸念である。"),), 1)
        assert concerns_section(recorded) in prompts[row["split_index"]]


STORED_ONLY_KEYS = {"nit_reason", "triage_reason", "out_of_scope", "adjudication", "theme"}


@pytest.mark.parametrize("schema", [micro.discovery_schema(), security.discovery_schema()])
def test_finder_schema_shows_only_model_written_fields(schema: dict[str, Any]) -> None:
    shown = set(schema["properties"]["findings"]["items"]["properties"])
    assert not STORED_ONLY_KEYS & shown
    assert "deepens" not in shown
    assert set(adversarial.MODEL_WRITTEN) <= shown


def test_stored_only_fields_from_a_finder_are_dropped_and_the_store_reloads(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 検証を素通しにして、スキーマが禁じる鍵が取り込みまで届く場合を作る
    class Permissive(BaseModel):
        model_config = ConfigDict(extra="allow")

    monkeypatch.setattr(micro, "lenient_discovery_model", lambda: Permissive)
    review = create_feature_review(project, ["src"], "p")
    extra = {
        "nit_reason": {"kind": "bogus", "text": "t", "concern": None},
        "triage_reason": "r",
        "out_of_scope": {"paths": ["src/a.py"], "reason": "r"},
        "theme": "T1",
    }
    fake = MicroFake({1: [{**d_finding("D1"), **extra}], 2: []})
    micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    saved = load_findings_state(review.id.directory()).groups[MICRO]
    assert [f.finding.claim for f in saved] == ["claim D1"]
    assert saved[0].finding.nit_reason is None
    stored = (review.id.directory() / "findings.json").read_text(encoding="utf-8")
    assert "bogus" not in stored
    assert "triage_reason" not in stored
    assert "out_of_scope" not in stored


class FailingVerification(DiscoveryFake):
    """発見役は通し、検証役の起動はすべて再試行されない失敗で落とす。"""

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        if invocation.tools == micro.DISCOVERY_TOOLS:
            return super().__call__(invocation)
        raise claude.ClaudeInvocationError("boom", RECORD, claude.FailureKind.OTHER)


class NitDiscovery(FailingVerification):
    """発見役が重さ nit の候補だけを返す。検証役を起動すれば落ちる。"""

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        result = super().__call__(invocation)
        if invocation.tools != micro.DISCOVERY_TOOLS:
            return result
        findings = (result.structured_output or {})["findings"]
        return claude.InvocationResult(
            {"findings": [{**f, "severity": "nit"} for f in findings]}, result.record
        )


def stop_verification_by_input_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """検証役の起動だけを、入力の見積もりで止める。発見役は通す。"""
    real_check = input_budget.check_input_budget

    def check(
        text: str,
        settings: claude.RunSettings,
        *,
        role: str | None = None,
        index: int | None = None,
    ) -> input_budget.BudgetCheck:
        tight = settings if role == "discovery" else with_budget(settings, max_budget_usd=0.001)
        return real_check(text, tight, role=role, index=index)

    monkeypatch.setattr(input_budget, "check_input_budget", check)


def saved_micro(review: Review) -> list[TriagedFinding]:
    return list(load_findings_state(review.id.directory()).groups[GroupId("micro")])


def assert_unverified_saved(review: Review, run: sharded.ShardedRun) -> None:
    unverified = [f for f in saved_micro(review) if f.decision.triage is Triage.UNVERIFIED]
    assert unverified
    assert len(unverified) == run.run_state.unverified_candidates
    for finding in unverified:
        assert re.fullmatch(r"F\d+", finding.finding.id)
        # 検証していないので、裁定は持たない
        assert finding.finding.adjudication is None
    stored = {f["id"]: f["triage"] for f in triaged_dicts(saved_micro(review))}
    assert {stored[f.finding.id] for f in unverified} == {"unverified"}
    ids = [f.finding.id for f in saved_micro(review)]
    assert len(ids) == len(set(ids))
    # 返した指摘にも同じ印が付く
    assert {f.finding.id for f in unverified} <= {f.finding.id for f in run.findings}
    # 必読に数えず、関門を止めない
    for finding in unverified:
        assert finding.decision.triage.traits.read_first is False
        assert finding.decision.triage.traits.blocks_gate is False


def test_candidate_left_missing_after_rerun_is_saved_as_unverified(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(OmitSecondRebuttal(()), max_budget_usd=0.5),
    )
    assert run.run_state.unverified_candidates == 1
    assert_unverified_saved(review, run)


def test_show_json_marks_unverified_findings_and_records_the_run_counts(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(OmitSecondRebuttal(()), max_budget_usd=0.5),
    )
    assert run.run_state.unverified_candidates == 1
    capsys.readouterr()
    assert main(["show", "shop:p", "micro", "--json"]) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    unverified = [f for f in body["findings"] if f["triage"] == "unverified"]
    assert len(unverified) == 1
    assert unverified[0]["display_category"] == "unverified"
    assert "unverified" in {t["name"] for t in body["triages"]}
    assert "unverified" in {c["name"] for c in body["display_categories"]}
    assert body["run"]["failed_splits"] == 1
    assert body["run"]["unverified_candidates"] == 1


def test_history_text_marks_unverified_findings_and_adds_the_rule(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(OmitSecondRebuttal(()), max_budget_usd=0.5),
    )
    text = history.shard_history_text(AssemblyInput.load(review), MICRO, {"src/a.py", "src/b.py"})
    unverified_lines = [line for line in text.splitlines() if "区分=未検証" in line]
    assert len(unverified_lines) == 1
    assert unverified_lines[0].startswith("- 前の回 F")
    assert "反証=なし" in unverified_lines[0]
    assert "区分が未検証の論点は、直ったとみなしません。" in text
    assert text.endswith(history.PREVIOUS_FINDINGS_RULES)


def test_candidates_of_a_split_whose_launch_failed_are_saved_as_unverified(
    project: Path,
) -> None:
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(FailingVerification(()), max_budget_usd=0.5),
    )
    assert run.failed_splits >= 1
    assert_unverified_saved(review, run)


def test_candidates_of_a_split_stopped_by_input_budget_are_saved_as_unverified(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_verification_by_input_budget(monkeypatch)
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(DiscoveryFake(()), max_budget_usd=0.5),
    )
    assert run.run_state.to_json()["budget_stops"]
    assert run.failed_splits >= 1
    assert_unverified_saved(review, run)


def test_nit_candidates_are_not_verified_so_they_are_never_unverified(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run = sharded.run_sharded(
        spec_with([]),
        AssemblyInput.load(review),
        settings_for(NitDiscovery(()), max_budget_usd=0.5),
    )
    assert run.failed_splits == 0
    assert run.run_state.unverified_candidates == 0
    assert saved_micro(review)
    assert Triage.UNVERIFIED not in {f.decision.triage for f in saved_micro(review)}
