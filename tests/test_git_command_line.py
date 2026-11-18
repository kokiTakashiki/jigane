import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from fakes import run_command_stub, without_containment

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.invocation import _PACKAGE_DIRECTORY, git_invocation
from jigane.git.revision import CommitSha, parse_commit_sha, resolve_commit_sha
from jigane.pull_request_context import gh
from jigane.pull_request_context.fetching import (
    PullRequestMetadata,
    fetch_metadata,
    fetch_pull_request,
)
from jigane.storage.store import ReviewError


def plain_build(
    subcommand: GitSubcommand,
    *,
    global_options: Sequence[str] = (),
    options: Sequence[str] = (),
    fixed_values: Sequence[str] = (),
    revisions: Sequence[CommitSha] = (),
    revision_paths: Sequence[tuple[CommitSha, str]] = (),
    pathspecs: Sequence[str] | None = None,
) -> list[str]:
    """build_git_command の結果から、入口が付ける指定を外した argv。"""
    return without_containment(
        build_git_command(
            subcommand,
            global_options=global_options,
            options=options,
            fixed_values=fixed_values,
            revisions=revisions,
            revision_paths=revision_paths,
            pathspecs=pathspecs,
        )
    )


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
    assert plain_build(GitSubcommand.FETCH, fixed_values=["origin"], revisions=[sha]) == [
        "git",
        "fetch",
        "--upload-pack=git-upload-pack",
        "--end-of-options",
        "origin",
        SHA_A,
    ]


def test_build_each_subcommand_orders_arguments() -> None:
    a = parse_commit_sha("x", SHA_A)
    b = parse_commit_sha("x", SHA_B)
    assert plain_build(GitSubcommand.DIFF, options=["-z"], revisions=[a, b]) == [
        "git", "diff", "--no-ext-diff", "--no-textconv", "-z", "--end-of-options", SHA_A, SHA_B,
    ]  # fmt: skip
    assert plain_build(GitSubcommand.MERGE_BASE, revisions=[a, b]) == [
        "git", "merge-base", "--end-of-options", SHA_A, SHA_B,
    ]  # fmt: skip
    assert plain_build(GitSubcommand.CAT_FILE, options=["--batch"]) == [
        "git", "cat-file", "--batch", "--end-of-options",
    ]  # fmt: skip
    assert plain_build(GitSubcommand.LS_TREE, revisions=[a]) == [
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
        ["git", "fetch", "--upload-pack=git-upload-pack", "--end-of-options", "origin", SHA_A],
        ["git", "fetch", "--upload-pack=git-upload-pack", "--end-of-options", "origin", SHA_B],
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
    assert plain_build(
        GitSubcommand.LOG,
        global_options=["-c", "core.quotepath=off"],
        options=["-m"],
        revisions=[a],
        pathspecs=["p.py", "-q"],
    ) == [
        "git", "-c", "core.quotepath=off", "log", "--no-ext-diff", "--no-textconv", "-m",
        "--end-of-options", SHA_A, "--", "p.py", "-q",
    ]  # fmt: skip
    assert plain_build(GitSubcommand.LOG, revisions=[a], pathspecs=[]) == [
        "git", "log", "--no-ext-diff", "--no-textconv", "--end-of-options", SHA_A, "--",
    ]  # fmt: skip
    assert plain_build(GitSubcommand.LOG, revisions=[a]) == [
        "git", "log", "--no-ext-diff", "--no-textconv", "--end-of-options", SHA_A,
    ]  # fmt: skip


def test_build_show_joins_checked_revision_and_path() -> None:
    a = parse_commit_sha("x", SHA_A)
    assert plain_build(GitSubcommand.SHOW, revision_paths=[(a, "--output=x")]) == [
        "git", "show", "--no-ext-diff", "--no-textconv", "--end-of-options", f"{SHA_A}:--output=x",
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


def test_every_git_invocation_carries_the_containment_options() -> None:
    commands = [
        build_git_command(GitSubcommand.LS_TREE, revisions=[parse_commit_sha("x", SHA_A)]),
        git_invocation("rev-parse", ["HEAD"]).argv,
        git_invocation("--version").argv,
    ]
    for command in commands:
        assert command[0] == "git"
        assert command[1:8] == [
            "-c",
            "core.fsmonitor=false",
            "-c",
            command[4],
            "-c",
            "log.showSignature=false",
            "--no-pager",
        ]
        assert command[4].startswith("core.hooksPath=")
        hooks = Path(command[4].removeprefix("core.hooksPath="))
        assert hooks.is_absolute()
        assert hooks.parent == _PACKAGE_DIRECTORY
        assert not hooks.exists()

    def hooks_option(argv: Sequence[str]) -> str:
        return next(part for part in argv if part.startswith("core.hooksPath="))

    first = hooks_option(git_invocation("rev-parse").argv)
    assert first != hooks_option(git_invocation("rev-parse").argv)
    assert not list(_PACKAGE_DIRECTORY.glob("no-hooks-*"))
    environment = git_invocation("rev-parse").environment
    assert environment is not None
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_ALLOW_PROTOCOL"] == "file:https:ssh"


IDENTITY = ["-c", "user.name=a", "-c", "user.email=a@example.com"]


def _repository_with_marking_script(tmp_path: Path, config_key: str, script_path: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    process.run_command(["git", "init", "-q", str(repo)])
    if config_key:
        process.run_command(["git", "config", config_key, script_path], cwd=repo)
    return repo


def _marking_script(tmp_path: Path) -> tuple[Path, Path]:
    mark = tmp_path / "mark"
    script = tmp_path / "script.sh"
    script.write_text(f"#!/bin/sh\necho x > '{mark.as_posix()}'\n")
    script.chmod(0o755)
    return script, mark


def test_fsmonitor_in_repository_config_is_not_launched(tmp_path: Path) -> None:
    script, mark = _marking_script(tmp_path)
    repo = _repository_with_marking_script(tmp_path, "core.fsmonitor", script.as_posix())
    (repo / "a.txt").write_text("x")
    # 対照: 指定の無い git では、同じ設定で台本が起動する
    process.run_command(["git", "ls-files", "--others"], cwd=repo)
    assert mark.exists()
    mark.unlink()
    process.run_command(git_invocation("ls-files", ["--others"]).argv, cwd=repo)
    assert not mark.exists()


def test_hooks_in_repository_are_not_launched(tmp_path: Path) -> None:
    script, mark = _marking_script(tmp_path)
    repo = _repository_with_marking_script(tmp_path, "", "")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(script.read_text())
    hook.chmod(0o755)
    # 対照: 指定の無い git では、同じ hook が起動する
    process.run_command(["git", *IDENTITY, "commit", "--allow-empty", "-m", "x"], cwd=repo)
    assert mark.exists()
    mark.unlink()
    process.run_command(
        git_invocation("commit", ["--allow-empty", "-m", "y"], global_options=IDENTITY).argv,
        cwd=repo,
    )
    assert not mark.exists()


def test_caller_cannot_override_the_containment_options() -> None:
    argv = git_invocation(
        "rev-parse", global_options=["-c", "core.fsmonitor=true", "-c", "core.hooksPath=/x"]
    ).argv
    assert argv.index("core.fsmonitor=true") < argv.index("core.fsmonitor=false")
    hooks = [item for item in argv if item.startswith("core.hooksPath=")]
    assert hooks[0] == "core.hooksPath=/x"
    assert hooks[-1] != "core.hooksPath=/x"
    assert argv[-1] == "rev-parse"
    assert argv[-2] == "--no-pager"


def test_fetch_ignores_remote_uploadpack_in_repository_config(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    process.run_command(["git", "init", "-q", str(source)])
    process.run_command(["git", *IDENTITY, "commit", "--allow-empty", "-m", "x"], cwd=source)
    sha = parse_commit_sha(
        "head", process.run_command(["git", "rev-parse", "HEAD"], cwd=source).strip()
    )
    mark = tmp_path / "mark"
    script = tmp_path / "uploadpack.sh"
    script.write_text(f"#!/bin/sh\necho x > '{mark.as_posix()}'\nexec git-upload-pack \"$@\"\n")
    script.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    process.run_command(["git", "init", "-q", str(work)])
    config = [
        ("remote.origin.url", source.as_posix()),
        ("remote.origin.uploadpack", script.as_posix()),
    ]
    # credential.helper はローカルパスへの fetch では使われず、確かめられないので置かない
    for key, value in config:
        process.run_command(["git", "config", key, value], cwd=work)
    # 対照: 指定の無い git では、設定の台本が起動する
    process.run_command(["git", "fetch", "origin"], cwd=work)
    assert mark.exists()
    mark.unlink()
    command = build_git_command(GitSubcommand.FETCH, fixed_values=["origin"], revisions=[sha])
    process.run_command(command, cwd=work)
    assert not mark.exists()


def test_diff_and_show_ignore_external_diff_and_textconv_in_repository_config(
    tmp_path: Path,
) -> None:
    script, mark = _marking_script(tmp_path)
    repo = _repository_with_marking_script(tmp_path, "diff.external", script.as_posix())
    process.run_command(["git", "config", "diff.marker.textconv", script.as_posix()], cwd=repo)
    (repo / ".gitattributes").write_text("*.txt diff=marker\n")
    shas: list[CommitSha] = []
    for text in ("one\n", "two\n"):
        (repo / "a.txt").write_text(text)
        process.run_command(["git", "add", "."], cwd=repo)
        process.run_command(["git", *IDENTITY, "commit", "-q", "-m", text.strip()], cwd=repo)
        head = process.run_command(["git", "rev-parse", "HEAD"], cwd=repo).strip()
        shas.append(parse_commit_sha("head", head))
    # 対照: 指定の無い git では、diff と show のどちらも台本を起動する
    for plain in (["diff", "HEAD~1", "HEAD"], ["show", "HEAD"]):
        process.run_command(["git", *plain], cwd=repo)
        assert mark.exists()
        mark.unlink()
    process.run_command(
        build_git_command(GitSubcommand.DIFF, options=["-p"], revisions=shas), cwd=repo
    )
    assert not mark.exists()
    process.run_command(
        build_git_command(GitSubcommand.SHOW, options=["-p"], revisions=[shas[1]]), cwd=repo
    )
    assert not mark.exists()


def test_build_check_attr_puts_source_before_end_of_options_and_attribute_after() -> None:
    sha = parse_commit_sha("x", SHA_A)
    assert plain_build(
        GitSubcommand.CHECK_ATTR,
        options=["--source", str(sha), "-z", "--stdin"],
        fixed_values=["linguist-generated"],
    ) == [
        "git", "check-attr", "--source", SHA_A, "-z", "--stdin",
        "--end-of-options", "linguist-generated",
    ]  # fmt: skip


def test_check_attr_carries_the_containment_options() -> None:
    command = build_git_command(GitSubcommand.CHECK_ATTR, fixed_values=["linguist-generated"])
    assert command[1:4] == ["-c", "core.fsmonitor=false", "-c"]
    assert command[4].startswith("core.hooksPath=")
    assert "--no-pager" in command


def _check_attr(repo: Path, source: str, paths: list[str]) -> str:
    command = build_git_command(
        GitSubcommand.CHECK_ATTR,
        options=["--source", source, "-z", "--stdin"],
        fixed_values=["linguist-generated"],
    )
    return process.run_command(command, cwd=repo, stdin="".join(f"{p}\0" for p in paths))


def test_check_attr_reads_the_attributes_of_the_given_commit_not_the_working_tree(
    tmp_path: Path,
) -> None:
    repo = _repository_with_marking_script(tmp_path, "", "")
    (repo / ".gitattributes").write_text(
        "Makefile linguist-generated\n"
        "justfile linguist-generated=false\n"
        "-dash linguist-generated\n"
    )
    process.run_command(["git", "add", ".gitattributes"], cwd=repo)
    process.run_command(["git", *IDENTITY, "commit", "-q", "-m", "x"], cwd=repo)
    head = process.run_command(["git", "rev-parse", "HEAD"], cwd=repo).strip()
    # 作業木から消しても、コミットの木の印が返る
    (repo / ".gitattributes").unlink()
    output = _check_attr(repo, head, ["Makefile", "justfile", "-dash", "other"])
    assert output.split("\0")[:-1] == [
        "Makefile", "linguist-generated", "set",
        "justfile", "linguist-generated", "false",
        "-dash", "linguist-generated", "set",
        "other", "linguist-generated", "unspecified",
    ]  # fmt: skip


def test_check_attr_fails_for_a_source_git_cannot_resolve(tmp_path: Path) -> None:
    repo = _repository_with_marking_script(tmp_path, "", "")
    with pytest.raises(process.CommandError):
        _check_attr(repo, "nosuchref", ["Makefile"])


def test_check_attr_with_an_absent_full_length_sha_reports_unspecified(tmp_path: Path) -> None:
    repo = _repository_with_marking_script(tmp_path, "", "")
    # git 2.54.0 の実測: 40 桁の 16 進は、リポジトリに無いオブジェクトでも失敗せず
    # 全パスを unspecified で返す。失敗の検出に頼れるのは、解釈できない名前のときだけ
    output = _check_attr(repo, SHA_A, ["Makefile"])
    assert output.split("\0")[:-1] == ["Makefile", "linguist-generated", "unspecified"]
