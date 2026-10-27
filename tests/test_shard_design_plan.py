import re
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes import RECORD, FakeInvoke, MicroFake, d_finding, settings_for
from pr_support import commit_all
from review_fakes import stored_finding
from shard_prompt_support import shard_prompt

from jigane.design.review_documents import create_design_doc_review
from jigane.findings.records import (
    TriagedFinding,
    fill_triage,
    load_findings_state,
    triaged_dicts,
)
from jigane.groups.definitions import MACRO, MICRO, SECURITY, GroupId
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import (
    DesignDocOrigin,
    FeatureOrigin,
    PullRequestOrigin,
    RangeOrigin,
    Review,
    create_feature_review,
)
from jigane.skills import micro, security, sharded, shards
from jigane.skills.assembly import AssemblyInput
from jigane.skills.shards import (
    DesignDocumentShard,
    DesignFileShard,
    DiffShard,
    ShardPlanKind,
)

DOCUMENT = "docs/shop-design.md"
DOCUMENT_TEXT = "# 題\n\n## 1. 序論\n\n`Cart` を持つ。\n\n## 2. 実装計画\n\n手順を書く。\n"


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / DOCUMENT).write_text(DOCUMENT_TEXT, encoding="utf-8")
    commit_all(root, "add design")
    return root


def design_review(repo: Path) -> Review:
    return create_design_doc_review(repo, DOCUMENT)


def test_plan_kind_is_decided_from_the_origin_in_one_place() -> None:
    pull_request = PullRequestOrigin(None, None)  # type: ignore[arg-type]
    local_range = RangeOrigin(None, None)  # type: ignore[arg-type]
    design = DesignDocOrigin(DOCUMENT, "v", "c", ())
    assert shards.plan_kind_of(pull_request) is ShardPlanKind.DIFF
    assert shards.plan_kind_of(local_range) is ShardPlanKind.DIFF
    assert shards.plan_kind_of(FeatureOrigin(None)) is ShardPlanKind.FILES
    assert shards.plan_kind_of(design) is ShardPlanKind.DESIGN


def test_group_plan_kind_follows_the_value_for_micro_and_security(repo: Path) -> None:
    review = design_review(repo)
    assert shards.plan_kind_for(MICRO, review) is ShardPlanKind.DESIGN
    assert shards.plan_kind_for(SECURITY, review) is ShardPlanKind.DESIGN
    feature = create_feature_review(repo, ["cart.py"], "f")
    assert shards.plan_kind_for(MICRO, feature) is ShardPlanKind.FILES
    with pytest.raises(ValueError, match="シャードに分けません"):
        shards.plan_kind_for(GroupId.MACRO, feature)


def test_design_plan_has_one_document_shard_and_file_shards(repo: Path) -> None:
    review = design_review(repo)
    plan = shards.build_plan(review, MICRO)
    assert plan.kind is ShardPlanKind.DESIGN
    document_shard, file_shard = plan.shards
    assert isinstance(document_shard, DesignDocumentShard)
    assert isinstance(file_shard, DesignFileShard)
    assert document_shard.paths == [DOCUMENT]
    assert document_shard.covered == {"cart.py"}
    assert file_shard.paths == ["cart.py"]
    assert shards.scope_paths(document_shard) == {DOCUMENT}
    assert shards.scope_paths(file_shard) == {DOCUMENT, "cart.py"}
    for shard in plan.shards:
        insert = sharded._design_insert(shard)
        assert "手順を書く。" in insert
        assert "| 群 | 問う懸念 |" in insert
    assert "設計書の構造" in sharded._design_insert(document_shard)
    assert "設計書の構造" not in sharded._design_insert(file_shard)
    assert "選んだファイルと推しの理由" in sharded._design_insert(file_shard)
    assert "`Cart` を名指しました" in sharded._design_insert(file_shard)


def test_prompts_state_each_shard_kind_from_the_value(repo: Path) -> None:
    review = design_review(repo)
    plan = shards.build_plan(review, MICRO)
    prompts = {
        type(shard): shard_prompt(
            MICRO, AssemblyInput.load(review), shard, i + 1, len(plan.shards)
        )
        for i, shard in enumerate(plan.shards)
    }
    document_prompt = prompts[DesignDocumentShard]
    file_prompt = prompts[DesignFileShard]
    assert sharded.DOCUMENT_SHARD_INTRO.format(group="実装") in document_prompt
    assert sharded.DESIGN_FILE_SHARD_INTRO.format(group="実装") in file_prompt
    assert "ファイルのシャードである" not in document_prompt
    assert "設計書だけで決まる懸念は、設計書のシャードが問うので、ここでは問わない" in file_prompt
    assert "=== cart.py ===" in file_prompt
    assert "=== cart.py ===" not in document_prompt


def test_no_selected_files_leaves_only_the_document_shard(repo: Path) -> None:
    (repo / "docs" / "empty.md").write_text("# 題\n\n手順だけ。\n", encoding="utf-8")
    commit_all(repo, "empty design")
    review = create_design_doc_review(repo, "docs/empty.md")
    assert review.paths == []
    plan = shards.build_plan(review, MICRO)
    assert [type(shard) for shard in plan.shards] == [DesignDocumentShard]


def test_document_is_not_cut_by_the_shard_limit(repo: Path) -> None:
    long_text = DOCUMENT_TEXT + "手順の行。\n" * 20_000
    (repo / DOCUMENT).write_text(long_text, encoding="utf-8")
    commit_all(repo, "long design")
    review = design_review(repo)
    plan = shards.build_plan(review, MICRO)
    assert len(long_text.encode("utf-8")) > shards.SHARD_BYTES
    assert [type(shard) for shard in plan.shards] == [DesignDocumentShard, DesignFileShard]
    assert all(
        sharded._design_insert(shard).count("手順の行。") == 20_000 for shard in plan.shards
    )


def test_security_design_plan_matches_micro_and_names_its_own_row(repo: Path) -> None:
    review = design_review(repo)
    plan = shards.build_plan(review, SECURITY)
    assert plan.kind is ShardPlanKind.DESIGN
    assert [type(s) for s in plan.shards] == [DesignDocumentShard, DesignFileShard]
    assert plan.shards == shards.build_plan(review, MICRO).shards
    for shard in plan.shards:
        assert "手順を書く。" in sharded._design_insert(shard)
    document_prompt, file_prompt = (
        shard_prompt(SECURITY, AssemblyInput.load(review), shard, i + 1, 2)
        for i, shard in enumerate(plan.shards)
    )
    assert "受け持ちの表でセキュリティの行に当たる懸念" in document_prompt
    assert "受け持ちの表でセキュリティの行に当たる懸念" in file_prompt
    assert "実装の行" not in document_prompt + file_prompt
    assert "設計書の資料" in document_prompt
    assert "=== cart.py ===" in file_prompt
    assert "$design" not in document_prompt
    micro_prompt = shard_prompt(MICRO, AssemblyInput.load(review), plan.shards[0], 1, 2)
    assert "受け持ちの表で実装の行に当たる懸念" in micro_prompt


def test_security_receives_no_macro_concerns(repo: Path) -> None:
    review = design_review(repo)
    for shard in shards.build_plan(review, SECURITY).shards:
        assert sharded.shard_concern_findings(SECURITY, review, shard) == []


def test_security_non_design_origins_leave_the_design_insert_empty(repo: Path) -> None:
    feature = create_feature_review(repo, ["cart.py"], "f")
    plan = shards.build_plan(feature, SECURITY)
    assert plan.kind is ShardPlanKind.FILES
    assert all(sharded._design_insert(shard) == "" for shard in plan.shards)
    prompt = shard_prompt(SECURITY, AssemblyInput.load(feature), plan.shards[0], 1, 1)
    assert "設計書の資料" not in prompt
    assert "$design" not in prompt
    assert sharded.FEATURE_INTRO in prompt


def test_diff_plan_builds_diff_shards(repo: Path) -> None:
    feature = create_feature_review(repo, ["cart.py"], "f")
    units = shards.build_units(feature)
    assert [type(shard) for shard in shards.assign_shards(units, DiffShard)] == [DiffShard]


def test_non_design_origins_leave_the_design_insert_empty(repo: Path) -> None:
    feature = create_feature_review(repo, ["cart.py"], "f")
    plan = shards.build_plan(feature, MICRO)
    assert plan.kind is ShardPlanKind.FILES
    prompt = shard_prompt(MICRO, AssemblyInput.load(feature), plan.shards[0], 1, 1)
    assert "設計書の資料" not in prompt
    assert sharded.FEATURE_INTRO in prompt
    assert "$design" not in prompt


def finding_with(*paths: str) -> TriagedFinding:
    evidence = [{"path": path, "start_line": 1, "end_line": 1} for path in paths]
    return fill_triage(MACRO, stored_finding({"id": "F1", "evidence": evidence}))


def test_handed_over_concern_goes_to_the_shard_with_its_evidence_file(repo: Path) -> None:
    review = design_review(repo)
    document_shard, file_shard = shards.build_plan(review, MICRO).shards
    assert isinstance(document_shard, DesignDocumentShard)
    assert isinstance(file_shard, DesignFileShard)
    on_file = finding_with("cart.py")
    assert sharded._shard_receives(on_file, file_shard)
    assert not sharded._shard_receives(on_file, document_shard)
    for finding in (finding_with(DOCUMENT), finding_with()):
        assert sharded._shard_receives(finding, document_shard)
        assert not sharded._shard_receives(finding, file_shard)


def test_micro_runs_on_a_design_review_with_fake_invoke_and_saves(repo: Path) -> None:
    review = design_review(repo)
    fake = MicroFake({1: [d_finding("D1", DOCUMENT)], 2: [d_finding("D1", "cart.py")]})
    run = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    assert run.failed_shards == 0
    assert run.run_state.shards == 2
    assert run.run_state.target_files == 1
    assert triaged_dicts(run.findings)
    saved = triaged_dicts(load_findings_state(review.id.directory()).groups[GroupId("micro")])
    assert [f["id"] for f in saved] == [f["id"] for f in triaged_dicts(run.findings)]


class SecurityShardFake(FakeInvoke):
    def __init__(self, by_shard: dict[int, list[dict[str, Any]]]) -> None:
        super().__init__(verdict="kept")
        self.by_shard = by_shard
        self.shard_prompts: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        if "担当のシャード" not in invocation.prompt:
            return super().__call__(invocation)
        found = re.search(r"担当のシャード: (\d+) / (\d+)", invocation.prompt)
        assert found
        with self.lock:
            self.calls.append(invocation)
            self.shard_prompts.append(invocation.prompt)
        return claude.InvocationResult({"findings": self.by_shard[int(found.group(1))]}, RECORD)


def test_security_runs_on_a_design_review_with_fake_invoke_and_saves(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "jigane-home"
    home.mkdir()
    monkeypatch.setenv("JIGANE_HOME", str(home))
    review = design_review(repo)
    by_shard = {
        1: [{**d_finding("S-AUTH", DOCUMENT), "references": ["ASVS-V6"]}],
        2: [{**d_finding("S-AUTH", "cart.py"), "references": ["ASVS-V6"]}],
    }
    fake = SecurityShardFake(by_shard)
    run = security.run_security(AssemblyInput.load(review), settings_for(fake))
    assert run.failed_shards == 0
    assert run.run_state.shards == 2
    assert triaged_dicts(run.findings)
    assert all("設計書の資料" in prompt for prompt in fake.shard_prompts)
    saved = triaged_dicts(load_findings_state(review.id.directory()).groups[GroupId("security")])
    assert [f["id"] for f in saved] == [f["id"] for f in triaged_dicts(run.findings)]
