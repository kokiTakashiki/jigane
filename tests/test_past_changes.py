from pathlib import Path

from pr_support import commit_all, git

from jigane.judgment_map.classification import FilePair
from jigane.pull_request_context import background
from jigane.pull_request_context.background import (
    Background,
    PastCommit,
    PastPullRequest,
    parse_background,
)


def make_history(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    (root / "a.py").write_text("x = 0\n", encoding="utf-8")
    (root / "b.py").write_text("y = 0\n", encoding="utf-8")
    commit_all(root, "init")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(root, "direct push")
    (root / "a.py").write_text("x = 2\n", encoding="utf-8")
    commit_all(root, "Bump x (#3)")
    git(root, "checkout", "-b", "side")
    (root / "a.py").write_text("x = 3\n", encoding="utf-8")
    commit_all(root, "side work")
    git(root, "checkout", "main")
    git(root, "merge", "--no-ff", "side", "-m", "Merge branch 'side'")
    (root / "b.py").write_text("y = 1\n", encoding="utf-8")
    commit_all(root, "unrelated to a")
    return root, git(root, "rev-parse", "HEAD")


def test_range_history_keeps_plain_commits_and_pull_requests(tmp_path: Path) -> None:
    root, head = make_history(tmp_path)
    found = background.find_past_changes(root, head, [FilePair("a.py", "a.py")])
    # 同じ秒のコミットは並びが揃わないので、集合で比べる。番号の無いマージのコミットは入らない
    assert sorted((type(c).__name__, c.title) for c in found) == [
        ("PastCommit", "direct push"),
        ("PastCommit", "init"),
        ("PastCommit", "side work"),
        ("PastPullRequest", "Bump x"),
    ]
    pull_request = next(c for c in found if isinstance(c, PastPullRequest))
    assert pull_request.number == 3
    commit = next(c for c in found if c.title == "side work")
    assert isinstance(commit, PastCommit)
    assert len(commit.sha) == 40
    assert commit.files == ("a.py",)


def test_range_history_without_paths_is_empty(tmp_path: Path) -> None:
    root, head = make_history(tmp_path)
    assert background.find_past_changes(root, head, []) == []


def test_background_round_trips_both_kinds() -> None:
    original = Background(
        (),
        (PastPullRequest(3, "Bump x", ("a.py",)), PastCommit("a" * 40, "plain", ("b.py",))),
        (),
    )
    data = original.to_dict()
    assert [c["kind"] for c in data["past_changes"]] == ["pull_request", "commit"]
    assert Background.from_dict(data) == original


def test_background_reads_old_record_with_past_pull_requests() -> None:
    old = {
        "issues": [],
        "documents": [],
        "past_pull_requests": [{"number": 5, "title": "Tune", "files": ["x.py"]}],
        "owners": [],
    }
    assert parse_background(old).past_changes == (PastPullRequest(5, "Tune", ("x.py",)),)
