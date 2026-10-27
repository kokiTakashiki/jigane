import json
from pathlib import Path
from typing import Any

import pytest
from fakes import run_command_stub
from pr_support import git
from store_format_support import check_upgrades

from jigane.git import process
from jigane.pull_request_context import bots
from jigane.pull_request_context.bots import _UPGRADES, BOT_COMMENTS_FILE, BOT_COMMENTS_FORMAT
from jigane.storage.review_error import ReviewError

V1_COMMENTS = {
    "comments": [
        {
            "id": 1,
            "user": "bot",
            "body": "text",
            "path": "a.py",
            "original_commit_id": "c",
            "original_line": 3,
            "original_start_line": None,
            "html_url": "https://example.invalid/c/1",
            "judged": True,
            "touched_lines": [1, 4],
            "region_keys": ["a.py:f"],
        }
    ],
    "unavailable": 0,
}


def test_v1_comments_upgrade_to_current_keys() -> None:
    stored = bots.load_bot_comments(V1_COMMENTS)
    dumped = bots.dump_bot_comments(stored.analysis(), {1: ["a.py:f"]})
    assert set(dumped) == {"format", "comments"}
    assert dumped["comments"] == V1_COMMENTS["comments"]
    assert bots.load_bot_comments(dumped) == stored


@pytest.mark.parametrize("version", [0, -1, 4, "2", True, 1.5])
def test_bad_format_version_is_rejected(version: object) -> None:
    with pytest.raises(ReviewError, match="形の版"):
        bots.load_bot_comments({**V1_COMMENTS, "format": version})


def test_corrupt_comments_stop_the_read(tmp_path: Path) -> None:
    assert bots.read_bot_comments(tmp_path) is None
    (tmp_path / bots.BOT_COMMENTS_FILE).write_text("{", encoding="utf-8")
    with pytest.raises(ReviewError):
        bots.read_bot_comments(tmp_path)
    (tmp_path / bots.BOT_COMMENTS_FILE).write_text('{"comments": [{}]}', encoding="utf-8")
    with pytest.raises(ReviewError):
        bots.read_bot_comments(tmp_path)


PAD = "".join(f"# pad{i}\n" for i in range(6))
OLD = "def f():\n    x = 1\n    return x\n\ndef g():\n    return 2\n" + PAD
# 先頭に 1 行足してコメントの行をずらし、f の戻り値を変える
HEAD = "import os\ndef f():\n    x = 1\n    return x + 1\n\ndef g():\n    return 2\n" + PAD


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, Any]:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    (origin / "a.py").write_text(OLD, encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-m", "old")
    old_sha = git(origin, "rev-parse", "HEAD")
    cache = tmp_path / "cache.git"
    git(tmp_path, "clone", "--bare", "--no-local", str(origin), str(cache))
    head_tree = tmp_path / "head"
    head_tree.mkdir()
    (head_tree / "a.py").write_text(HEAD, encoding="utf-8")
    return {"cache": cache, "head": head_tree, "sha": old_sha}


def raw_comment(comment_id: int, **overrides: object) -> dict[str, Any]:
    comment = {
        "id": comment_id,
        "user": {"login": "review-bot", "type": "Bot"},
        "body": "指摘",
        "path": "a.py",
        "original_commit_id": "0" * 40,
        "original_line": 3,
        "original_start_line": None,
        "in_reply_to_id": None,
        "subject_type": "line",
        "html_url": f"https://github.com/o/r/pull/1#discussion_r{comment_id}",
    }
    return comment | overrides


def fake_gh(monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, Any]]]) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(args)
        return json.dumps(pages)

    monkeypatch.setattr(process, "run_command", run_command_stub(fake_run))
    return calls


def fetch(bots_config: tuple[str, ...] = ()) -> list[bots.BotComment]:
    return bots.fetch_bot_comments("o", "r", 1, bots_config)


def comment_for(repo: dict[str, Any], **overrides: object) -> bots.BotComment:
    values = {
        "id": 1,
        "user": "review-bot",
        "body": "指摘",
        "path": "a.py",
        "original_commit_id": repo["sha"],
        "original_line": 3,
        "original_start_line": None,
        "html_url": "u",
    }
    return bots.BotComment(**(values | overrides))


def test_fetch_flattens_pages_and_uses_slurp(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_gh(monkeypatch, [[raw_comment(1)], [raw_comment(2), raw_comment(3)]])
    assert [c.id for c in fetch()] == [1, 2, 3]
    assert calls[0][:6] == ["gh", "api", "--method", "GET", "--paginate", "--slurp"]
    assert calls[0][6] == "repos/o/r/pulls/1/comments"


def test_fetch_selects_bots(monkeypatch: pytest.MonkeyPatch) -> None:
    human = {"login": "human", "type": "User"}
    listed = {"login": "listed-app", "type": "User"}
    fake_gh(
        monkeypatch,
        [
            [
                raw_comment(1),
                raw_comment(2, user=human),
                raw_comment(3, user=listed),
            ]
        ],
    )
    assert [c.id for c in fetch()] == [1]
    assert [c.id for c in fetch(("listed-app",))] == [1, 3]


def test_fetch_drops_replies_file_level_and_no_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_gh(
        monkeypatch,
        [
            [
                raw_comment(1),
                raw_comment(2, in_reply_to_id=1),
                raw_comment(3, subject_type="file", original_line=None),
                raw_comment(4, original_line=None),
                raw_comment(5, subject_type="file"),
            ]
        ],
    )
    assert [c.id for c in fetch()] == [1]


def test_fetch_truncates_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(monkeypatch, [[raw_comment(1, body="あ" * (bots.BODY_CHAR_LIMIT + 10))]])
    assert len(fetch()[0].body) == bots.BODY_CHAR_LIMIT


def test_fetch_keeps_range(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(monkeypatch, [[raw_comment(1, original_start_line=2)]])
    found = fetch()[0]
    assert (found.original_start_line, found.original_line) == (2, 3)


def test_analyze_picks_changed_line_after_shift(repo: dict[str, Any]) -> None:
    # 上に 1 行足されたため、コメントの行 3 の変更は head の行 4 になる。
    # 先頭の挿入（行 1）も窓に入る
    analysis = bots.analyze(repo["cache"], repo["head"], [comment_for(repo)])
    assert analysis.judgments[0].judged
    assert analysis.judgments[0].touched_lines == (1, 4)
    assert [t.lines for t in analysis.touches()] == [frozenset({1, 4})]
    assert analysis.warnings() == []


def test_analyze_ignores_change_not_on_comment_line(repo: dict[str, Any]) -> None:
    # 行 3 の変更は、行 10 のコメントの窓（7..13）に入らない
    comments = [comment_for(repo, id=1, original_line=10)]
    analysis = bots.analyze(repo["cache"], repo["head"], comments)
    assert [j.judged for j in analysis.judgments] == [True]
    assert [j.touched_lines for j in analysis.judgments] == [()]
    assert analysis.touches() == []


def test_analyze_range_comment(repo: dict[str, Any]) -> None:
    analysis = bots.analyze(
        repo["cache"],
        repo["head"],
        [comment_for(repo, original_start_line=2, original_line=3)],
    )
    assert analysis.judgments[0].touched_lines == (1, 4)


def test_analyze_unavailable_commit_and_missing_head_file(repo: dict[str, Any]) -> None:
    comments = [
        comment_for(repo, id=1, original_commit_id="1" * 40),
        comment_for(repo, id=2, path="gone.py"),
        comment_for(repo, id=3),
    ]
    analysis = bots.analyze(repo["cache"], repo["head"], comments)
    assert [j.judged for j in analysis.judgments] == [False, False, True]
    assert analysis.unavailable_count == 2
    assert analysis.warnings() == [
        (
            f"{bots.UNAVAILABLE_WARNING_PREFIX} 2 件は、"
            "付いたコミットまたは head のファイルを取れず、判定から外しました"
        )
    ]


def test_to_dict_carries_region_keys(repo: dict[str, Any]) -> None:
    analysis = bots.analyze(repo["cache"], repo["head"], [comment_for(repo)])
    data = analysis.to_dict({1: ["a.py:f"]})
    assert "unavailable" not in data
    assert data["comments"][0]["region_keys"] == ["a.py:f"]
    assert data["comments"][0]["touched_lines"] == [1, 4]


def insertion_head(repo: dict[str, Any]) -> Path:
    head: Path = repo["head"].parent / "head_insert"
    head.mkdir()
    lines = OLD.splitlines(keepends=True)
    lines.insert(3, "    # added\n")
    (head / "a.py").write_text("".join(lines), encoding="utf-8")
    return head


def test_analyze_picks_insertion_within_context(repo: dict[str, Any]) -> None:
    # 行 3 の後への純粋な挿入は、変更前の範囲が空でも、行 6 のコメントの窓（3..9）に入る
    analysis = bots.analyze(
        repo["cache"], insertion_head(repo), [comment_for(repo, original_line=6)]
    )
    assert analysis.judgments[0].touched_lines == (4,)


def test_analyze_ignores_insertion_beyond_context(repo: dict[str, Any]) -> None:
    analysis = bots.analyze(
        repo["cache"], insertion_head(repo), [comment_for(repo, original_line=7)]
    )
    assert analysis.judgments[0].touched_lines == ()


def test_upgrade_table_has_every_step() -> None:
    check_upgrades(BOT_COMMENTS_FILE, BOT_COMMENTS_FORMAT, _UPGRADES)


def test_v2_comments_drop_the_recountable_unavailable_count() -> None:
    old = {**V1_COMMENTS, "format": 2, "unavailable": 5}
    assert "unavailable" not in bots._UPGRADES[2](old)
    stored = bots.load_bot_comments(old)
    assert stored.unavailable_count == 0
    assert "unavailable" not in bots.dump_bot_comments(stored.analysis())
    # 入力は書き換えない
    assert old["unavailable"] == 5
