from pathlib import Path

import pytest
from test_review_relations import save

from jigane.reviews import catalog
from jigane.reviews import resolution as resolution_module
from jigane.reviews.lifecycle import _assign_numbers
from jigane.storage.review_access import read_stored_file
from jigane.storage.review_error import ReviewError
from jigane.storage.store import REVIEW_FILE, ReviewKind
from jigane.storage.symlink_guard import StoredFileSymlinkError

OUTSIDE_TEXT = '{"secret": true}'


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _replace_with_link(directory: Path, name: str, tmp_path: Path) -> Path:
    """保存先のファイルを、外のファイルへの symlink に差し替える。外のファイルを返す。"""
    outside = tmp_path / f"outside-{name}"
    outside.write_text(OUTSIDE_TEXT, encoding="utf-8")
    (directory / name).unlink()
    (directory / name).symlink_to(outside)
    return outside


def test_read_stored_file_reads_a_regular_file(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("本文", encoding="utf-8")
    assert read_stored_file(tmp_path, "f.txt") == "本文"


def test_read_stored_file_raises_file_not_found_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_stored_file(tmp_path, "f.txt")


def test_read_stored_file_refuses_a_symlink(tmp_path: Path) -> None:
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "f.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(StoredFileSymlinkError):
        read_stored_file(tmp_path, "f.txt")


def test_read_review_at_refuses_a_symlinked_review_file(project: Path, tmp_path: Path) -> None:
    directory = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00").id.directory()
    _replace_with_link(directory, "review.json", tmp_path)
    with pytest.raises(ReviewError) as raised:
        catalog.read_review_at(directory)
    assert isinstance(raised.value.__cause__, StoredFileSymlinkError)


def test_read_review_at_refuses_a_symlinked_map_file(project: Path, tmp_path: Path) -> None:
    directory = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00").id.directory()
    _replace_with_link(directory, "map.json", tmp_path)
    with pytest.raises(ReviewError) as raised:
        catalog.read_review_at(directory)
    assert isinstance(raised.value.__cause__, StoredFileSymlinkError)


def test_check_stored_id_passes_a_symlinked_review_file_for_recovery(
    project: Path, tmp_path: Path
) -> None:
    saved = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")
    _replace_with_link(saved.id.directory(), "review.json", tmp_path)
    assert catalog.check_stored_id(saved.id) == saved.id
    with pytest.raises(ReviewError):
        catalog.read_review_at(saved.id.directory())


def test_summary_read_refuses_a_symlinked_map_file(project: Path, tmp_path: Path) -> None:
    directory = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00").id.directory()
    _replace_with_link(directory, "map.json", tmp_path)
    with pytest.raises(ReviewError) as raised:
        catalog._read_review_summary(directory)
    assert isinstance(raised.value.__cause__, StoredFileSymlinkError)


def test_assign_numbers_refuses_a_symlinked_regions_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(OUTSIDE_TEXT, encoding="utf-8")
    directory = tmp_path / "review"
    directory.mkdir()
    (directory / "regions.json").symlink_to(outside)
    with pytest.raises(StoredFileSymlinkError):
        _assign_numbers([], directory)


def test_assign_numbers_refuses_a_broken_symlinked_regions_file(tmp_path: Path) -> None:
    directory = tmp_path / "review"
    directory.mkdir()
    (directory / "regions.json").symlink_to(tmp_path / "missing.json")
    with pytest.raises(StoredFileSymlinkError):
        _assign_numbers([], directory)


def test_stored_ids_named_does_not_read_a_symlinked_review_file(
    project: Path, tmp_path: Path
) -> None:
    directory = save(
        project, "payment", ReviewKind.FEATURE, "2026-01-01T00:00:00+00:00"
    ).id.directory()
    assert [i.key for i in resolution_module._stored_ids_named("payment")] == ["payment"]
    _replace_with_link(directory, REVIEW_FILE, tmp_path)
    # StoredFileSymlinkError は OSError なので、読めない記録として候補から外れる
    assert resolution_module._stored_ids_named("payment") == []


def test_listing_skips_a_review_with_a_symlinked_map_file(project: Path, tmp_path: Path) -> None:
    linked = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")
    kept = save(project, "r-2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00")
    _replace_with_link(linked.id.directory(), "map.json", tmp_path)
    listed = catalog.list_review_summaries_counting_skipped()
    assert [summary.id for summary in listed.summaries] == [kept.id]
    assert listed.skipped == 1
