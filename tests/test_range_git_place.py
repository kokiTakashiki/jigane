import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pr_support import commit_all, git, remove_tree

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.subcommands.refresh import refresh_review
from jigane.reviews import review_formats
from jigane.reviews.catalog import load_review
from jigane.reviews.lifecycle import (
    LocalRange,
    RangeOrigin,
    Review,
    ReviewError,
    create_range_review,
    read_stored_local_range,
)
from jigane.reviews.range_head import (
    BaseLost,
    BranchMissingHead,
    FollowedBranchHead,
    GitPlace,
    GitPlaceKind,
    GitPlaceUnusable,
    KeepRecordedBase,
    ResolveAgain,
    ResolvedRangeHead,
    TrackedBranch,
    TrackedBranchKind,
    UntrackedHead,
    select_git_place,
    select_refresh_head,
)


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    return {**repo_template, "root": str(root)}


def _local_range(review: Review) -> LocalRange:
    assert isinstance(review.origin, RangeOrigin)
    return review.origin.range


def _add_worktree(repo: dict[str, str], tmp_path: Path, branch: str) -> Path:
    path = tmp_path / "linked"
    git(Path(repo["root"]), "worktree", "add", "-b", branch, str(path), "feature")
    return path


def test_range_review_records_the_common_git_directory_and_a_named_branch(
    repo: dict[str, str], tmp_path: Path
) -> None:
    linked = _add_worktree(repo, tmp_path, "topic")
    local_range = _local_range(create_range_review(linked, "main..topic", "mine"))
    assert local_range.git_directory == str((Path(repo["root"]) / ".git").resolve())
    assert local_range.head_tracking == TrackedBranch(TrackedBranchKind.NAMED_BRANCH, "topic")


def test_range_review_at_the_sha_of_the_current_branch_records_that_branch(
    repo: dict[str, str], tmp_path: Path
) -> None:
    linked = _add_worktree(repo, tmp_path, "topic")
    local_range = _local_range(create_range_review(linked, f"main..{repo['head']}", "mine"))
    assert local_range.head_tracking == TrackedBranch(TrackedBranchKind.CURRENT_BRANCH, "topic")


def test_range_review_at_a_detached_head_tracks_no_branch(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    git(root, "switch", "--detach")
    local_range = _local_range(create_range_review(root, f"main..{repo['head']}", "mine"))
    assert local_range.head_tracking is None


def test_range_review_at_a_sha_off_the_current_branch_tracks_no_branch(
    repo: dict[str, str],
) -> None:
    root = Path(repo["root"])
    git(root, "switch", "main")
    local_range = _local_range(create_range_review(root, f"main..{repo['head']}", "mine"))
    assert local_range.head_tracking is None


def test_range_review_at_head_is_untracked(repo: dict[str, str]) -> None:
    local_range = _local_range(create_range_review(Path(repo["root"]), "main..HEAD", "mine"))
    assert local_range.head_tracking is None


def test_refresh_runs_in_the_common_git_directory_after_the_worktree_is_removed(
    repo: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = _add_worktree(repo, tmp_path, "topic")
    review = create_range_review(linked, f"main..{repo['head']}", "mine")
    monkeypatch.chdir(tmp_path)
    git(Path(repo["root"]), "worktree", "remove", "--force", str(linked))
    assert not linked.exists()
    result = refresh_review(review.id)
    assert result.updated is False
    assert _local_range(result.review) == _local_range(review)


def test_refresh_without_the_working_directory_and_the_common_git_directory_fails(
    repo: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = _add_worktree(repo, tmp_path, "topic")
    review = create_range_review(linked, "main..topic", "mine")
    monkeypatch.chdir(tmp_path)
    remove_tree(linked)
    remove_tree(Path(repo["root"]))
    with pytest.raises(ReviewError, match="作業ディレクトリがありません"):
        refresh_review(review.id)


def test_review_v4_local_range_upgrades_with_default_tracking() -> None:
    raw: dict[str, Any] = {"local_range": {"range_text": "a..b", "working_directory": "/work"}}
    upgraded = review_formats._REVIEW_UPGRADES[4](raw)
    assert upgraded["local_range"] == {
        "range_text": "a..b",
        "working_directory": "/work",
        "git_directory": "",
        "head_tracking": None,
    }
    assert "git_directory" not in raw["local_range"]
    assert review_formats._REVIEW_UPGRADES[4]({"local_range": None}) == {"local_range": None}
    assert read_stored_local_range(upgraded["local_range"]) == LocalRange("a..b", "/work")


def test_range_review_at_a_tag_tracks_the_current_branch_only_when_it_points_there(
    repo: dict[str, str], tmp_path: Path
) -> None:
    linked = _add_worktree(repo, tmp_path, "topic")
    git(linked, "tag", "marker", "topic")
    at_branch = _local_range(create_range_review(linked, "main..marker", "at"))
    assert at_branch.head_tracking == TrackedBranch(TrackedBranchKind.CURRENT_BRANCH, "topic")
    git(linked, "switch", "--detach", "main")
    git(linked, "switch", "-c", "other")
    away = _local_range(create_range_review(linked, "main..marker", "away"))
    assert away.head_tracking is None


def test_range_review_at_a_remote_tracking_branch_is_not_a_named_branch(
    repo: dict[str, str], tmp_path: Path
) -> None:
    linked = _add_worktree(repo, tmp_path, "topic")
    git(linked, "update-ref", "refs/remotes/origin/topic", "topic")
    tracked = _local_range(create_range_review(linked, "main..origin/topic", "at"))
    assert tracked.head_tracking == TrackedBranch(TrackedBranchKind.CURRENT_BRANCH, "topic")
    git(linked, "switch", "--detach", "main")
    git(linked, "switch", "-c", "other")
    away = _local_range(create_range_review(linked, "main..origin/topic", "away"))
    assert away.head_tracking is None


def test_refresh_falls_back_to_the_common_git_directory_when_the_directory_is_not_git(
    repo: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = _add_worktree(repo, tmp_path, "topic")
    review = create_range_review(linked, f"main..{repo['head']}", "mine")
    monkeypatch.chdir(tmp_path)
    remove_tree(linked)
    linked.mkdir()
    result = refresh_review(review.id)
    assert result.updated is False


def test_git_place_is_the_common_git_directory_when_the_working_directory_is_not_git(
    repo: dict[str, str], tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    common = Path(repo["root"]) / ".git"
    assert select_git_place(str(plain), str(common)) == GitPlace(
        common, GitPlaceKind.COMMON_DIRECTORY
    )
    assert select_git_place(str(plain), "") is GitPlaceUnusable.NOT_A_REPOSITORY
    assert select_git_place(str(tmp_path / "gone"), str(common)) == GitPlace(
        common, GitPlaceKind.COMMON_DIRECTORY
    )
    assert select_git_place("", "") is GitPlaceUnusable.NO_WORKING_DIRECTORY


def test_unknown_stored_local_range_key_is_not_reported_as_a_head_tracking_error() -> None:
    stored = {"range_text": "a..b", "working_directory": "/w", "head_tracking": None, "extra": 1}
    with pytest.raises(ReviewError) as caught:
        read_stored_local_range(stored)
    assert "head_tracking" not in str(caught.value)


@pytest.mark.parametrize(
    "tracking",
    [{"kind": "other", "branch": "x"}, {"kind": "named_branch"}, "named_branch"],
)
def test_malformed_stored_head_tracking_is_a_review_error(tracking: object) -> None:
    stored = {"range_text": "a..b", "working_directory": "/w", "head_tracking": tracking}
    with pytest.raises(ReviewError, match="head_tracking"):
        read_stored_local_range(stored)
    with pytest.raises(ReviewError, match="head_tracking"):
        read_stored_local_range({"range_text": "a..b", "working_directory": "/w"})


def _worktree_at_a_sha(repo: dict[str, str], tmp_path: Path) -> tuple[Path, Review]:
    linked = _add_worktree(repo, tmp_path, "topic")
    return linked, create_range_review(linked, f"main..{repo['head']}", "mine")


def _remove_worktree(repo: dict[str, str], linked: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(linked.parent)
    git(Path(repo["root"]), "worktree", "remove", "--force", str(linked))


def test_refresh_of_a_range_at_a_sha_follows_the_recorded_branch_after_the_worktree_is_removed(
    repo: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked, review = _worktree_at_a_sha(repo, tmp_path)
    (linked / "extra.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    new_head = commit_all(linked, "add delta")
    _remove_worktree(repo, linked, monkeypatch)
    result = refresh_review(review.id)
    assert result.updated is True
    assert result.head == FollowedBranchHead(new_head, "topic")
    assert result.review.diff_origin is not None
    assert result.review.diff_origin.head_sha == new_head
    assert result.to_dict()["head"] == {
        "branch": "topic",
        "sha": new_head,
        "reason": "followed_branch",
    }


def test_refresh_pins_the_recorded_sha_when_the_recorded_branch_is_gone(
    repo: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    linked, review = _worktree_at_a_sha(repo, tmp_path)
    _remove_worktree(repo, linked, monkeypatch)
    git(Path(repo["root"]), "branch", "-D", "topic")
    assert main(["refresh", str(review.id)]) == EXIT_OK
    output = capsys.readouterr().out
    assert "変更なし" in output
    assert f"先端: topic {repo['head'][:12]}" in output
    assert "記録した枝が無いので" in output
    assert "作り直して" in output
    assert main(["refresh", str(review.id), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["head"] == {
        "branch": "topic",
        "sha": repo["head"],
        "reason": "branch_missing",
    }


def test_refresh_of_an_untracked_range_without_a_worktree_keeps_the_recorded_sha(
    repo: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = _add_worktree(repo, tmp_path, "topic")
    git(linked, "switch", "--detach")
    review = create_range_review(linked, f"main..{repo['head']}", "mine")
    _remove_worktree(repo, linked, monkeypatch)
    result = refresh_review(review.id)
    assert result.head == UntrackedHead(repo["head"])


def test_refresh_stops_when_the_recorded_branch_no_longer_contains_the_range_base(
    repo: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked, review = _worktree_at_a_sha(repo, tmp_path)
    _remove_worktree(repo, linked, monkeypatch)
    root = Path(repo["root"])
    unrelated = git(root, "commit-tree", "main^{tree}", "-m", "unrelated")
    git(root, "branch", "-f", "topic", unrelated)
    with pytest.raises(ReviewError, match="基点を失った") as raised:
        refresh_review(review.id)
    message = str(raised.value)
    assert "topic" in message
    assert unrelated in message
    assert _local_range(review) == _local_range(load_review(review.id))


def test_refresh_resolving_the_range_text_reports_the_resolved_head(repo: dict[str, str]) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    result = refresh_review(review.id)
    assert result.head == ResolvedRangeHead(repo["head"], "feature")


class _TableQueries:
    """表で答える偽物の口。呼ばれた問い合わせを順に記録する。"""

    def __init__(
        self,
        names: dict[str, str] | None = None,
        tips: dict[str, str] | None = None,
        ancestors: set[tuple[str, str]] | None = None,
    ) -> None:
        self.names = names or {}
        self.tips = tips or {}
        self.ancestors = ancestors or set()
        self.calls: list[str] = []

    def full_reference_name(self, written: str) -> str:
        self.calls.append(f"name:{written}")
        return self.names.get(written, "")

    def branch_tip(self, name: str) -> str | None:
        self.calls.append(f"tip:{name}")
        return self.tips.get(name)

    def is_ancestor(self, base: str, head: str) -> bool:
        self.calls.append(f"ancestor:{base}:{head}")
        return (base, head) in self.ancestors


_COMMON = GitPlace(Path("/nowhere"), GitPlaceKind.COMMON_DIRECTORY)
_WORKING = GitPlace(Path("/nowhere"), GitPlaceKind.WORKING_DIRECTORY)
_NAMED = TrackedBranch(TrackedBranchKind.NAMED_BRANCH, "topic")


def test_choice_follows_the_recorded_branch_tip_without_git() -> None:
    queries = _TableQueries(tips={"topic": "tip1"}, ancestors={("mb", "tip1")})
    choice = select_refresh_head(queries, _COMMON, "main..topic", _NAMED, "old", "mb")
    assert choice == KeepRecordedBase(FollowedBranchHead("tip1", "topic"))
    assert queries.calls == ["tip:topic", "ancestor:mb:tip1"]


def test_choice_pins_the_recorded_sha_when_the_branch_is_missing_without_git() -> None:
    queries = _TableQueries(ancestors={("mb", "old")})
    choice = select_refresh_head(queries, _COMMON, "main..topic", _NAMED, "old", "mb")
    assert choice == KeepRecordedBase(BranchMissingHead("old", "topic"))


def test_choice_reports_a_lost_base_without_git() -> None:
    queries = _TableQueries(tips={"topic": "tip1"})
    choice = select_refresh_head(queries, _COMMON, "main..topic", _NAMED, "old", "mb")
    assert choice == BaseLost("tip1", "topic")


def test_choice_resolves_again_for_a_named_branch_without_asking() -> None:
    queries = _TableQueries()
    choice = select_refresh_head(queries, _WORKING, "main..topic", _NAMED, "old", "mb")
    assert choice == ResolveAgain("topic")
    assert queries.calls == []


def test_choice_asks_the_reference_name_only_for_an_untracked_working_directory_range() -> None:
    queries = _TableQueries(names={"v1": "refs/tags/v1"}, ancestors={("mb", "old")})
    choice = select_refresh_head(queries, _WORKING, "main..v1", None, "old", "mb")
    assert choice == KeepRecordedBase(UntrackedHead("old"))
    assert queries.calls == ["name:v1", "ancestor:mb:old"]
