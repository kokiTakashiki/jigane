"""レビュー対象の設定の log.showSignature で、log が gpg.program を走らせない試験。"""

from pathlib import Path

from pr_support import git

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.revision import parse_commit_sha


def _signing_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    """署名を確かめる設定と、起動されたら印を残す gpg.program を持つリポジトリを作る。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "a.txt").write_bytes(b"a\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "first")
    # 署名の付いたコミットでだけ gpg.program が走るので、偽の署名の見出しを持つコミットを作る
    body = git(repo, "cat-file", "commit", "HEAD")
    header, message = body.split("\n\n", 1)
    signature = "gpgsig -----BEGIN PGP SIGNATURE-----\n x\n -----END PGP SIGNATURE-----"
    commit_file = tmp_path / "signed-commit"
    commit_file.write_text(f"{header}\n{signature}\n\n{message}")
    signed = git(repo, "hash-object", "-t", "commit", "-w", str(commit_file)).strip()
    git(repo, "update-ref", "refs/heads/main", signed)
    marker = tmp_path / "marker"
    program = tmp_path / "gpg"
    program.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    program.chmod(0o755)
    git(repo, "config", "log.showSignature", "true")
    git(repo, "config", "gpg.program", str(program))
    return repo, marker, git(repo, "rev-parse", "HEAD").strip()


def test_log_does_not_run_gpg_program_from_repository_config(tmp_path: Path) -> None:
    repo, marker, head = _signing_repo(tmp_path)
    process.run_command(
        build_git_command(
            GitSubcommand.LOG, options=["--format=%s"], revisions=[parse_commit_sha("h", head)]
        ),
        cwd=repo,
    )
    assert not marker.exists()


def test_plain_git_log_runs_gpg_program_with_this_config(tmp_path: Path) -> None:
    # 入口を通さない log では gpg.program が走る。上の試験が設定を本当に効かせている証拠
    repo, marker, _ = _signing_repo(tmp_path)
    git(repo, "log", "-1", "--format=%s")
    assert marker.exists()
