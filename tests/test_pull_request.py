import json
import os
import stat
import tarfile
from pathlib import Path

import pytest
from fakes import run_command_stub
from pr_support import commit_all, git, isolated_git_environment

from jigane.git import process
from jigane.git.process import CommandError
from jigane.git.revision import CommitSha, parse_commit_sha
from jigane.judgment_map.classification import FilePair
from jigane.pull_request_context import fetching as pull_request
from jigane.pull_request_context import gh
from jigane.pull_request_context.fetching import (
    PullRequestMetadata,
    _read_tree,
    _TreeEntry,
    _write_tree,
    changed_file_pairs,
    extract_tree,
    fetch_metadata,
    fetch_pull_request,
    parse_pull_request_url,
)
from jigane.storage import store

# 読めない gh の出力の文は、何が起きたかとどうすればよいかの両方を持つ
UNREADABLE_OUTPUT = (
    "JSON として読めませんでした。構文に誤りがあるか、入れ子が深すぎます。"
    "gh を新しいバージョンに更新してから"
)


def _sha(text: str) -> CommitSha:
    return parse_commit_sha("x", text)


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

    pairs = changed_file_pairs(repo, _sha(base), _sha(head))

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
    extract_tree(repo, _sha(base), destination)
    assert not (destination / "stale.txt").exists()
    assert (destination / "edit.txt").read_text(encoding="utf-8") == "before\n"
    assert (destination / "gone.txt").exists()


def test_type_change(repo: Path) -> None:
    (repo / "link").write_text("target\n", encoding="utf-8")
    base = commit_all(repo, "base")
    (repo / "link").unlink()
    (repo / "link").symlink_to("elsewhere")
    head = commit_all(repo, "head")
    assert changed_file_pairs(repo, _sha(base), _sha(head)) == [FilePair("link", "link")]


def test_merge_base_ignores_base_branch_progress(repo: Path) -> None:
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    fork_point = commit_all(repo, "fork point")
    git(repo, "checkout", "-b", "feature")
    (repo / "feature.txt").write_text("f\n", encoding="utf-8")
    head = commit_all(repo, "feature")
    git(repo, "checkout", "main")
    (repo / "main.txt").write_text("m\n", encoding="utf-8")
    base = commit_all(repo, "main progress")

    metadata = PullRequestMetadata(
        "o", "r", 1, "u", "t", "b", parse_commit_sha("x", base), parse_commit_sha("x", head), "dev"
    )
    # origin への fetch を、検査用のリポジトリ自身への fetch に置き換える
    git(repo, "remote", "add", "origin", str(repo))
    assert str(fetch_pull_request(repo, metadata)) == fork_point
    pairs = changed_file_pairs(repo, _sha(fork_point), _sha(head))
    assert pairs == [FilePair(None, "feature.txt")]


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
                "headRefOid": "c" * 40,
                "author": {"login": "dev"},
            }
        )

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    metadata = fetch_metadata("o", "r", 5)
    assert calls[0][:6] == ["gh", "pr", "view", "5", "-R", "o/r"]
    assert str(metadata.base_sha) == "b" * 40
    assert str(metadata.head_sha) == "c" * 40
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
        Path(args[4]).mkdir(exist_ok=True)
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
        Path(args[4]).mkdir(exist_ok=True)
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
        Path(args[4]).mkdir(exist_ok=True)
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


def _fake_view_output(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    monkeypatch.setattr(process, "run_command", run_command_stub(lambda _args: output))


def test_fetch_metadata_rejects_too_deep_output(monkeypatch: pytest.MonkeyPatch) -> None:
    depth = gh.MAX_RESPONSE_NESTING + 1
    _fake_view_output(monkeypatch, "[" * depth + "]" * depth)
    with pytest.raises(store.ReviewError, match=UNREADABLE_OUTPUT):
        fetch_metadata("o", "r", 5)


def test_fetch_metadata_rejects_unparsable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_view_output(monkeypatch, "{ not json SECRET")
    with pytest.raises(store.ReviewError, match=UNREADABLE_OUTPUT) as raised:
        fetch_metadata("o", "r", 5)
    assert "SECRET" not in str(raised.value)


def test_fetch_metadata_converts_recursion_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def recursing(_text: str, **_options: object) -> object:
        raise RecursionError

    monkeypatch.setattr(json, "loads", recursing)
    _fake_view_output(monkeypatch, "{}")
    with pytest.raises(store.ReviewError, match=UNREADABLE_OUTPUT):
        fetch_metadata("o", "r", 5)


def _archive_extract(repo: Path, commit: str, destination: Path) -> None:
    """従来の展開。git archive を tar の data フィルタで展開する。"""
    destination.mkdir()
    archive_path = destination.parent / "legacy.tar"
    archive_path.write_bytes(
        process.run_command_bytes(
            ["git", "archive", "--format=tar", commit],
            cwd=repo,
            env=isolated_git_environment(),
        )
    )
    with tarfile.open(archive_path) as archive:
        archive.extractall(destination, filter="data")


def _snapshot(root: Path) -> dict[str, tuple[str, object]]:
    result: dict[str, tuple[str, object]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("link", path.readlink())
        elif path.is_file():
            executable = bool(path.stat().st_mode & stat.S_IXUSR)
            result[relative] = ("file", (path.read_bytes(), executable))
        else:
            result[relative] = ("dir", None)
    return result


def test_extract_matches_archive_without_filter(repo: Path, tmp_path: Path) -> None:
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    (repo / "dir" / "deep").mkdir(parents=True)
    (repo / "dir" / "deep" / "日本語 名.txt").write_bytes(b"\xff\x00binary\n")
    (repo / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "run.sh").chmod(0o755)
    (repo / "dir" / "link").symlink_to("deep/日本語 名.txt")
    (repo / "same.txt").write_text("a\n", encoding="utf-8")
    commit = commit_all(repo, "base")

    legacy = tmp_path / "legacy"
    _archive_extract(repo, commit, legacy)
    actual = tmp_path / "actual"
    extract_tree(repo, _sha(commit), actual)

    assert _snapshot(actual) == _snapshot(legacy)
    assert (actual / "dir" / "link").is_symlink()
    assert os.access(actual / "run.sh", os.X_OK) or os.name == "nt"


def test_extract_empty_tree(repo: Path, tmp_path: Path) -> None:
    git(repo, "commit", "--allow-empty", "-m", "empty")
    destination = tmp_path / "out"
    extract_tree(repo, _sha(git(repo, "rev-parse", "HEAD")), destination)
    assert list(destination.iterdir()) == []


def test_extract_skips_submodule_entries(repo: Path, tmp_path: Path) -> None:
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        "160000,1234567890123456789012345678901234567890,sub",
    )
    git(repo, "commit", "-m", "with submodule")
    destination = tmp_path / "out"
    extract_tree(repo, _sha(git(repo, "rev-parse", "HEAD")), destination)
    assert sorted(path.name for path in destination.iterdir()) == ["a.txt"]


@pytest.mark.parametrize("target", ["/etc/passwd", "../outside"])
def test_extract_rejects_symlink_leaving_destination(
    repo: Path, tmp_path: Path, target: str
) -> None:
    (repo / "link").symlink_to(target)
    commit = commit_all(repo, "escaping link")
    with pytest.raises(tarfile.FilterError):
        extract_tree(repo, _sha(commit), tmp_path / "out")


def _filter_repo(repo: Path, tmp_path: Path) -> tuple[str, Path]:
    marker = tmp_path / "filter-ran"
    script = tmp_path / "smudge.sh"
    script.write_text(f'#!/bin/sh\necho ran > "{marker.as_posix()}"\ncat\n', encoding="utf-8")
    script.chmod(0o755)
    (repo / ".gitattributes").write_text("* filter=mark\n", encoding="utf-8")
    # write_text は Windows で改行を CRLF に変える。コミットする中身を LF のバイト列に固定する
    (repo / "a.txt").write_bytes(b"committed\n")
    commit = commit_all(repo, "with filter")
    git(repo, "config", "filter.mark.smudge", f'sh "{script.as_posix()}"')
    return commit, marker


def test_plain_archive_runs_smudge_filter(repo: Path, tmp_path: Path) -> None:
    commit, marker = _filter_repo(repo, tmp_path)
    _archive_extract(repo, commit, tmp_path / "legacy")
    assert marker.exists()


def test_extract_does_not_run_smudge_filter(repo: Path, tmp_path: Path) -> None:
    commit, marker = _filter_repo(repo, tmp_path)
    destination = tmp_path / "out"
    extract_tree(repo, _sha(commit), destination)
    assert not marker.exists()
    assert (destination / "a.txt").read_bytes() == b"committed\n"


def _hash_tree(repo: Path, raw: bytes) -> str:
    return (
        process.run_command_bytes(
            ["git", "hash-object", "-w", "-t", "tree", "--literally", "--stdin"],
            cwd=repo,
            env=isolated_git_environment(),
            stdin=raw,
        )
        .decode()
        .strip()
    )


def _raw_tree_commit(repo: Path, entries: list[tuple[str, str, str]]) -> CommitSha:
    """(mode, パス名, オブジェクト名) の木を、検証なしで作ってコミットする。"""
    raw = b"".join(
        mode.encode() + b" " + os.fsencode(name) + b"\0" + bytes.fromhex(object_id)
        for mode, name, object_id in entries
    )
    tree = _hash_tree(repo, raw)
    return _sha(git(repo, "commit-tree", tree, "-m", "crafted"))


def _blob(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    return git(repo, "hash-object", "-w", name)


def test_read_tree_rejects_path_the_filesystem_cannot_decode(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows のファイル名は UTF-8 のバイト列でなく、os.fsdecode が失敗する。その経路を再現する
    real_fsdecode = os.fsdecode

    # subprocess も os.fsdecode を使うので、試す名前のときだけ失敗させる
    def failing_fsdecode(raw: bytes | str) -> str:
        if raw == b"name.txt":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return real_fsdecode(raw)

    blob = _blob(repo, "seed", "x")
    commit = _raw_tree_commit(repo, [("100644", "name.txt", blob)])
    monkeypatch.setattr(os, "fsdecode", failing_fsdecode)
    with pytest.raises(store.ReviewError):
        _read_tree(repo, commit)


def test_extract_rejects_dotdot_path(repo: Path, tmp_path: Path) -> None:
    blob = _blob(repo, "seed", "x")
    inner = _hash_tree(repo, b"100644 x\0" + bytes.fromhex(blob))
    commit = _raw_tree_commit(repo, [("40000", "..", inner)])
    destination = tmp_path / "out"
    with pytest.raises(tarfile.OutsideDestinationError):
        extract_tree(repo, commit, destination)
    assert not (tmp_path / "x").exists()
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_write_tree_rejects_absolute_path(tmp_path: Path) -> None:
    # git は空の名前の項目を ls-tree で拒むため、書き出す層へ直接渡して確かめる
    absolute = (tmp_path / "elsewhere.txt").as_posix()
    with pytest.raises(tarfile.OutsideDestinationError):
        _write_tree(tmp_path / "out", [_TreeEntry(0o100644, absolute, b"x")])
    assert not (tmp_path / "elsewhere.txt").exists()


def test_extract_rejects_link_redirected_by_later_link(repo: Path, tmp_path: Path) -> None:
    # `l` を作った時点では m が無く、m/.. は展開先の中に解決する。`m` を作ると外へ向く
    (repo / "l").symlink_to("m/..")
    (repo / "m").symlink_to(".")
    commit = commit_all(repo, "order dependent links")
    destination = tmp_path / "out"
    with pytest.raises(tarfile.LinkOutsideDestinationError):
        extract_tree(repo, _sha(commit), destination)
    # 拒んだ後は、作ったリンクを含め中身を残さず、ディレクトリ自体は残す
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_extract_rejects_redirected_link_when_realpath_is_lexical(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows では os.path.realpath が `m/..` を字句で畳み、リンクをたどった先を見失う。
    # その解決に頼らず、項目の表だけでたどって拒むことを、字句で畳む realpath で確かめる
    (repo / "l").symlink_to("m/..")
    (repo / "m").symlink_to(".")
    commit = commit_all(repo, "order dependent links")
    monkeypatch.setattr(os.path, "realpath", os.path.abspath)
    with pytest.raises(tarfile.LinkOutsideDestinationError):
        extract_tree(repo, _sha(commit), tmp_path / "out")


def test_extract_rounds_regular_modes_to_644_or_755(repo: Path, tmp_path: Path) -> None:
    blob = _blob(repo, "seed", "x")
    commit = _raw_tree_commit(
        repo,
        [("100664", "old.txt", blob), ("100764", "group.sh", blob), ("100775", "old.sh", blob)],
    )
    destination = tmp_path / "out"
    extract_tree(repo, commit, destination)
    assert (destination / "old.txt").read_text(encoding="utf-8") == "x"
    if os.name != "nt":
        assert stat.S_IMODE((destination / "old.txt").stat().st_mode) == 0o644
        assert stat.S_IMODE((destination / "group.sh").stat().st_mode) == 0o755
        assert stat.S_IMODE((destination / "old.sh").stat().st_mode) == 0o755


def test_cleanup_failure_keeps_original_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failing_iterdir(_self: Path) -> list[Path]:
        raise PermissionError("denied")

    destination = tmp_path / "out"
    monkeypatch.setattr(Path, "iterdir", failing_iterdir)
    entry = _TreeEntry(0o100644, "../escaped.txt", b"x")
    with pytest.raises(tarfile.OutsideDestinationError) as raised:
        _write_tree(destination, [entry])
    assert any("後始末" in note for note in raised.value.__notes__)


def test_link_chain_too_long_to_trace_is_treated_as_escaping() -> None:
    chain = {f"l{index}": f"l{index + 1}" for index in range(50)}
    assert pull_request._link_escapes(chain, "l0")
    assert pull_request._link_escapes({"a": "b", "b": "a"}, "a")
    assert not pull_request._link_escapes({"a": "dir/b"}, "a")


def _fetch_count(trace: Path) -> int:
    if not trace.exists():
        # どの git も fetch を起動しなかった
        return 0
    return trace.read_text(encoding="utf-8").count("built-in: git fetch")


def test_extract_from_blobless_clone_fetches_missing_blobs_at_once(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "file0.txt").write_text("old version\n")
    old_blob = git(repo, "hash-object", "file0.txt")
    commit_all(repo, "old version")
    for index in range(20):
        (repo / f"file{index}.txt").write_text(f"content {index}\n")
    commit = commit_all(repo, "many files")
    git(repo, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "clone.git"
    git(tmp_path, "clone", "--bare", "--filter=blob:none", f"file://{repo}", str(clone))
    trace = tmp_path / "trace.log"
    monkeypatch.setenv("GIT_TRACE", str(trace))

    extract_tree(clone, _sha(commit), tmp_path / "first")
    assert _fetch_count(trace) == 1
    assert (tmp_path / "first" / "file19.txt").read_text() == "content 19\n"
    # 起点の木に無い過去の版は取らない
    missing = git(clone, "rev-list", "--objects", "--all", "--missing=print")
    assert f"?{old_blob}" in missing.splitlines()

    # 全部が手元に揃った後は fetch を起動しない
    extract_tree(clone, _sha(commit), tmp_path / "second")
    assert _fetch_count(trace) == 1


def test_prefetch_passes_object_ids_to_fetch_as_bytes(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "a.txt").write_text("a\n")
    commit = commit_all(repo, "one file")
    git(repo, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "clone.git"
    git(tmp_path, "clone", "--bare", "--filter=blob:none", f"file://{repo}", str(clone))
    real = process.run_command_bytes
    fetch_inputs: list[object] = []

    def recording(args: list[str], **kwargs: object) -> bytes:
        if "fetch" in args:
            fetch_inputs.append(kwargs.get("stdin"))
        return real(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(process, "run_command_bytes", recording)
    extract_tree(clone, _sha(commit), tmp_path / "out")
    # 文字列で渡すと Windows で改行が \r\n になり、git が refspec として拒む
    assert len(fetch_inputs) == 1
    assert isinstance(fetch_inputs[0], bytes)
    assert b"\r" not in fetch_inputs[0]
