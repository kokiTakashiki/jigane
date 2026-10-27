import json
from typing import Any

import pytest
from fakes import MicroFake, clear_policy, d_finding, settings_for
from pr_support import URL, advance_head

from jigane.commands.subcommands.refresh import refresh_review
from jigane.reviews.lifecycle import ReviewError, create_pull_request_review, load_review
from jigane.skills import micro, shards
from jigane.skills.assembly import AssemblyInput


@pytest.mark.usefixtures("pr")
def test_units_follow_file_pairs_not_diff_text() -> None:
    review = create_pull_request_review(URL)
    units = {unit.path: unit for unit in shards.build_units(review)}
    # 削除した gone.py は入らない。中身が見出しの形の tricky.txt は 1 単位のまま
    assert "gone.py" not in units
    # generated/out.py は変更前の設定の除外に当たる
    assert set(units) == {".jigane.toml", "added.py", "edit.py", "new_name.py", "tricky.txt"}
    tricky = units["tricky.txt"].text
    assert tricky.count("--- ") == 2
    assert "+++ b/tricky.txt" in tricky
    assert "+++ b/x" in tricky
    assert "edit.py" not in tricky


@pytest.mark.usefixtures("pr")
def test_run_micro_on_diff_review() -> None:
    review = create_pull_request_review(URL)
    clear_policy(review)
    fake = MicroFake({1: [d_finding("D1", "edit.py")]})
    result = micro.run_micro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    assert result.failed_shards == 0
    assert result.run_state.target_files == len(shards.build_units(review))


@pytest.mark.usefixtures("pr")
def test_old_review_without_pairs_asks_for_refresh() -> None:
    review = create_pull_request_review(URL)
    (review.id.directory() / "pairs.json").unlink()
    with pytest.raises(ReviewError, match="refresh"):
        shards.build_units(review)


def test_refresh_marks_micro_stale_and_run_clears(pr: dict[str, Any]) -> None:
    review = create_pull_request_review(URL)
    clear_policy(review)
    fake = MicroFake({1: [d_finding("D1", "edit.py")]})
    micro.run_micro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    findings_path = review.id.directory() / "findings.json"
    assert json.loads(findings_path.read_text(encoding="utf-8"))["stale_groups"] == []
    advance_head(pr)
    assert refresh_review(review.id).updated
    assert json.loads(findings_path.read_text(encoding="utf-8"))["stale_groups"] == [
        "macro",
        "micro",
    ]
    refreshed = load_review(review.id)
    clear_policy(refreshed)
    micro.run_micro(AssemblyInput.load(refreshed), settings_for(MicroFake({1: []})))
    assert json.loads(findings_path.read_text(encoding="utf-8"))["stale_groups"] == []
