import json
import shutil
from pathlib import Path

import pytest
from fakes import MicroFake, d_finding, settings_for
from pr_support import commit_all

from jigane.commands.subcommands.refresh import refresh_review
from jigane.design.review_documents import (
    attachment_candidates,
    create_design_doc_review,
    document_version,
    read_attachments,
    read_document,
    version_drifts,
)
from jigane.findings.records import triaged_dicts
from jigane.groups.definitions import MICRO
from jigane.policy_materials.design_doc import design_materials
from jigane.reviews.attachments import AttachmentContent, DriftedRole, VersionDrift, drift_notice
from jigane.reviews.lifecycle import AttachedDocument, DesignDocOrigin, ReviewError
from jigane.skills import micro, sharded, shards
from jigane.skills.assembly import AssemblyInput

DOCUMENT = "docs/shop-design.md"
MEMO = "docs/reports/history-split.md"


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "docs" / "reports").mkdir(parents=True, exist_ok=True)
    (root / DOCUMENT).write_text("# 題\n\n## 1. 序論\n\n`Cart` を持つ。\n", encoding="utf-8")
    (root / MEMO).write_text("# 検証メモ\n\n分割で履歴が残る。\n", encoding="utf-8")
    (root / "docs" / "reports" / "other.md").write_text("# 別\n", encoding="utf-8")
    commit_all(root, "add design")
    return root


def test_with_is_stored_in_origin(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO, MEMO])
    origin = review.origin
    assert isinstance(origin, DesignDocOrigin)
    expected = document_version((repo / MEMO).read_bytes())
    assert origin.attachments == (AttachedDocument(MEMO, expected),)
    assert review.stored_fields()["design_doc"]["attachments"] == [
        {"path": MEMO, "version": expected}
    ]


def test_non_utf8_attachment_still_gets_a_version_at_creation(repo: Path) -> None:
    raw = b"\xff\xfe not utf-8\r\n"
    (repo / MEMO).write_bytes(raw)
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    origin = review.origin
    assert isinstance(origin, DesignDocOrigin)
    assert origin.attachments == (AttachedDocument(MEMO, document_version(raw)),)


def test_missing_attachment_is_rejected(repo: Path) -> None:
    with pytest.raises(ReviewError, match="付属の文書がありません"):
        create_design_doc_review(repo, DOCUMENT, attach=["docs/reports/ghost.md"])


def test_without_with_nothing_is_attached_and_stored_form_is_unchanged(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT)
    assert isinstance(review.origin, DesignDocOrigin)
    assert review.origin.attachments == ()
    assert "attachments" not in review.stored_fields()["design_doc"]


def test_old_stored_origin_without_attachments_is_readable() -> None:
    stored = {
        "document_path": DOCUMENT,
        "document_version": "d" * 64,
        "code_version": "c" * 40,
        "selection": ["cart.py"],
    }
    origin = DesignDocOrigin.from_dict(json.loads(json.dumps(stored)))
    assert origin.attachments == ()
    assert origin.to_dict() == stored


def test_reports_markdown_are_candidates(repo: Path) -> None:
    assert attachment_candidates(repo, DOCUMENT) == [
        "docs/reports/history-split.md",
        "docs/reports/other.md",
    ]
    assert attachment_candidates(repo, "cart.py") == []


def test_materials_separate_main_and_attachment_by_headings(tmp_path: Path) -> None:
    memo = AttachmentContent(MEMO, "# 検証メモ\n本文\n", stale=False)
    with_memo = design_materials(tmp_path, DOCUMENT, "# 題\n", [], (memo,))
    titles = [title for title, _ in with_memo.sections()]
    assert titles[:2] == ["主の設計書", f"付属の文書: {MEMO}"]
    assert "本文" in with_memo.text()
    without = design_materials(tmp_path, DOCUMENT, "# 題\n", [])
    assert next(title for title, _ in without.sections()) == "設計書の全文"


def test_design_shards_carry_attachments(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    plan = shards.build_plan(review, MICRO)
    assert plan.kind is shards.ShardPlanKind.DESIGN
    for shard in plan.shards:
        assert isinstance(shard, shards.DesignDocumentShard | shards.DesignFileShard)
        insert = sharded._design_insert(shard)
        assert "#### 主の設計書" in insert
        assert f"#### 付属の文書: {MEMO}" in insert


def test_every_design_shard_accepts_attachment_paths_as_evidence(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    document_shard, file_shard = shards.build_plan(review, MICRO).shards
    assert shards.scope_paths(document_shard) == {DOCUMENT, MEMO}
    assert shards.scope_paths(file_shard) >= {DOCUMENT, MEMO, "cart.py"}


def test_finding_grounded_on_an_attachment_survives_the_scope_filter(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    fake = MicroFake({1: [d_finding("D1", MEMO)], 2: [d_finding("D1", MEMO)]})
    run = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    assert run.failed_shards == 0
    assert triaged_dicts(run.findings)
    assert all(
        location["path"] == MEMO
        for found in triaged_dicts(run.findings)
        for location in found["evidence"]
    )


def test_attachment_version_change_triggers_refresh(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    assert not refresh_review(review.id).updated
    (repo / MEMO).write_text("# 検証メモ\n\n改訂した。\n", encoding="utf-8")
    commit_all(repo, "revise memo")
    refreshed = refresh_review(review.id)
    assert refreshed.updated
    origin = refreshed.review.origin
    assert isinstance(origin, DesignDocOrigin)
    assert origin.attachments[0].version == document_version((repo / MEMO).read_bytes())


def test_read_attachments_returns_typed_contents_not_stale_when_unchanged(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    read = read_attachments(review)
    assert read == (AttachmentContent(MEMO, "# 検証メモ\n\n分割で履歴が残る。\n", stale=False),)
    assert version_drifts(review, read_document(repo / DOCUMENT), read) == ()


def test_read_attachments_marks_a_changed_document_stale(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    (repo / MEMO).write_text("# 検証メモ\n\n改訂した。\n", encoding="utf-8")
    read = read_attachments(review)
    assert [item.stale for item in read] == [True]
    assert read[0].text == "# 検証メモ\n\n改訂した。\n"
    drifts = version_drifts(review, read_document(repo / DOCUMENT), read)
    assert drifts == (VersionDrift(str(review.id), DriftedRole.ATTACHMENT, MEMO),)
    notice = drift_notice(drifts)
    assert notice is not None
    assert f"付属の文書 {MEMO} が保存した版と違います" in notice
    assert f"jigane refresh {review.id}" in notice


def test_drift_notice_is_none_without_drifts() -> None:
    assert drift_notice(()) is None


def test_shard_plan_carries_the_stale_attachment_drift(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    assert shards.build_plan(review, MICRO).drifts == ()
    (repo / MEMO).write_text("# 検証メモ\n\n改訂した。\n", encoding="utf-8")
    drifts = shards.build_plan(review, MICRO).drifts
    assert drifts == (VersionDrift(str(review.id), DriftedRole.ATTACHMENT, MEMO),)


def test_shard_plan_carries_the_stale_main_document_drift(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    path = repo / DOCUMENT
    path.write_text(path.read_text(encoding="utf-8") + "\n追記。\n", encoding="utf-8")
    drifts = shards.build_plan(review, MICRO).drifts
    assert drifts == (VersionDrift(str(review.id), DriftedRole.MAIN, DOCUMENT),)


def test_micro_run_reports_the_stale_main_document_as_notice(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    path = repo / DOCUMENT
    path.write_text(path.read_text(encoding="utf-8") + "\n追記。\n", encoding="utf-8")
    fake = MicroFake({1: [], 2: []})
    run = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    assert run.notice is not None
    assert f"設計書 {DOCUMENT} が保存した版と違います" in run.notice


def test_micro_run_reports_the_stale_attachment_as_notice(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT, attach=[MEMO])
    (repo / MEMO).write_text("# 検証メモ\n\n改訂した。\n", encoding="utf-8")
    fake = MicroFake({1: [], 2: []})
    run = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    assert run.notice is not None
    assert f"付属の文書 {MEMO} が保存した版と違います" in run.notice
