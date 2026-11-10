from pathlib import Path

import pytest
from fakes import run_command_stub
from pr_support import commit_all, git

from jigane.git import process
from jigane.policy_materials import cochange


def write(repo: Path, *names: str, text: str = "x\n") -> None:
    for name in names:
        (repo / name).write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    return root


def find(rows: list[cochange.CoChange], origin: str, partner: str) -> cochange.CoChange:
    return next(row for row in rows if (row.origin, row.partner) == (origin, partner))


def test_strength_is_shared_over_mean_revisions(repo: Path) -> None:
    write(repo, "a.py", "b.py")
    commit_all(repo, "one")
    write(repo, "a.py", "b.py", text="y\n")
    commit_all(repo, "two")
    write(repo, "a.py", text="z\n")
    commit_all(repo, "three")
    rows = cochange.count_co_changes(repo, frozenset({"a.py"}))
    row = find(rows, "a.py", "b.py")
    assert row.shared_commits == 2
    # a は 3 回、b は 2 回変わった。2 / ((3 + 2) / 2)
    assert row.strength == pytest.approx(0.8)
    assert [r.origin for r in rows] == ["a.py"]


def test_merge_commits_are_not_counted(repo: Path) -> None:
    write(repo, "a.py")
    commit_all(repo, "base")
    git(repo, "switch", "-c", "side")
    write(repo, "b.py")
    commit_all(repo, "side")
    git(repo, "switch", "main")
    write(repo, "a.py", text="y\n")
    commit_all(repo, "main")
    # 衝突の解消で a と b を同時に変えたマージのコミット
    git(repo, "merge", "--no-ff", "-m", "merge", "side")
    write(repo, "a.py", "b.py", text="m\n")
    git(repo, "add", "-A")
    git(repo, "commit", "--amend", "--no-edit")
    assert cochange.count_co_changes(repo, frozenset({"a.py"})) == []


def test_large_commits_are_not_counted(repo: Path) -> None:
    names = [f"f{index}.py" for index in range(4)]
    write(repo, *names)
    commit_all(repo, "bulk")
    write(repo, "f0.py", "f1.py", text="y\n")
    commit_all(repo, "pair")
    rows = cochange.count_co_changes(repo, frozenset({"f0.py"}), max_files_per_commit=3)
    assert [(r.partner, r.shared_commits) for r in rows] == [("f1.py", 1)]
    assert rows[0].strength == pytest.approx(1.0)


def test_renames_are_followed(repo: Path) -> None:
    write(repo, "old.py", "peer.py", text="line one\nline two\nline three\n")
    commit_all(repo, "first")
    write(repo, "old.py", "peer.py", text="line one\nline two\nline three\nfour\n")
    commit_all(repo, "second")
    git(repo, "mv", "old.py", "new.py")
    commit_all(repo, "move")
    write(repo, "new.py", "peer.py", text="line one\nline two\nline three\nfive\n")
    commit_all(repo, "third")
    rows = cochange.count_co_changes(repo, frozenset({"new.py"}))
    # 移動の前のパスの 2 回を含め、new.py と peer.py は 3 回一緒に変わった
    row = find(rows, "new.py", "peer.py")
    assert row.shared_commits == 3
    assert all(r.partner != "old.py" for r in rows)


def row(shared: int, strength: float, origin_revs: int, partner_revs: int) -> cochange.CoChange:
    return cochange.CoChange("a", "b", shared, strength, origin_revs, partner_revs)


def test_select_strong_drops_pairs_below_each_floor() -> None:
    kept = row(5, 0.9, 6, 6)
    rows = [
        kept,
        row(5, 0.9, 4, 6),  # 起点の変更回数が下限未満
        row(5, 0.9, 6, 4),  # 相手の変更回数が下限未満
        row(4, 0.9, 6, 6),  # 共変更の回数が下限未満
        row(5, 0.2, 6, 6),  # 強さが下限未満
    ]
    assert cochange.select_strong(rows) == [kept]


def test_select_strong_caps_rows() -> None:
    rows = [row(5, 0.9, 6, 6), row(6, 0.8, 7, 7)]
    assert cochange.select_strong(rows, max_rows=1) == rows[:1]


def test_revision_is_resolved_then_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    sha = "c" * 40

    def fake_run(args: list[str]) -> str:
        seen.append(args)
        return f"{sha}\n" if args[1] == "rev-parse" else ""

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    evil = "--upload-pack=touch /tmp/x"
    cochange.count_co_changes(Path(), frozenset({"a.py"}), revision=evil)
    rev_parse, log = seen
    assert rev_parse.index("--end-of-options") < rev_parse.index(f"{evil}^{{commit}}")
    assert evil not in log
    assert log[-3:] == ["--end-of-options", sha, "--"]
