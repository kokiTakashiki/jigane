import json
from pathlib import Path

import pytest
from fakes import run_command_stub

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.revision import CommitSha, parse_commit_sha, resolve_commit_sha
from jigane.pull_request_context import gh
from jigane.pull_request_context.fetching import (
    PullRequestMetadata,
    fetch_metadata,
    fetch_pull_request,
)
from jigane.storage.store import ReviewError

SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.mark.parametrize("text", ["a" * 40, "0123456789abcdef" * 4])
def test_parse_commit_sha_accepts_full_length(text: str) -> None:
    assert str(parse_commit_sha("x", text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "a" * 39,
        "a" * 41,
        "A" * 40,
        "--output=x",
        "-" + "a" * 39,
        "g" * 40,
        "HEAD",
        "a" * 40 + "\n",
    ],
)
def test_parse_commit_sha_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="16 進"):
        parse_commit_sha("x", text)


@pytest.mark.parametrize("text", ["", "--output=x", "a" * 40 + "\n"])
def test_commit_sha_constructor_checks_the_form(text: str) -> None:
    with pytest.raises(ValueError, match="16 進"):
        CommitSha(text)


def test_build_fetch_puts_remote_after_end_of_options() -> None:
    sha = parse_commit_sha("x", SHA_A)
    assert build_git_command(GitSubcommand.FETCH, fixed_values=["origin"], revisions=[sha]) == [
        "git",
        "fetch",
        "--end-of-options",
        "origin",
        SHA_A,
    ]


def test_build_each_subcommand_orders_arguments() -> None:
    a = parse_commit_sha("x", SHA_A)
    b = parse_commit_sha("x", SHA_B)
    assert build_git_command(GitSubcommand.DIFF, options=["-z"], revisions=[a, b]) == [
        "git", "diff", "-z", "--end-of-options", SHA_A, SHA_B,
    ]  # fmt: skip
    assert build_git_command(GitSubcommand.MERGE_BASE, revisions=[a, b]) == [
        "git", "merge-base", "--end-of-options", SHA_A, SHA_B,
    ]  # fmt: skip
    assert build_git_command(GitSubcommand.ARCHIVE, options=["-o", "t"], revisions=[a]) == [
        "git", "archive", "-o", "t", "--end-of-options", SHA_A,
    ]  # fmt: skip
    assert build_git_command(GitSubcommand.LS_TREE, revisions=[a]) == [
        "git", "ls-tree", "--end-of-options", SHA_A,
    ]  # fmt: skip


def test_build_rejects_fixed_values_for_other_subcommands() -> None:
    with pytest.raises(ValueError, match="固定の値"):
        build_git_command(GitSubcommand.DIFF, fixed_values=["origin"])


def test_fetch_pull_request_builds_guarded_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        seen.append(args)
        return f"{SHA_A}\n" if args[1] == "merge-base" else ""

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    metadata = PullRequestMetadata(
        "o",
        "r",
        1,
        "u",
        "t",
        "b",
        parse_commit_sha("x", SHA_B),
        parse_commit_sha("x", SHA_A),
        "dev",
    )
    assert str(fetch_pull_request(Path(), metadata)) == SHA_A
    assert seen == [
        ["git", "fetch", "--end-of-options", "origin", SHA_A],
        ["git", "fetch", "--end-of-options", "origin", SHA_B],
        ["git", "merge-base", "--end-of-options", SHA_B, SHA_A],
    ]


@pytest.mark.parametrize("field", ["baseRefOid", "headRefOid"])
def test_fetch_metadata_rejects_malformed_sha(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    data = {
        "number": 5,
        "url": "u",
        "title": "t",
        "body": "b",
        "baseRefOid": SHA_B,
        "headRefOid": SHA_A,
        "author": {"login": "dev"},
    }
    data[field] = "--output=x\n\x1b[31m"
    monkeypatch.setattr(gh, "view_pull_request", lambda *_: json.dumps(data))
    with pytest.raises(ReviewError, match=field) as raised:
        fetch_metadata("o", "r", 5)
    assert "\x1b" not in str(raised.value)
    assert "\n" not in str(raised.value)


def test_fetch_pull_request_rejects_malformed_merge_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process,
        "run_command",
        run_command_stub(lambda args: "--output=x\n" if args[1] == "merge-base" else ""),
    )
    metadata = PullRequestMetadata(
        "o", "r", 1, "u", "t", "b", parse_commit_sha("x", SHA_B), parse_commit_sha("x", SHA_A), "d"
    )
    with pytest.raises(ReviewError, match="merge-base"):
        fetch_pull_request(Path(), metadata)


def test_build_show_and_log_order_arguments() -> None:
    a = parse_commit_sha("x", SHA_A)
    assert build_git_command(
        GitSubcommand.LOG,
        global_options=["-c", "core.quotepath=off"],
        options=["-m"],
        revisions=[a],
        pathspecs=["p.py", "-q"],
    ) == [
        "git", "-c", "core.quotepath=off", "log", "-m",
        "--end-of-options", SHA_A, "--", "p.py", "-q",
    ]  # fmt: skip
    assert build_git_command(GitSubcommand.LOG, revisions=[a], pathspecs=[]) == [
        "git", "log", "--end-of-options", SHA_A, "--",
    ]  # fmt: skip
    assert build_git_command(GitSubcommand.LOG, revisions=[a]) == [
        "git", "log", "--end-of-options", SHA_A,
    ]  # fmt: skip


def test_build_show_joins_checked_revision_and_path() -> None:
    a = parse_commit_sha("x", SHA_A)
    assert build_git_command(GitSubcommand.SHOW, revision_paths=[(a, "--output=x")]) == [
        "git", "show", "--end-of-options", f"{SHA_A}:--output=x",
    ]  # fmt: skip
    with pytest.raises(ValueError, match="16 進"):
        build_git_command(GitSubcommand.SHOW, revision_paths=[(CommitSha("--output=x"), "a.py")])


def test_resolve_commit_sha_checks_the_output_and_guards_the_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        seen.append(args)
        return f"{SHA_A}\n"

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    assert str(resolve_commit_sha(Path(), "--upload-pack=touch /tmp/x")) == SHA_A
    assert seen[0].index("--end-of-options") < seen[0].index("--upload-pack=touch /tmp/x^{commit}")
    monkeypatch.setattr(process, "run_command", run_command_stub(lambda _: "--output=x\n"))
    with pytest.raises(ValueError, match="16 進"):
        resolve_commit_sha(Path(), "HEAD")
