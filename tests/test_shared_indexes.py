from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_policy_classes import feature_review
from test_policy_discriminators import SOURCE

from jigane.languages.base import SequenceAdapter
from jigane.languages.python import PythonAdapter
from jigane.sequence import index as index_module
from jigane.sequence.index import Index, build_index, shared_indexes
from jigane.skills.policy_figures import diagrams_of, placed_figures


@pytest.fixture
def build_counts(monkeypatch: pytest.MonkeyPatch) -> Counter[tuple[Path, str]]:
    """実際に索引を作った回数を、木の根と言語名ごとに数える。"""
    counts: Counter[tuple[Path, str]] = Counter()
    real = index_module._build_index

    def counting(root: Path, paths: Sequence[str], adapter: SequenceAdapter) -> Index:
        counts[(root, adapter.name)] += 1
        return real(root, paths, adapter)

    monkeypatch.setattr(index_module, "_build_index", counting)
    return counts


def test_build_index_reuses_the_index_inside_shared_indexes(
    tmp_path: Path, build_counts: Counter[tuple[Path, str]]
) -> None:
    (tmp_path / "a.py").write_text("def f() -> None: ...\n")
    adapter = PythonAdapter()
    with shared_indexes():
        first = build_index(tmp_path, ["a.py"], adapter)
        second = build_index(tmp_path, ["a.py"], adapter)
    assert first is second
    assert build_counts[(tmp_path, "python")] == 1


def test_build_index_builds_each_time_outside_shared_indexes(
    tmp_path: Path, build_counts: Counter[tuple[Path, str]]
) -> None:
    (tmp_path / "a.py").write_text("def f() -> None: ...\n")
    adapter = PythonAdapter()
    assert build_index(tmp_path, ["a.py"], adapter) is not build_index(tmp_path, ["a.py"], adapter)
    assert build_counts[(tmp_path, "python")] == 2


def test_build_index_keeps_indexes_apart_for_different_paths(
    tmp_path: Path, build_counts: Counter[tuple[Path, str]]
) -> None:
    (tmp_path / "a.py").write_text("def f() -> None: ...\n")
    (tmp_path / "b.py").write_text("def g() -> None: ...\n")
    adapter = PythonAdapter()
    with shared_indexes():
        only_a = build_index(tmp_path, ["a.py"], adapter)
        both = build_index(tmp_path, ["a.py", "b.py"], adapter)
    assert only_a is not both
    assert build_counts[(tmp_path, "python")] == 2


def test_diagrams_of_builds_the_tree_index_once_per_language(
    tmp_path: Path, build_counts: Counter[tuple[Path, str]]
) -> None:
    review = feature_review(tmp_path, "m.py", SOURCE)
    # 共有の範囲の外で図を作ると、同じ木と言語の索引を2回以上作る入力であることを確かめる
    placed_figures(review)
    assert max(build_counts.values()) >= 2
    build_counts.clear()
    diagrams_of(review)
    assert build_counts
    assert set(build_counts.values()) == {1}
