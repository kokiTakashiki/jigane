import io
import json
import tarfile
from pathlib import Path

import pytest
from fakes import run_command_stub
from pr_support import commit_all, git

from jigane.git import process
from jigane.git.process import CommandError
from jigane.judgment_map.classification import FilePair
from jigane.pull_request_context import fetching as pull_request
from jigane.pull_request_context.fetching import (
    PullRequestMetadata,
    changed_file_pairs,
    extract_tree,
    fetch_metadata,
    fetch_pull_request,
    parse_pull_request_url,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    return path


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octo/hello/pull/12",
        "https://github.com/octo/hello/pull/12/files",
        "https://github.com/octo/hello.git/pull/12",
    ],
)
def test_parse_url(url: str) -> None:
    assert parse_pull_request_url(url) == ("octo", "hello", 12)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octo/hello",
        "https://github.com/octo/hello/issues/12",
        "http://github.com/octo/hello/pull/12",
        "https://example.com/octo/hello/pull/12",
        "https://github.com/octo/hello/pull/abc",
    ],
)
def test_parse_url_rejects(url: str) -> None:
    with pytest.raises(ValueError, match="URL の形が不正"):
        parse_pull_request_url(url)


def test_changed_file_pairs_and_extract(repo: Path, tmp_path: Path) -> None:
    (repo / "keep.txt").write_text("keep\n", encoding="utf-8")
    (repo / "edit.txt").write_text("before\n", encoding="utf-8")
    (repo / "gone.txt").write_text("gone\n", encoding="utf-8")
    (repo / "old name.txt").write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
    base = commit_all(repo, "base")
    (repo / "edit.txt").write_text("after\n", encoding="utf-8")
    (repo / "gone.txt").unlink()
    (repo / "added.txt").write_text("new\n", encoding="utf-8")
    (repo / "old name.txt").rename(repo / "新しい名前.txt")
    head = commit_all(repo, "head")

    pairs = changed_file_pairs(repo, base, head)

    assert sorted(pairs, key=repr) == sorted(
        [
            FilePair("edit.txt", "edit.txt"),
            FilePair("gone.txt", None),
            FilePair(None, "added.txt"),
            FilePair("old name.txt", "新しい名前.txt"),
        ],
        key=repr,
    )

    destination = tmp_path / "out"
    destination.mkdir()
    (destination / "stale.txt").write_text("stale", encoding="utf-8")
    extract_tree(repo, base, destination)
    assert not (destination / "stale.txt").exists()
    assert (destination / "edit.txt").read_text(encoding="utf-8") == "before\n"
    assert (destination / "gone.txt").exists()


def test_type_change(repo: Path) -> None:
    (repo / "link").write_text("target\n", encoding="utf-8")
    base = commit_all(repo, "base")
    (repo / "link").unlink()
    (repo / "link").symlink_to("elsewhere")
    head = commit_all(repo, "head")
    assert changed_file_pairs(repo, base, head) == [FilePair("link", "link")]


def test_merge_base_ignores_base_branch_progress(repo: Path) -> None:
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    fork_point = commit_all(repo, "fork point")
    git(repo, "checkout", "-b", "feature")
    (repo / "feature.txt").write_text("f\n", encoding="utf-8")
    head = commit_all(repo, "feature")
    git(repo, "checkout", "main")
    (repo / "main.txt").write_text("m\n", encoding="utf-8")
    base = commit_all(repo, "main progress")

    metadata = PullRequestMetadata("o", "r", 1, "u", "t", "b", base, head, "dev")
    # origin への fetch を、検査用のリポジトリ自身への fetch に置き換える
    git(repo, "remote", "add", "origin", str(repo))
    assert fetch_pull_request(repo, metadata) == fork_point
    assert changed_file_pairs(repo, fork_point, head) == [FilePair(None, "feature.txt")]


def test_extract_rejects_escaping_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(args: list[str]) -> str:
        archive_path = Path(args[args.index("-o") + 1])
        with tarfile.open(archive_path, "w") as archive:
            info = tarfile.TarInfo("../escaped.txt")
            payload = b"x"
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        return ""

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    destination = tmp_path / "out"
    with pytest.raises(tarfile.FilterError):
        extract_tree(tmp_path, "HEAD", destination)
    assert not (tmp_path / "escaped.txt").exists()


def test_fetch_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(args)
        return json.dumps(
            {
                "number": 5,
                "url": "https://github.com/o/r/pull/5",
                "title": "表題",
                "body": "本文",
                "baseRefOid": "b" * 40,
                "headRefOid": "h" * 40,
                "author": {"login": "dev"},
            }
        )

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    metadata = fetch_metadata("o", "r", 5)
    assert calls[0][:6] == ["gh", "pr", "view", "5", "-R", "o/r"]
    assert metadata.base_sha == "b" * 40
    assert metadata.head_sha == "h" * 40
    assert metadata.title == "表題"
    assert metadata.author == "dev"
    # closingIssuesReferences は要求しない（背景から issue を外した）
    assert "closingIssuesReferences" not in calls[0][calls[0].index("--json") + 1]


GIT_PROBE = ["git", "--version"]


def test_ensure_cache_clones_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        if args == GIT_PROBE:
            return ""
        calls.append(args)
        Path(args[4]).mkdir()
        return ""

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    first = pull_request.ensure_repository_cache("o", "r")
    second = pull_request.ensure_repository_cache("o", "r")
    assert first == second == tmp_path / "repositories" / "o" / "r.git"
    assert len(calls) == 1
    assert calls[0][-2:] == ["--bare", "--filter=blob:none"]


def test_ensure_cache_failed_clone_leaves_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    attempts = 0

    def fake_run(args: list[str]) -> str:
        nonlocal attempts
        if args == GIT_PROBE:
            return ""
        attempts += 1
        Path(args[4]).mkdir()
        (Path(args[4]) / "HEAD").write_text("partial", encoding="utf-8")
        if attempts == 1:
            raise CommandError(args, "network")
        return ""

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    parent = tmp_path / "repositories" / "o"
    with pytest.raises(CommandError):
        pull_request.ensure_repository_cache("o", "r")
    assert list(parent.iterdir()) == []
    # 失敗の後の再実行は、壊れたものに阻まれずにもう一度 clone する
    assert pull_request.ensure_repository_cache("o", "r") == parent / "r.git"
    assert attempts == 2
    assert [path.name for path in parent.iterdir()] == ["r.git"]


def test_ensure_cache_keeps_cache_made_by_concurrent_clone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    final = tmp_path / "repositories" / "o" / "r.git"

    def fake_run(args: list[str]) -> str:
        if args == GIT_PROBE:
            return ""
        Path(args[4]).mkdir()
        # 自分が clone している間に別のプロセスが先に同じ名前を完成させた
        final.mkdir()
        (final / "HEAD").write_text("winner", encoding="utf-8")
        return ""

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    assert pull_request.ensure_repository_cache("o", "r") == final
    assert (final / "HEAD").read_text(encoding="utf-8") == "winner"
    assert [path.name for path in final.parent.iterdir()] == ["r.git"]


def test_command_failure_is_wrapped(tmp_path: Path) -> None:
    with pytest.raises(CommandError) as raised:
        process.run_command(["git", "rev-parse", "nope"], cwd=tmp_path)
    assert raised.value.command[0] == "git"


@pytest.mark.parametrize(
    ("owner", "repo"),
    [("..", "r"), ("o", ".."), ("-o", "r"), ("o", "-r"), ("o/x", "r"), ("", "r")],
)
def test_ensure_cache_rejects_names_outside_the_component_rule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, owner: str, repo: str
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    calls: list[list[str]] = []

    def record(args: list[str]) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(process, "run_command", run_command_stub(record))
    with pytest.raises(ValueError, match="名前が不正"):
        pull_request.ensure_repository_cache(owner, repo)
    assert calls == []
    assert not (tmp_path / "repositories").exists()
