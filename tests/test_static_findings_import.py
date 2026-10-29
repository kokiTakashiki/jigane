import hashlib
import json
import shutil
from pathlib import Path

import pytest
from pr_support import commit_all, git
from sarif_support import log, result, run, write

from jigane.commands.subcommands.refresh import refresh_review
from jigane.design.review_documents import create_design_doc_review
from jigane.reviews.lifecycle import DeclaredInputs, ReviewError, create_range_review
from jigane.skills.macro import policy_sections
from jigane.static_analysis.intake import (
    NO_STATIC,
    STATIC_FINDINGS_FILE,
    ImportedStatic,
    SarifRequest,
    StaleStatic,
    StoredStatic,
    import_sarif,
    read_static_findings,
    sarif_request,
    select_for_refresh,
    static_to_save,
    static_touches,
    static_warnings,
)
from jigane.storage.store import ReviewId

MESSAGE = "UNIQUE-FINDING-MESSAGE-7731"


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    return {**repo_template, "root": str(root)}


def _sarif(root: Path, name: str, *, revision: str | None, level: str = "error") -> Path:
    extra: dict[str, object] = {}
    if revision is not None:
        extra["versionControlProvenance"] = [{"revisionId": revision}]
    document = log(run([result("edit.py", 2, MESSAGE, level=level)], **extra))
    return write(root.parent, name, document)


def _request(root: Path, path: Path) -> SarifRequest | None:
    return sarif_request([path], None, root)


def _static_ids(review_id: ReviewId) -> set[str]:
    stored = json.loads((review_id.directory() / "map.json").read_text(encoding="utf-8"))
    segments = stored.get("current_form", stored)["segments"]
    return {finding for segment in segments for finding in segment["static_finding_ids"]}


def test_revision_id_mismatch_stops_the_import(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    path = _sarif(root, "a.sarif", revision="0" * 40)
    with pytest.raises(ReviewError, match="revisionId"):
        create_range_review(
            root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
        )


def test_missing_revision_id_warns_and_stores_the_head_commit(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    path = _sarif(root, "a.sarif", revision=None)
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
    )
    assert any("revisionId が無く" in warning for warning in review.warnings)
    stored = read_static_findings(review.id.directory())
    assert stored is not None
    assert stored.analyzed_commit == repo["head"]
    assert stored.files[0].sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_static_findings_file_version_1_reads_back(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    path = _sarif(root, "a.sarif", revision=repo["head"])
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
    )
    raw = json.loads((review.id.directory() / STATIC_FINDINGS_FILE).read_text(encoding="utf-8"))
    assert raw["format"] == 1
    assert raw["analyzed_commit"] == repo["head"]
    assert "dropped" in raw
    stored = read_static_findings(review.id.directory())
    assert stored is not None
    assert [finding.id for finding in stored.findings] == ["S1"]


def test_review_without_sarif_writes_no_file(repo: dict[str, str]) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    assert not (review.id.directory() / STATIC_FINDINGS_FILE).exists()
    assert read_static_findings(review.id.directory()) is None


def test_new_range_with_sarif_raises_a_segment(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    path = _sarif(root, "a.sarif", revision=repo["head"])
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
    )
    assert _static_ids(review.id) == {"S1"}
    segments = json.loads((review.id.directory() / "map.json").read_text(encoding="utf-8"))[
        "segments"
    ]
    assert [s["kind"] for s in segments if s["static_finding_ids"]] == ["static_finding"]


def test_refresh_at_the_same_commit_overlays_again(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    path = _sarif(root, "a.sarif", revision=repo["head"])
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
    )
    refreshed = refresh_review(review.id)
    assert _static_ids(refreshed.review.id) == {"S1"}


def test_refresh_after_a_new_commit_warns_and_does_not_overlay(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    path = _sarif(root, "a.sarif", revision=repo["head"])
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
    )
    (root / "extra.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    commit_all(root, "add delta")
    refreshed = refresh_review(review.id)
    assert _static_ids(review.id) == set()
    assert any("--sarif で渡し直" in warning for warning in refreshed.review.warnings)
    stored = read_static_findings(review.id.directory())
    assert stored is not None
    assert stored.analyzed_commit == repo["head"]


def test_refresh_with_sarif_replaces_and_renumbers(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    first = _sarif(root, "a.sarif", revision=repo["head"])
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, first))
    )
    (root / "extra.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    new_head = commit_all(root, "add delta")
    assert git(root, "rev-parse", "HEAD") == new_head
    second = write(
        root.parent,
        "b.sarif",
        log(
            run(
                [result("extra.py", 1, "x"), result("new.py", 1, "y")],
                versionControlProvenance=[{"revisionId": new_head}],
            )
        ),
    )
    refreshed = refresh_review(review.id, DeclaredInputs(sarif=_request(root, second)))
    stored = read_static_findings(refreshed.review.id.directory())
    assert stored is not None
    assert stored.analyzed_commit == new_head
    assert [finding.id for finding in stored.findings] == ["S1", "S2"]


def test_finding_message_is_not_in_the_policy_group_materials(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    path = _sarif(root, "a.sarif", revision=repo["head"])
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
    )
    sections = policy_sections(review)
    text = "\n".join(
        [
            *(str(value) for value in sections.figures.values()),
            sections.repeats,
            sections.technology,
        ]
    )
    assert MESSAGE not in text
    assert MESSAGE not in json.dumps(review.map_kind.to_dict(), ensure_ascii=False)


def test_refresh_of_a_design_doc_review_refuses_sarif(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "plan.md").write_text("# 題\n\n## 1. 序論\n\n本文。\n", encoding="utf-8")
    review = create_design_doc_review(root, "docs/plan.md")
    path = _sarif(root, "a.sarif", revision=repo["head"])
    with pytest.raises(ReviewError, match="設計書のレビュー"):
        refresh_review(review.id, DeclaredInputs(sarif=_request(root, path)))


def test_static_selection_kinds_decide_overlay_warnings_and_save(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    request = _request(root, _sarif(root, "a.sarif", revision=repo["head"]))
    assert request is not None
    imported = import_sarif(request, repo["head"])
    assert isinstance(imported, ImportedStatic)
    assert static_to_save(imported) is imported.store
    assert [touch.finding_id for touch in static_touches(imported)] == ["S1"]
    review = create_range_review(root, "main..feature", "mine", DeclaredInputs(sarif=request))
    directory = review.id.directory()
    stored = select_for_refresh(directory, repo["head"], None)
    assert isinstance(stored, StoredStatic)
    assert static_to_save(stored) is None
    assert [touch.finding_id for touch in static_touches(stored)] == ["S1"]
    assert static_warnings(stored) == ()
    stale = select_for_refresh(directory, "1" * 40, None)
    assert isinstance(stale, StaleStatic)
    assert static_to_save(stale) is None
    assert static_touches(stale) == []
    assert len(static_warnings(stale)) == 1
    assert select_for_refresh(directory, "1" * 40, imported) is imported
    absent = select_for_refresh(directory / "none", repo["head"], None)
    assert absent is NO_STATIC
    assert static_to_save(absent) is None
    assert static_touches(absent) == []
    assert static_warnings(absent) == ()
