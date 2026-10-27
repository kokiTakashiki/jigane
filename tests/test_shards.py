import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from jigane.git.tree_files import MAX_REVIEW_FILE_BYTES
from jigane.groups.definitions import MICRO
from jigane.judgment_map.classification import FilePair
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import sharded, shards
from jigane.skills.shards import MAX_SHARDS, SHARD_BYTES, FullTextShard, Unit, assign_shards


def unit(path: str, size: int) -> Unit:
    return Unit(path, "x" * size, size)


def test_no_units_gives_no_shards() -> None:
    assert assign_shards([], FullTextShard) == []


def test_small_total_is_one_shard() -> None:
    result = assign_shards([unit("a", 10), unit("b", 20)], FullTextShard)
    assert len(result) == 1
    assert result[0].size == 30


def test_count_follows_guideline_and_cap() -> None:
    mid = [unit(f"f{i}", SHARD_BYTES // 2) for i in range(5)]
    assert len(assign_shards(mid, FullTextShard)) == 3
    big = [unit(f"f{i}", SHARD_BYTES) for i in range(10)]
    assert len(assign_shards(big, FullTextShard)) == MAX_SHARDS


def test_single_large_file_is_one_shard() -> None:
    result = assign_shards([unit("big", SHARD_BYTES * 10)], FullTextShard)
    assert len(result) == 1


def test_count_never_exceeds_unit_count() -> None:
    assert len(assign_shards([unit("a", SHARD_BYTES * 3), unit("b", 1)], FullTextShard)) == 2


def test_longest_processing_time_first_with_path_tiebreak() -> None:
    units = [unit("z", 15_000), unit("y", 20_000), unit("x", 20_000), unit("w", 5_000)]
    result = assign_shards(units, FullTextShard)
    # 大きい順（同じ大きさはパス順）に、最も軽い（同じ軽さは番号の若い）シャードへ入れる
    assert [s.paths for s in result] == [["x", "z"], ["w", "y"]]


def test_deterministic() -> None:
    units = [unit(f"f{i}", 1000 * (i % 7 + 1)) for i in range(40)]
    first = [s.paths for s in assign_shards(units, FullTextShard)]
    second = [s.paths for s in assign_shards(list(reversed(units)), FullTextShard)]
    assert first == second


def test_feature_units_skip_generated_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "r"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "b.lock").write_text("gen\n", encoding="utf-8")
    (root / "src" / "c.bin").write_bytes(b"\xff\xfe\x00")
    monkeypatch.chdir(root)
    review = create_feature_review(root, ["src"], "p")
    units = shards.build_units(review)
    assert [u.path for u in units] == ["src/a.py"]
    assert units[0].size == len(units[0].text.encode("utf-8"))


def test_secret_file_names_are_withheld_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "r"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / ".env").write_text("TOKEN=hunter2\n", encoding="utf-8")
    (root / "src" / "id_rsa").write_text("hunter2-key\n", encoding="utf-8")
    monkeypatch.chdir(root)
    review = create_feature_review(root, ["src"], "p")
    plan = shards.build_plan(review, MICRO)
    assert [u.path for u in plan.units] == ["src/a.py"]
    assert plan.withheld == ("src/.env", "src/id_rsa")
    sent = "".join(unit.text for shard in plan.shards for unit in shard.units)
    assert "hunter2" not in sent
    notice = sharded.withheld_notice(plan.withheld)
    assert notice is not None
    assert "src/.env" in notice
    assert "src/id_rsa" in notice
    assert sharded.withheld_notice(()) is None


def test_diff_units_withhold_pairs_whose_old_or_new_name_is_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base"
    head = tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / ".env").write_text("TOKEN=hunter2\n", encoding="utf-8")
    (head / "config.txt").write_text("TOKEN=hunter2\n", encoding="utf-8")
    (head / ".env.local").write_text("TOKEN=hunter3\n", encoding="utf-8")
    (base / "a.py").write_text("x = 1\n", encoding="utf-8")
    (head / "a.py").write_text("x = 2\n", encoding="utf-8")
    pairs = [
        FilePair(".env", "config.txt"),
        FilePair(None, ".env.local"),
        FilePair("a.py", "a.py"),
    ]
    monkeypatch.setattr(shards, "load_pairs", lambda _id: pairs)
    review = cast("Review", SimpleNamespace(id="r"))
    withheld: list[str] = []
    units = shards._diff_units(review, head, base, withheld)
    assert [u.path for u in units] == ["a.py"]
    assert withheld == [".env", ".env.local"]
    assert all("hunter" not in u.text for u in units)


def test_sharded_run_notice_reports_withheld_paths() -> None:
    run = sharded.ShardedRun((), cast("Any", None), withheld=(".env",))
    assert run.notice is not None
    assert ".env" in run.notice
    assert sharded.ShardedRun((), cast("Any", None)).notice is None


def test_file_units_skip_files_over_the_size_limit_and_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "r"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "big.py").write_text("#" * (MAX_REVIEW_FILE_BYTES + 1), encoding="utf-8")
    monkeypatch.chdir(root)
    review = create_feature_review(root, ["src"], "p")
    with caplog.at_level(logging.WARNING, logger="jigane.skills.shards"):
        units = shards.build_units(review)
    assert [u.path for u in units] == ["src/a.py"]
    assert any("src/big.py" in message for message in caplog.messages)
