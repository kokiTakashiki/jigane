import sys
import tomllib
from pathlib import Path

import gate_stamp
import pytest
from dev_launch import run_tool

from jigane.git.process import CommandError, run_command

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "gate_stamp.py"
ENTRYPOINTS = ROOT / "entrypoints.toml"
HOOK = ROOT / ".githooks" / "pre-push"
ZEROS = "0" * 40
IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def git(repository: Path, *args: str) -> str:
    return run_command(["git", *IDENTITY, *args], cwd=repository).strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "init")
    return tmp_path


def run(args: list[str], repository: Path, stdin: str | None = None) -> tuple[int, str]:
    """終了コードと、失敗したときの出力を返す。"""
    try:
        output = run_tool(args, cwd=repository, stdin=stdin)
    except CommandError as error:
        return error.returncode or 1, str(error)
    return 0, output


def stamp(repository: Path, *args: str) -> tuple[int, str]:
    return run([sys.executable, str(SCRIPT), *args], repository)


def pre_push(repository: Path, lines: str) -> tuple[int, str]:
    return run(["sh", str(HOOK)], repository, lines)


def push_line(sha: str) -> str:
    return f"refs/heads/main {sha} refs/heads/main {ZEROS}\n"


def test_check_refuses_without_a_stamp(repository: Path) -> None:
    code, output = stamp(repository, "check", git(repository, "rev-parse", "HEAD"))
    assert code == 1
    assert "make check" in output


def test_check_refuses_a_different_sha(repository: Path) -> None:
    assert stamp(repository, "write")[0] == 0
    (repository / "a.txt").write_text("b\n", encoding="utf-8")
    git(repository, "commit", "-am", "second")
    assert stamp(repository, "check", git(repository, "rev-parse", "HEAD"))[0] == 1


def test_write_refuses_a_dirty_tree_and_leaves_no_stamp(repository: Path) -> None:
    (repository / "a.txt").write_text("dirty\n", encoding="utf-8")
    assert stamp(repository, "write")[0] == 1
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    (repository / "new.txt").write_text("untracked\n", encoding="utf-8")
    assert stamp(repository, "write")[0] == 1
    assert not (repository / ".git" / "jigane-gate-passed").exists()


def test_write_ignores_an_ignored_untracked_file(repository: Path) -> None:
    (repository / ".gitignore").write_text("*.log\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "ignore")
    (repository / "x.log").write_text("noise\n", encoding="utf-8")
    assert stamp(repository, "write")[0] == 0


def test_check_passes_for_the_stamped_sha(repository: Path) -> None:
    assert stamp(repository, "write")[0] == 0
    head = git(repository, "rev-parse", "HEAD")
    stamped = (repository / ".git" / "jigane-gate-passed").read_text(encoding="utf-8")
    assert stamped.split() == [head, "check"]
    assert stamp(repository, "check", head)[0] == 0


def test_write_outside_a_work_tree_is_skipped(tmp_path: Path) -> None:
    assert stamp(tmp_path, "write")[0] == 0


def test_install_hook_sets_hooks_path(repository: Path) -> None:
    assert stamp(repository, "install-hook")[0] == 0
    assert git(repository, "config", "core.hooksPath") == ".githooks"


def test_pre_push_refuses_an_unstamped_sha(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    assert pre_push(repository, push_line(head))[0] == 1


def test_pre_push_passes_a_stamped_sha(repository: Path) -> None:
    assert stamp(repository, "write")[0] == 0
    head = git(repository, "rev-parse", "HEAD")
    assert pre_push(repository, push_line(head))[0] == 0


def test_pre_push_refuses_when_any_line_is_unstamped(repository: Path) -> None:
    assert stamp(repository, "write")[0] == 0
    head = git(repository, "rev-parse", "HEAD")
    assert pre_push(repository, push_line(head) + push_line("1" * 40))[0] == 1


def test_pre_push_passes_a_deletion(repository: Path) -> None:
    line = f"(delete) {ZEROS} refs/heads/old {'1' * 40}\n"
    assert pre_push(repository, line)[0] == 0


def test_check_passes_an_annotated_tag_on_a_stamped_commit(repository: Path) -> None:
    assert stamp(repository, "write")[0] == 0
    git(repository, "tag", "-a", "v1", "-m", "release")
    tag_sha = git(repository, "rev-parse", "v1")
    assert tag_sha != git(repository, "rev-parse", "v1^{commit}")
    assert stamp(repository, "check", tag_sha)[0] == 0


def test_check_refuses_an_annotated_tag_on_an_unstamped_commit(repository: Path) -> None:
    assert stamp(repository, "write")[0] == 0
    (repository / "a.txt").write_text("b\n", encoding="utf-8")
    git(repository, "commit", "-am", "second")
    git(repository, "tag", "-a", "v2", "-m", "release")
    tag_sha = git(repository, "rev-parse", "v2")
    assert stamp(repository, "check", tag_sha)[0] == 1


def test_pre_push_passes_a_new_branch_with_a_stamped_sha(repository: Path) -> None:
    assert stamp(repository, "write")[0] == 0
    head = git(repository, "rev-parse", "HEAD")
    line = f"refs/heads/feature {head} refs/heads/feature {ZEROS}\n"
    assert pre_push(repository, line)[0] == 0


def test_pre_push_hook_is_executable_in_the_index() -> None:
    # git は実行の権限が無い hook を黙って無視するので、索引の mode を直接見る
    listing = run_command(["git", "ls-files", "-s", ".githooks/pre-push"], cwd=ROOT)
    assert listing.split()[0] == "100755"


def test_real_push_runs_the_hook_and_refuses_an_unstamped_commit(tmp_path: Path) -> None:
    repository = tmp_path / "work"
    remote = tmp_path / "remote.git"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "init")
    git(tmp_path, "init", "--bare", str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    # gate_stamp.py の呼び出しだけを、この試験の Python に差し替えた hook を置く
    hook_source = HOOK.read_text(encoding="utf-8")
    call = 'uv run --locked --project "$root" python "$root/scripts/gate_stamp.py"'
    assert call in hook_source
    hook_source = hook_source.replace(call, f'"{sys.executable}" "{SCRIPT}"')
    (repository / ".githooks").mkdir()
    hook = repository / ".githooks" / "pre-push"
    hook.write_text(hook_source, encoding="utf-8")
    hook.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add hook")
    assert stamp(repository, "install-hook")[0] == 0
    with pytest.raises(CommandError) as refused:
        run_command(["git", "push", "origin", "main"], cwd=repository)
    assert "make check" in str(refused.value)
    assert stamp(repository, "write")[0] == 0
    run_command(["git", "push", "origin", "main"], cwd=repository)
    assert git(remote, "rev-parse", "main") == git(repository, "rev-parse", "HEAD")


def hooked_repository(tmp_path: Path) -> tuple[Path, Path]:
    """フックを入れた作業ツリーと、その送り先の bare リポジトリを返す。"""
    repository = tmp_path / "work"
    remote = tmp_path / "remote.git"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "init")
    git(tmp_path, "init", "--bare", str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    hook_source = HOOK.read_text(encoding="utf-8")
    call = 'uv run --locked --project "$root" python "$root/scripts/gate_stamp.py"'
    assert call in hook_source
    hook_source = hook_source.replace(call, f'"{sys.executable}" "{SCRIPT}"')
    (repository / ".githooks").mkdir()
    hook = repository / ".githooks" / "pre-push"
    hook.write_text(hook_source, encoding="utf-8")
    hook.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "add hook")
    assert stamp(repository, "install-hook")[0] == 0
    return repository, remote


def commit_b(repository: Path) -> None:
    (repository / "b.txt").write_text("b\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "later")


def test_real_push_refuses_a_stale_stamp(tmp_path: Path) -> None:
    repository, _ = hooked_repository(tmp_path)
    assert stamp(repository, "write")[0] == 0
    commit_b(repository)
    with pytest.raises(CommandError) as refused:
        run_command(["git", "push", "origin", "main"], cwd=repository)
    assert "make check" in str(refused.value)


def test_real_push_refuses_when_a_later_ref_is_unstamped(tmp_path: Path) -> None:
    repository, remote = hooked_repository(tmp_path)
    assert stamp(repository, "write")[0] == 0
    # 先の枝は印と同じ sha、後の枝は印と違う sha。後の行が照らされることを確かめる。
    git(repository, "branch", "a-stamped")
    commit_b(repository)
    git(repository, "branch", "b-unstamped")
    with pytest.raises(CommandError) as refused:
        run_command(["git", "push", "origin", "a-stamped", "b-unstamped"], cwd=repository)
    assert "make check" in str(refused.value)
    assert git(remote, "for-each-ref", "--format=%(refname)") == ""


def test_real_push_allows_a_branch_deletion(tmp_path: Path) -> None:
    repository, remote = hooked_repository(tmp_path)
    assert stamp(repository, "write")[0] == 0
    git(repository, "push", "origin", "main:x")
    git(repository, "push", "origin", "--delete", "x")
    assert git(remote, "for-each-ref", "--format=%(refname)") == ""


def pushed_baseline(tmp_path: Path) -> tuple[Path, Path]:
    """check の印で main を一度送り、リモートに基準の先端がある状態を返す。"""
    repository, remote = hooked_repository(tmp_path)
    assert stamp(repository, "write")[0] == 0
    run_command(["git", "push", "origin", "main"], cwd=repository)
    return repository, remote


def commit_file(repository: Path, name: str) -> None:
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", f"add {name}")


def push_main(repository: Path) -> None:
    run_command(["git", "push", "origin", "main"], cwd=repository)


def test_real_push_of_documents_only_passes_with_a_docs_stamp(tmp_path: Path) -> None:
    repository, remote = pushed_baseline(tmp_path)
    commit_file(repository, "docs/guide.md")
    commit_file(repository, "README.md")
    assert stamp(repository, "write", "--gate", "docs")[0] == 0
    push_main(repository)
    assert git(remote, "rev-parse", "main") == git(repository, "rev-parse", "HEAD")


def test_real_push_of_a_japanese_named_document_passes_with_a_docs_stamp(tmp_path: Path) -> None:
    repository, remote = pushed_baseline(tmp_path)
    commit_file(repository, "docs/メモ.md")
    # 既定の core.quotePath（true）では、この名前は引用符と 8 進の逃がしで出る。その形を前提にする
    assert '"' in git(repository, "diff", "--name-only", "HEAD~1", "HEAD")
    assert stamp(repository, "write", "--gate", "docs")[0] == 0
    push_main(repository)
    assert git(remote, "rev-parse", "main") == git(repository, "rev-parse", "HEAD")


def test_real_push_of_a_japanese_named_code_is_refused_by_a_docs_stamp(tmp_path: Path) -> None:
    repository, _ = pushed_baseline(tmp_path)
    commit_file(repository, "docs/メモ.md")
    commit_file(repository, "pkg/処理.py")
    assert stamp(repository, "write", "--gate", "docs")[0] == 0
    with pytest.raises(CommandError) as refused:
        push_main(repository)
    assert "make check" in str(refused.value)


def test_real_push_with_code_is_refused_by_a_docs_stamp(tmp_path: Path) -> None:
    repository, _ = pushed_baseline(tmp_path)
    commit_file(repository, "docs/guide.md")
    commit_file(repository, "code.py")
    assert stamp(repository, "write", "--gate", "docs")[0] == 0
    with pytest.raises(CommandError) as refused:
        push_main(repository)
    assert "make check" in str(refused.value)


def test_real_push_of_a_markdown_outside_the_document_paths_needs_check(tmp_path: Path) -> None:
    repository, _ = pushed_baseline(tmp_path)
    commit_file(repository, "pkg/notes.md")
    assert stamp(repository, "write", "--gate", "docs")[0] == 0
    with pytest.raises(CommandError):
        push_main(repository)


def test_real_push_of_documents_only_passes_with_a_check_stamp(tmp_path: Path) -> None:
    repository, remote = pushed_baseline(tmp_path)
    commit_file(repository, "docs/guide.md")
    assert stamp(repository, "write")[0] == 0
    push_main(repository)
    assert git(remote, "rev-parse", "main") == git(repository, "rev-parse", "HEAD")


def test_real_push_of_documents_only_without_a_stamp_names_docs_check(tmp_path: Path) -> None:
    repository, _ = pushed_baseline(tmp_path)
    commit_file(repository, "docs/guide.md")
    with pytest.raises(CommandError) as refused:
        push_main(repository)
    assert "make docs-check" in str(refused.value)


def test_check_refuses_an_unknown_gate_kind_in_the_stamp(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    (repository / ".git" / "jigane-gate-passed").write_text(f"{head} bogus\n", encoding="utf-8")
    assert stamp(repository, "check", head)[0] == 1


def test_a_legacy_stamp_with_only_a_sha_counts_as_check(tmp_path: Path) -> None:
    repository, remote = pushed_baseline(tmp_path)
    commit_file(repository, "code.py")
    head = git(repository, "rev-parse", "HEAD")
    (repository / ".git" / "jigane-gate-passed").write_text(head + "\n", encoding="utf-8")
    push_main(repository)
    assert git(remote, "rev-parse", "main") == head


def test_a_new_branch_without_an_origin_default_branch_needs_check(tmp_path: Path) -> None:
    repository, _ = pushed_baseline(tmp_path)
    commit_file(repository, "docs/guide.md")
    assert stamp(repository, "write", "--gate", "docs")[0] == 0
    # 新しい枝では remote sha が 0 で、origin/HEAD も無いので、差分の基準が決まらない
    with pytest.raises(CommandError) as refused:
        run_command(["git", "push", "origin", "main:feature"], cwd=repository)
    # 基準が決められないので、文書だけの変更でも docs ではなく check を求める
    assert "make check" in str(refused.value)
    assert "make docs-check" not in str(refused.value)


def test_a_new_branch_diffs_against_the_origin_default_branch(tmp_path: Path) -> None:
    repository, remote = pushed_baseline(tmp_path)
    git(repository, "remote", "set-head", "origin", "main")
    commit_file(repository, "docs/guide.md")
    assert stamp(repository, "write", "--gate", "docs")[0] == 0
    run_command(["git", "push", "origin", "main:feature"], cwd=repository)
    assert git(remote, "rev-parse", "feature") == git(repository, "rev-parse", "HEAD")


def test_a_rename_out_of_documents_needs_check(tmp_path: Path) -> None:
    repository, _ = pushed_baseline(tmp_path)
    commit_file(repository, "docs/guide.md")
    assert stamp(repository, "write")[0] == 0
    push_main(repository)
    (repository / "pkg").mkdir()
    git(repository, "mv", "docs/guide.md", "pkg/guide.md")
    git(repository, "commit", "-m", "move out of docs")
    assert stamp(repository, "write", "--gate", "docs")[0] == 0
    with pytest.raises(CommandError) as refused:
        push_main(repository)
    assert "make check" in str(refused.value)


@pytest.mark.parametrize(
    ("gate", "task"),
    [(gate_stamp.Gate.CHECK, "check"), (gate_stamp.Gate.DOCS, "docs-check")],
)
def test_gate_commands_name_tasks_defined_in_entrypoints(gate: gate_stamp.Gate, task: str) -> None:
    tasks = tomllib.loads(ENTRYPOINTS.read_text(encoding="utf-8"))["task"]
    assert task in {entry["name"] for entry in tasks}
    guidance = gate_stamp.GATE_COMMANDS[gate]
    assert f"make {task}" in guidance
    assert f"just {task}" in guidance
