from dataclasses import replace
from pathlib import Path

from pr_support import commit_all, git
from review_fakes import make_review

from jigane.reviews.lifecycle import LocalRange, RangeOrigin, Review
from jigane.skills import policy_cochange
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_cochange import StructuralDependency
from jigane.skills.policy_figures import REGISTRY, FigureSlot, section_texts
from jigane.storage.store import ReviewKind

COMMITS = 5


def feature_review(root: Path) -> Review:
    return make_review(root, ["a.py"], kind=ReviewKind.FEATURE)


def range_review(tmp_path: Path, working_directory: Path, head_sha: str | None = None) -> Review:
    """repository_root は .git を持たない木、履歴は working_directory にある範囲のレビュー。"""
    tree = tmp_path / "stored-tree"
    tree.mkdir(exist_ok=True)
    (tree / "a.py").write_text("import b\n", encoding="utf-8")
    for name in ("b.py", "c.py", "d.md"):
        (tree / name).write_text("x\n", encoding="utf-8")
    base = tmp_path / "stored-base"
    base.mkdir(exist_ok=True)
    review = make_review(tree, ["a.py"], kind=ReviewKind.RANGE, base_tree=base)
    origin = review.origin
    assert isinstance(origin, RangeOrigin)
    sha = head_sha if head_sha is not None else git(working_directory, "rev-parse", "HEAD").strip()
    review.origin = RangeOrigin(
        LocalRange(origin.range.range_text, str(working_directory)),
        replace(origin.diff, head_sha=sha),
    )
    return review


def history_repo(tmp_path: Path) -> Path:
    """a.py は b.py を import し、c.py と d.md は import しない。四つが 5 回一緒に変わる。"""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    for number in range(COMMITS):
        (root / "a.py").write_text(f"import b\nx = {number}\n", encoding="utf-8")
        for name in ("b.py", "c.py", "d.md"):
            (root / name).write_text(f"v{number}\n", encoding="utf-8")
        commit_all(root, f"change {number}")
    return root


def test_dependency_has_three_values_and_unlisted_first(tmp_path: Path) -> None:
    root = history_repo(tmp_path)
    figure = policy_cochange.cochange_table(range_review(tmp_path, root))
    assert figure is not None
    assert figure.header[-1] == "構造の依存"
    assert [(row.cells[1], row.cells[-1]) for row in figure.rows] == [
        ("c.py", StructuralDependency.ABSENT),
        ("b.py", StructuralDependency.PRESENT),
        ("d.md", StructuralDependency.NOT_CODE),
    ]
    assert figure.rows[0].cells[2:4] == ("5", "1.00")
    assert figure.note is None


def test_rows_over_the_limit_are_only_counted_in_the_note(tmp_path: Path) -> None:
    root = history_repo(tmp_path)
    figure = policy_cochange.cochange_table(range_review(tmp_path, root), max_rows=2)
    assert figure is not None
    assert [row.cells[1] for row in figure.rows] == ["c.py", "b.py"]
    assert figure.note is not None
    assert "1 件" in figure.note


def test_a_review_without_history_omits_the_section(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert policy_cochange.cochange_table(feature_review(plain)) is None
    assert policy_cochange.cochange_table(range_review(tmp_path, plain, "0" * 40)) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-b", "main")
    assert policy_cochange.cochange_table(range_review(tmp_path, empty, "0" * 40)) is None
    assert not any(
        text.startswith(f"### {policy_cochange.SECTION_COCHANGE}")
        for text in section_texts(
            AssemblyInput.load(feature_review(plain)), FigureSlot.AFTER_REGIONS
        )
    )


def test_a_range_review_reads_the_working_directory_history_up_to_head(tmp_path: Path) -> None:
    root = history_repo(tmp_path)
    head = git(root, "rev-parse", "HEAD").strip()
    # 先端より後のコミット。数えられれば回数が 6 になる
    (root / "a.py").write_text("import b\nlater\n", encoding="utf-8")
    (root / "c.py").write_text("later\n", encoding="utf-8")
    commit_all(root, "after head")
    review = range_review(tmp_path, root, head)
    assert not (Path(review.repository_root) / ".git").exists()
    figure = policy_cochange.cochange_table(review)
    assert figure is not None
    assert {row.cells[1]: row.cells[2] for row in figure.rows} == {
        "b.py": "5",
        "c.py": "5",
        "d.md": "5",
    }


def test_the_registry_has_the_cochange_figure() -> None:
    entry = next(e for e in REGISTRY if e.key == "cochange")
    assert entry.title == policy_cochange.SECTION_COCHANGE
