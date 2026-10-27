import ast
import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFake, discovered, run_command_stub, settings_for
from pr_support import BODY, URL, advance_head, commit_all, git
from review_fakes import EXTRA_WARNING

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands.refresh import refresh_review
from jigane.findings.records import triaged_dicts
from jigane.flaky.catalog import Item
from jigane.flaky.gates import update_gates
from jigane.gates.entry import Gates
from jigane.git import process
from jigane.judgment_map import changes
from jigane.judgment_map.changes import render_changes
from jigane.judgment_map.classification import FilePair, Segment
from jigane.judgment_map.configuration import Config
from jigane.judgment_map.declaration import (
    DECLARATION_TEXT_LIMIT,
    EMPTY_BODY_WARNING,
    pull_request_declaration,
)
from jigane.pull_request_context import background
from jigane.pull_request_context.background import PastPullRequest, dump_background
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import (
    FeatureOrigin,
    PullRequestOrigin,
    Review,
    ReviewError,
    create_feature_review,
    create_pull_request_review,
    load_review,
)
from jigane.skills import macro, shards
from jigane.skills.assembly import AssemblyInput
from jigane.storage.store import ReviewId, ReviewKind


def test_create_pull_request_review(pr: dict[str, Any]) -> None:
    review = create_pull_request_review(URL)
    assert review.id == ReviewId("octo", "hello", "7", ReviewKind.PULL_REQUEST)
    assert review.mode == "diff"
    directory = review.id.directory()
    assert (directory / "trees" / "base" / "gone.py").is_file()
    assert (directory / "trees" / "head" / "added.py").is_file()
    assert not (directory / "trees" / "head" / "gone.py").exists()
    assert review.repository_root == str(directory / "trees" / "head")
    assert sorted(review.paths) == [
        ".jigane.toml",
        "added.py",
        "edit.py",
        "generated/out.py",
        "new_name.py",
        "tricky.txt",
    ]
    paths = {region["path"] for region in review.map_kind.to_dict()["regions"]}
    assert {"added.py", "gone.py"} <= paths
    segment_paths = {segment["new_path"] for segment in review.map_kind.to_dict()["segments"]}
    assert "edit.py" in segment_paths
    assert review.declaration.source == "pull_request"
    assert review.declaration.intent == f"支払い\n\n{BODY}"
    assert review.warnings == []
    assert isinstance(review.origin, PullRequestOrigin)
    source = review.origin.diff
    assert source.merge_base == pr["base"]
    assert source.head_sha == pr["head"]
    assert source.base_tree == str(directory / "trees" / "base")

    loaded = load_review(review.id)
    assert loaded.origin == review.origin
    stored = json.loads((directory / "review.json").read_text(encoding="utf-8"))
    assert set(stored["pull_request"]) == {"url", "number", "title"}
    assert stored["local_range"] is None
    assert set(stored["diff_source"]) == {"base_sha", "head_sha", "merge_base", "base_tree"}
    assert loaded.paths == review.paths


@pytest.mark.usefixtures("pr")
def test_config_is_read_from_base_tree() -> None:
    review = create_pull_request_review(URL)
    # head 側の設定は除外を空にするが、変更前の設定の除外が効く
    kinds = {
        (segment["new_path"], segment["kind"]) for segment in review.map_kind.to_dict()["segments"]
    }
    assert ("generated/out.py", "generated") in kinds
    # 除外の効いた箇所は判断の箇所に上がらない
    regions = review.map_kind.to_dict()["regions"]
    assert "generated/out.py" not in {region["path"] for region in regions}


def test_head_config_cannot_change_the_review(pr: dict[str, Any]) -> None:
    origin = pr["origin"]
    # 作者が head の設定で、変更のファイルを除外し、機微の指定を足す
    (origin / ".jigane.toml").write_text(
        'exclude = ["edit.py", "added.py"]\nsensitive = ["edit.py"]\n', encoding="utf-8"
    )
    pr["head"] = commit_all(origin, "relax config")
    review = create_pull_request_review(URL)
    kinds = {
        (segment["new_path"], segment["kind"]) for segment in review.map_kind.to_dict()["segments"]
    }
    # 除外は変更前の設定のものだけが効き、edit.py は対象に残る。機微の指定も効かない
    assert ("edit.py", "logic") in kinds
    assert ("edit.py", "sensitive_logic") not in kinds
    assert {"edit.py", "added.py"} <= {path for path, _ in kinds}
    assert {unit.path for unit in shards.build_units(review)} >= {"edit.py", "added.py"}


@pytest.mark.usefixtures("pr")
def test_existing_review_is_an_error() -> None:
    create_pull_request_review(URL)
    with pytest.raises(ReviewError, match="既にあります"):
        create_pull_request_review(URL)


def test_declaration_truncates_and_warns() -> None:
    long_body = "あ" * (DECLARATION_TEXT_LIMIT + 5)
    declaration = pull_request_declaration("題", long_body)
    assert declaration.intent == "題\n\n" + "あ" * DECLARATION_TEXT_LIMIT
    assert len(declaration.warnings) == 1
    assert "切った" in declaration.warnings[0]
    declaration = pull_request_declaration("題", "  \n")
    assert declaration.intent == "題"
    assert declaration.warnings == (EMPTY_BODY_WARNING,)


def test_cli_new_pr_and_show(pr: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "pr", URL]) == EXIT_OK
    out = capsys.readouterr().out
    assert "octo/hello#7" in out
    assert "PR: 支払い" in out
    assert URL in out
    assert main(["show", "hello#7"]) == EXIT_OK
    text = capsys.readouterr().out
    lines = text.splitlines()
    assert lines[1] == "PR: 支払い"
    assert f"merge-base {pr['base'][:7]}、head {pr['head'][:7]}" in text
    assert text.index("PR: ") < text.index("判断の地図")
    assert main(["show", "hello#7", "overview", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["pull_request"]["number"] == 7
    assert main(["list"]) == EXIT_OK
    assert "octo/hello#7" in capsys.readouterr().out
    assert main(["new", "pr", URL, "--json"]) == EXIT_PROCESSING_FAILURE
    assert "既にあります" in capsys.readouterr().err


@pytest.mark.usefixtures("pr")
def test_cli_new_pr_failures_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "pr", "https://example.com/x"]) == EXIT_PROCESSING_FAILURE
    assert "URL の形" in capsys.readouterr().err

    def failing(args: list[str]) -> str:
        raise process.CommandError(args, "boom")

    process.run_command = run_command_stub(failing)  # monkeypatch fixture が戻す
    assert main(["new", "pr", URL]) == EXIT_PROCESSING_FAILURE
    assert "boom" in capsys.readouterr().err


def test_old_feature_review_json_still_loads(tmp_path: Path) -> None:
    # 差分の出所を持たなかった頃の機能レビューは、review.json に diff_source も pull_request も無い
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "remote", "add", "origin", "https://github.com/octo/feat.git")
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    commit_all(repo, "init")
    review = create_feature_review(repo, ["a.py"], "feat")
    path = review.id.directory() / "review.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["pull_request"] = None
    meta["diff_source"] = None
    path.write_text(json.dumps(meta), encoding="utf-8")
    loaded = load_review(review.id)
    assert loaded.origin == FeatureOrigin(("a.py",))
    assert loaded.kind == ReviewKind.FEATURE


@pytest.mark.usefixtures("pr")
def test_changes_diff_is_saved() -> None:
    review = create_pull_request_review(URL)
    text = (review.id.directory() / "changes.diff").read_text(encoding="utf-8")
    assert (
        "--- a/edit.py\n+++ b/edit.py\n@@ -1,2 +1,2 @@\n def alpha():\n-    return 2\n"
        "+    return 10\n"
    ) in text
    assert "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def beta():\n-    return 2\n" in text
    assert "--- /dev/null\n+++ b/added.py\n@@ -0,0 +1,2 @@\n+def delta():\n" in text
    # 名前だけの変更は見出しだけ
    assert "--- a/old_name.py\n+++ b/new_name.py\n" in text
    # 除外は変更前のツリーの設定で効き、本文を出さない
    assert "--- a/generated/out.py\n+++ b/generated/out.py\n省略（生成物）\n" in text
    assert review.warnings == []


def test_changes_omits_binary_and_over_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (head / "bin.dat").write_bytes(b"\xff\xfe\x00")
    (head / "big.txt").write_text("x\n" * 10, encoding="utf-8")
    (head / "small.txt").write_text("y\n", encoding="utf-8")
    pairs = [
        FilePair(None, "bin.dat"),
        FilePair(None, "big.txt"),
        FilePair(None, "small.txt"),
    ]
    text, omitted = render_changes(base, head, pairs, Config(exclude=()))
    assert "+++ b/bin.dat\n省略（バイナリ）\n" in text
    assert omitted == 0
    monkeypatch.setattr(changes, "CHANGES_CHAR_LIMIT", 60)
    text, omitted = render_changes(base, head, pairs, Config(exclude=()))
    assert omitted == 2
    assert "+++ b/big.txt\n省略（上限）\n" in text
    assert "+++ b/small.txt\n省略（上限）\n" in text
    assert "+x" not in text


def _limit_fixture(tmp_path: Path) -> tuple[Path, Path, list[FilePair]]:
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "gone1.txt").write_text("a\n" * 10, encoding="utf-8")
    (base / "gone2.txt").write_text("b\n" * 10, encoding="utf-8")
    (head / "kept1.txt").write_text("c\n" * 10, encoding="utf-8")
    (head / "kept2.txt").write_text("d\n" * 10, encoding="utf-8")
    pairs = [
        FilePair("gone1.txt", None),
        FilePair("gone2.txt", None),
        FilePair(None, "kept1.txt"),
        FilePair(None, "kept2.txt"),
    ]
    return base, head, pairs


def test_changes_limit_keeps_non_deletions_before_whole_deletions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, head, pairs = _limit_fixture(tmp_path)
    monkeypatch.setattr(changes, "CHANGES_CHAR_LIMIT", 170)
    text, omitted = render_changes(base, head, pairs, Config(exclude=()))
    assert omitted == 2
    assert "+c\n" in text
    assert "+d\n" in text
    assert "-a" not in text
    assert "-b" not in text
    assert "--- a/gone1.txt\n+++ /dev/null\n省略（上限）\n" in text
    assert "--- a/gone2.txt\n+++ /dev/null\n省略（上限）\n" in text


def test_changes_limit_partial_omits_later_deletion_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, head, pairs = _limit_fixture(tmp_path)
    full, _ = render_changes(base, head, pairs, Config(exclude=()))
    one_file = len(full) // 4
    monkeypatch.setattr(changes, "CHANGES_CHAR_LIMIT", one_file * 3)
    text, omitted = render_changes(base, head, pairs, Config(exclude=()))
    assert omitted == 1
    assert "-a\n" in text
    assert "-b" not in text
    assert "+c\n" in text
    assert "+d\n" in text


def test_changes_keeps_pairs_order_and_output_without_limit(tmp_path: Path) -> None:
    base, head, pairs = _limit_fixture(tmp_path)
    text, omitted = render_changes(base, head, pairs, Config(exclude=()))
    assert omitted == 0
    headings = [line for line in text.split("\n") if line.startswith(("--- a/", "--- /dev/null"))]
    assert headings == [
        "--- a/gone1.txt",
        "--- a/gone2.txt",
        "--- /dev/null",
        "--- /dev/null",
    ]
    assert "+++ b/kept1.txt" in text
    assert text.index("kept1.txt") < text.index("kept2.txt")


def region_ids(review: Review) -> dict[str, str]:
    return {r["path"]: r["id"] for r in review.map_kind.to_dict()["regions"]}


@pytest.mark.usefixtures("pr")
def test_refresh_unchanged_writes_nothing() -> None:
    review = create_pull_request_review(URL)
    directory = review.id.directory()
    before = {
        name: (directory / name).read_bytes()
        for name in ("review.json", "map.json", "regions.json", "changes.diff")
    }
    result = refresh_review(review.id)
    assert result.updated is False
    after = {name: (directory / name).read_bytes() for name in before}
    assert after == before
    assert load_review(review.id).refreshed_at is None


@pytest.mark.usefixtures("pr")
def test_refresh_intent_is_rejected_for_a_pull_request() -> None:
    review = create_pull_request_review(URL)
    with pytest.raises(ReviewError, match="PR のタイトルと本文"):
        refresh_review(review.id, intent="x")
    assert load_review(review.id).declaration == review.declaration


@pytest.mark.usefixtures("pr")
def test_refresh_unchanged_sha_rebuilds_stale_map() -> None:
    created = create_pull_request_review(URL)
    map_path = created.id.directory() / "map.json"
    stored = json.loads(map_path.read_text(encoding="utf-8"))
    # 分類の規則が変わる前に保存した地図を真似て、ある種別を古い値へ戻す
    stored["segments"][0]["kind"] = "generated"
    map_path.write_text(json.dumps(stored), encoding="utf-8")
    assert load_review(created.id).map_kind.to_dict()["segments"][0]["kind"] == "generated"

    result = refresh_review(created.id)
    assert result.updated is True
    assert result.review.map_kind.to_dict() == created.map_kind.to_dict()
    assert load_review(created.id).refreshed_at is not None
    assert refresh_review(created.id).updated is False


def test_refresh_after_head_advances(pr: dict[str, Any]) -> None:
    created = create_pull_request_review(URL)
    before = region_ids(created)
    assert {"added.py", "gone.py"} <= set(before)
    directory = created.id.directory()
    (directory / "findings.json").write_text(
        json.dumps({"last_number": 1, "groups": {"macro": []}}), encoding="utf-8"
    )

    advance_head(pr)
    pr["title"] = "支払いを直した"
    result = refresh_review(created.id)
    assert result.updated is True
    review = result.review
    assert review.created_at == created.created_at
    assert review.refreshed_at is not None
    assert isinstance(review.origin, PullRequestOrigin)
    assert review.origin.diff.head_sha == pr["head"]
    assert review.declaration.intent == f"支払いを直した\n\n{BODY}"
    assert (directory / "trees" / "head" / "extra.py").is_file()
    assert not (directory / "trees" / "head" / "added.py").exists()
    assert "+    return 11" in (directory / "changes.diff").read_text(encoding="utf-8")

    after = region_ids(review)
    changes = result.region_changes
    assert changes.added == [after["extra.py"]]
    assert changes.removed == [before["added.py"]]
    assert after["extra.py"] not in before.values()
    # 内容が変わっていない箇所は番号を保つ
    assert after["gone.py"] == before["gone.py"]
    assert load_review(review.id).refreshed_at == review.refreshed_at
    saved = json.loads((directory / "findings.json").read_text(encoding="utf-8"))
    assert saved["stale_groups"] == ["macro"]


def test_cli_refresh(pr: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "pr", URL]) == EXIT_OK
    capsys.readouterr()
    assert main(["refresh", "hello#7"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == "変更なし: octo/hello#7"
    advance_head(pr)
    assert main(["refresh", "hello#7"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "レビューを更新しました: octo/hello#7" in out
    assert "新しい判断の箇所: J" in out
    assert "消えた判断の箇所: J" in out
    assert main(["refresh", "hello#7", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["updated"] is False
    assert data["regions"] == {
        "added": [],
        "changed": [],
        "removed": [],
    }
    assert main(["refresh", "hello#99"]) == EXIT_PROCESSING_FAILURE
    assert "レビューがありません" in capsys.readouterr().err


def test_refresh_title_only_updates_declaration_not_trees(pr: dict[str, Any]) -> None:
    created = create_pull_request_review(URL)
    directory = created.id.directory()
    (directory / "findings.json").write_text(
        json.dumps({"last_number": 1, "groups": {"macro": []}}), encoding="utf-8"
    )
    watched = [
        *(p for p in (directory / "trees").rglob("*") if p.is_file()),
        directory / "changes.diff",
        directory / "map.json",
        directory / "regions.json",
    ]
    before = {p: p.stat().st_mtime_ns for p in watched}
    map_before = (directory / "map.json").read_bytes()
    pr["title"] = "支払いを直した"
    result = refresh_review(created.id)
    assert result.updated is True
    assert result.region_changes.added == []
    review = load_review(created.id)
    assert review.declaration.intent == f"支払いを直した\n\n{BODY}"
    assert isinstance(review.origin, PullRequestOrigin)
    assert review.origin.info.title == "支払いを直した"
    assert review.origin.diff.head_sha == pr["head"]
    assert review.refreshed_at is not None
    assert {p: p.stat().st_mtime_ns for p in watched} == before
    assert (directory / "map.json").read_bytes() == map_before
    saved = json.loads((directory / "findings.json").read_text(encoding="utf-8"))
    assert saved["stale_groups"] == ["macro"]
    # 同じ内容でもう一度取り込んでも、変更なしになる
    assert refresh_review(created.id).updated is False


def test_background_collects_documents(pr: dict[str, Any]) -> None:
    pr["body"] = (
        "設計は docs/design.md と ./docs/design.md を見る。"
        "https://github.com/octo/hello/blob/main/docs/design.md#L3 "
        "https://github.com/other/hello/blob/main/edit.py "
        "docs/missing.md と ../outside.md は無い。edit.py."
    )
    review = create_pull_request_review(URL)
    background = review.background
    assert background is not None
    assert background.documents == ("docs/design.md", "edit.py")
    saved = json.loads((review.id.directory() / "background.json").read_text("utf-8"))
    assert saved == dump_background(background)
    assert load_review(review.id).background == background


def test_background_blob_url_ref_with_slash(pr: dict[str, Any]) -> None:
    pr["body"] = "https://github.com/octo/hello/blob/feature/x/docs/design.md"
    review = create_pull_request_review(URL)
    assert review.background is not None
    assert review.background.documents == ("docs/design.md",)


def test_background_prompt_lists_documents_and_empty_case(pr: dict[str, Any]) -> None:
    review = create_pull_request_review(URL)
    # 所有者の一覧が常に入るので、この review では「背景の材料は無い」にならない
    assert "### 背景\n\n背景の材料は無い" not in macro.build_diagram_prompt(
        AssemblyInput.load(review)
    )
    empty = dataclasses.replace(review, background=background.Background((), (), ()))
    assert "### 背景\n\n背景の材料は無い" in macro.build_diagram_prompt(AssemblyInput.load(empty))
    pr["body"] = "docs/design.md"
    other = create_pull_request_review("https://github.com/octo/hello/pull/8")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(other))
    assert "issue" not in prompt
    assert "- docs/design.md" in prompt
    assert "作業ディレクトリから Read で読む" in prompt


def test_background_feature_review_has_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "remote", "add", "origin", "https://github.com/octo/feat.git")
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    commit_all(repo, "init")
    review = create_feature_review(repo, ["a.py"], "feat")
    assert review.background is None
    assert not (review.id.directory() / "background.json").exists()
    assert "集めない" in macro.build_diagram_prompt(AssemblyInput.load(review))


def test_cli_show_prints_background(
    pr: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    pr["body"] = "docs/design.md"
    assert main(["new", "pr", URL]) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "hello#7"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "背景:\n  参照する文書 docs/design.md" in out
    assert main(["show", "hello#7", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["background"]["documents"] == ["docs/design.md"]
    assert "issues" not in data["background"]


def test_refresh_title_only_recollects_background(pr: dict[str, Any]) -> None:
    created = create_pull_request_review(URL)
    pr["body"] = "docs/design.md を見る"
    result = refresh_review(created.id)
    assert result.updated is True
    review = load_review(created.id)
    assert review.background is not None
    assert review.background.documents == ("docs/design.md",)
    # 同じ内容でもう一度取り込むと、変更なしになる
    assert refresh_review(created.id).updated is False


@pytest.mark.usefixtures("pr")
def test_past_pull_requests_and_owners_in_background() -> None:
    review = create_pull_request_review(URL)
    assert review.background is not None
    assert review.background.past_changes == (PastPullRequest(5, "Tune alpha", ("edit.py",)),)
    owners = {item.path: item.owners for item in review.background.owners}
    # 後の規則が優先する。gone.py は所有者を空にする規則で外れる
    assert owners["edit.py"] == ("@octo/py",)
    assert owners["gone.py"] == ()
    assert owners["new_name.py"] == ("@octo/py",)
    assert owners["generated/out.py"] == ("@octo/py",)
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert "#### 同じファイルに触れた過去の PR\n\n- #5 Tune alpha（edit.py）" in prompt
    assert "- edit.py: @octo/py" in prompt
    assert "- gone.py: 所有者なし" in prompt


@pytest.mark.usefixtures("pr")
def test_cli_show_prints_past_pull_requests_and_owners(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "pr", URL]) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "hello#7"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "  過去の PR #5 Tune alpha（edit.py）" in out
    assert "  所有者 edit.py: @octo/py" in out
    assert "  所有者 gone.py: 所有者なし" in out
    assert main(["show", "hello#7", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["background"]["past_changes"][0]["number"] == 5
    assert {"path": "gone.py", "owners": []} in data["background"]["owners"]


def test_refresh_keeps_past_changes_when_only_declaration_changes(pr: dict[str, Any]) -> None:
    created = create_pull_request_review(URL)
    pr["title"] = "支払い（改）"
    assert refresh_review(created.id).updated is True
    review = load_review(created.id)
    assert review.background is not None
    # 過去の PR と所有者は、ツリーが同じなので引き継ぐ
    assert created.background is not None
    assert review.background.past_changes == created.background.past_changes
    assert review.background.owners == created.background.owners


def history_repo(tmp_path: Path) -> tuple[Path, str]:
    origin = tmp_path / "hist"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    (origin / "a.py").write_text("x = 0\n", encoding="utf-8")
    (origin / "b.py").write_text("y = 0\n", encoding="utf-8")
    (origin / "old.py").write_text("z = 0\n" * 5, encoding="utf-8")
    commit_all(origin, "init")
    # merge commit 方式の PR #1 (a.py)
    git(origin, "checkout", "-b", "feat1")
    (origin / "a.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(origin, "feature commit")
    git(origin, "checkout", "main")
    git(
        origin,
        "merge",
        "--no-ff",
        "feat1",
        "-m",
        "Merge pull request #1 from o/feat1",
        "-m",
        "Add x",
    )
    # squash 方式の PR #2..#8 (a.py)
    for number in range(2, 9):
        (origin / "a.py").write_text(f"x = {number}\n", encoding="utf-8")
        commit_all(origin, f"Bump x ({number == 0 or ''}#{number})".replace("True", ""))
    # 番号の無いコミット
    (origin / "a.py").write_text("x = 99\n", encoding="utf-8")
    commit_all(origin, "direct push")
    # 名前の変更 (PR #9)。旧名の履歴は変更前のパスで辿る
    (origin / "old.py").write_text("z = 1\n" * 5, encoding="utf-8")
    commit_all(origin, "Touch old (#9)")
    git(origin, "mv", "old.py", "new.py")
    commit_all(origin, "Rename (#10)")
    return origin, git(origin, "rev-parse", "HEAD")


def bare_clone(tmp_path: Path, origin: Path) -> Path:
    # objects を hardlink で写すローカルの最適化は、並列の負荷で失敗しうる。pack の転送にする
    cache = tmp_path / "cache.git"
    git(tmp_path, "clone", "--bare", "--no-local", str(origin), str(cache))
    return cache


def test_find_past_pull_requests_merge_squash_limit_and_self(tmp_path: Path) -> None:
    origin, head = history_repo(tmp_path)
    cache = bare_clone(tmp_path, origin)
    pairs = [FilePair("a.py", "a.py")]
    found = background.find_past_pull_requests(cache, head, pairs, own_number=8)
    # 新しい順、自身 (#8) を除き、上限の 5 件
    assert [p.number for p in found] == [7, 6, 5, 4, 3]
    assert found[0].title == "Bump x"
    assert found[0].files == ("a.py",)
    everything = background.find_past_pull_requests(cache, head, pairs, own_number=0)
    assert len(everything) == background.PAST_PULL_REQUEST_LIMIT
    pairs = [FilePair("a.py", "a.py"), FilePair("b.py", "b.py")]
    merged = background.find_past_pull_requests(cache, f"{head}~9", pairs, 0)
    # マージのコミットの題は本文の最初の行から取る
    assert [(p.number, p.title) for p in merged][-1] == (1, "Add x")


def test_find_past_pull_requests_follows_old_path_of_rename(tmp_path: Path) -> None:
    origin, head = history_repo(tmp_path)
    cache = bare_clone(tmp_path, origin)
    # 変更前のパス old.py と変更後のパス new.py の両方の履歴を見る
    pairs = [FilePair("old.py", "new.py")]
    found = background.find_past_pull_requests(cache, head, pairs, own_number=0)
    assert [p.number for p in found] == [10, 9]
    assert found[1].files == ("old.py",)
    # 変更後のパスだけでは、旧名の時代の PR は見えない
    only_new = background.find_past_pull_requests(
        cache, head, [FilePair(None, "new.py")], own_number=0
    )
    assert [p.number for p in only_new] == [10]


def test_find_past_pull_requests_without_paths_or_matches(tmp_path: Path) -> None:
    origin, head = history_repo(tmp_path)
    cache = bare_clone(tmp_path, origin)
    assert background.find_past_pull_requests(cache, head, [], 0) == []
    assert background.find_past_pull_requests(cache, head, [FilePair(None, "no.py")], 0) == []


@pytest.mark.parametrize(
    ("location", "others"),
    [
        (".github/CODEOWNERS", ["CODEOWNERS", "docs/CODEOWNERS"]),
        ("CODEOWNERS", ["docs/CODEOWNERS"]),
        ("docs/CODEOWNERS", []),
    ],
)
def test_codeowners_location_order(tmp_path: Path, location: str, others: list[str]) -> None:
    for name in [location, *others]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        owner = "@first" if name == location else "@later"
        path.write_text(f"* {owner}\n", encoding="utf-8")
    owners = background.find_owners(tmp_path, [FilePair("a.py", "a.py")])
    assert owners == [background.PathOwners("a.py", ("@first",))]


def test_codeowners_last_rule_wins_and_unowned(tmp_path: Path) -> None:
    (tmp_path / "CODEOWNERS").write_text(
        "# コメント\n*.py @a @b\n/src/ @c\n/src/gen/ \n", encoding="utf-8"
    )
    pairs = [
        FilePair("x.py", "x.py"),
        FilePair("src/m.py", "src/m.py"),
        FilePair("src/gen/g.py", "src/gen/g.py"),
        FilePair("README.md", "README.md"),
        FilePair("src/old.py", None),
        FilePair(None, "src/new.txt"),
    ]
    owners = {i.path: i.owners for i in background.find_owners(tmp_path, pairs)}
    assert owners == {
        "x.py": ("@a", "@b"),
        "src/m.py": ("@c",),
        "src/gen/g.py": (),
        "README.md": (),
        "src/old.py": ("@c",),
        "src/new.txt": ("@c",),
    }


def test_no_codeowners_means_no_owners(tmp_path: Path) -> None:
    owners = background.find_owners(tmp_path, [FilePair("a.py", "a.py")])
    assert owners == [background.PathOwners("a.py", ())]


def bot_comment(pr: dict[str, Any], **overrides: object) -> dict[str, Any]:
    comment = {
        "id": 101,
        "user": {"login": "review-bot", "type": "Bot"},
        "body": "戻り値を見直す",
        "path": "edit.py",
        "original_commit_id": pr["base"],
        "original_line": 2,
        "original_start_line": None,
        "in_reply_to_id": None,
        "subject_type": "line",
        "html_url": "https://github.com/octo/hello/pull/7#discussion_r101",
    }
    return comment | overrides


def test_bot_comments_feed_macro_and_map(pr: dict[str, Any]) -> None:
    pr["comment_pages"] = [[bot_comment(pr)]]
    review = create_pull_request_review(URL)
    saved = json.loads((review.id.directory() / "bot_comments.json").read_text(encoding="utf-8"))
    assert "unavailable" not in saved
    assert saved["comments"][0]["touched_lines"] == [2]
    region = next(r for r in review.map_kind.to_dict()["regions"] if r["path"] == "edit.py")
    assert region["kind"] == "bot_response"
    assert region["bot_comment_ids"] == [101]
    assert saved["comments"][0]["region_keys"] == [region["key"]]
    findings = macro.bot_findings(review)
    assert findings is not None
    assert "id 101" in findings
    assert "戻り値を見直す" in findings
    assert review.warnings == []


def test_macro_keeps_m5_with_bot_comments(pr: dict[str, Any]) -> None:
    pr["comment_pages"] = [[bot_comment(pr)]]
    review = create_pull_request_review(URL)
    fake = MacroFake([discovered("M5", "edit.py")])
    result = macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    assert [f["viewpoint"] for f in triaged_dicts(result.findings)] == ["M5"]
    assert result.run_state.dropped_without_bot_findings == 0


@pytest.mark.usefixtures("pr")
def test_macro_drops_m5_without_bot_comments() -> None:
    review = create_pull_request_review(URL)
    assert macro.bot_findings(review) is None
    fake = MacroFake([discovered("M5", "edit.py")])
    result = macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    assert triaged_dicts(result.findings) == []
    assert result.run_state.dropped_without_bot_findings == 1


def test_unavailable_bot_comment_warns(pr: dict[str, Any]) -> None:
    pr["comment_pages"] = [[bot_comment(pr, original_commit_id="1" * 40)]]
    review = create_pull_request_review(URL)
    assert len(review.warnings) == 1
    assert "1 件" in review.warnings[0]


@pytest.mark.usefixtures("pr")
def test_review_does_not_write_approval_and_ignores_a_stored_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    review = create_pull_request_review(URL)
    path = review.id.directory() / "approval.json"
    assert not path.exists()
    # 承認を外す前に作ったレビューが持つ approval.json は、読まず、書き換えもしない
    legacy = '[{"name": "logic", "approvals": 1}]'
    path.write_text(legacy, encoding="utf-8")
    assert load_review(review.id).id == review.id
    assert refresh_review(review.id).updated is False
    assert path.read_text(encoding="utf-8") == legacy
    assert main(["show", "octo/hello#7"]) == EXIT_OK
    assert "承認" not in capsys.readouterr().out
    assert main(["show", "octo/hello#7", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert "approval" not in shown
    assert "approval_status" not in shown


@pytest.mark.usefixtures("pr")
def test_legacy_review_json_is_read_into_the_separated_form() -> None:
    review = create_pull_request_review(URL)
    path = review.id.directory() / "review.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    # 版を持たない古い記録の形にする
    del stored["format"]
    stored["warnings"] = review.warnings
    source = stored.pop("diff_source")
    stored["pull_request"] = {**stored["pull_request"], "linked_issues": [], **source}
    path.write_text(json.dumps(stored), encoding="utf-8")

    loaded = load_review(review.id)

    assert loaded.origin == review.origin


def test_refresh_rebuilds_the_pull_request_body_warnings(pr: dict[str, Any]) -> None:
    pr["body"] = ""
    created = create_pull_request_review(URL)
    assert created.declaration.warnings == (EMPTY_BODY_WARNING,)
    assert EMPTY_BODY_WARNING in created.warnings
    pr["body"] = BODY
    result = refresh_review(created.id)
    assert result.updated is True
    assert result.review.declaration.warnings == ()
    assert EMPTY_BODY_WARNING not in result.review.warnings
    assert EMPTY_BODY_WARNING not in load_review(created.id).warnings


def _unavailable_bot_review(pr: dict[str, Any]) -> Review:
    pr["comment_pages"] = [[bot_comment(pr, original_commit_id="1" * 40)]]
    created = create_pull_request_review(URL)
    assert created.content_warnings
    return created


def test_content_warnings_are_stored_apart_from_the_declaration_warnings(
    pr: dict[str, Any],
) -> None:
    pr["body"] = ""
    created = _unavailable_bot_review(pr)
    stored = json.loads((created.id.directory() / "review.json").read_text(encoding="utf-8"))
    assert stored["content_warnings"] == created.content_warnings
    assert EMPTY_BODY_WARNING not in stored["content_warnings"]
    assert "warnings" not in stored
    assert load_review(created.id).content_warnings == created.content_warnings


def test_refresh_with_the_same_tree_keeps_the_content_warnings(pr: dict[str, Any]) -> None:
    created = _unavailable_bot_review(pr)
    map_path = created.id.directory() / "map.json"
    stored = json.loads(map_path.read_text(encoding="utf-8"))
    stored["segments"][0]["kind"] = "generated"
    map_path.write_text(json.dumps(stored), encoding="utf-8")
    result = refresh_review(created.id)
    assert result.updated is True
    assert result.review.content_warnings == created.content_warnings
    assert load_review(created.id).warnings == created.warnings


def test_refresh_of_the_body_only_keeps_the_content_warnings(pr: dict[str, Any]) -> None:
    pr["body"] = ""
    created = _unavailable_bot_review(pr)
    pr["body"] = BODY
    result = refresh_review(created.id)
    assert result.updated is True
    assert result.review.content_warnings == created.content_warnings
    assert result.review.warnings == created.content_warnings
    assert load_review(created.id).warnings == created.content_warnings


def _picks_up_by_leading_text(source: str) -> bool:
    """`startswith` の引数に LIMIT_WARNING_PREFIX か、その綴りの文字列を渡しているか。"""
    prefix = review_module.LIMIT_WARNING_PREFIX
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
        ):
            continue
        for argument in node.args:
            for part in ast.walk(argument):
                if (isinstance(part, ast.Name) and part.id == "LIMIT_WARNING_PREFIX") or (
                    isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and part.value.startswith(prefix)
                ):
                    return True
    return False


def _subtracts_declaration_warnings(source: str) -> bool:
    """`<式> not in <変数>.declaration.warnings` の形があるか。

    内容の警告は content_warnings として別に持つので、全体の警告から宣言の警告を引いて求めない。
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        for operator, right in zip(node.ops, node.comparators, strict=True):
            if (
                isinstance(operator, ast.NotIn)
                and isinstance(right, ast.Attribute)
                and right.attr == "warnings"
                and isinstance(right.value, ast.Attribute)
                and right.value.attr == "declaration"
            ):
                return True
    return False


def test_picking_warnings_up_by_their_leading_text_is_found() -> None:
    prefix = review_module.LIMIT_WARNING_PREFIX
    assert _picks_up_by_leading_text("w.startswith(LIMIT_WARNING_PREFIX)\n")
    assert _picks_up_by_leading_text("w.startswith((LIMIT_WARNING_PREFIX, 'x'))\n")
    assert _picks_up_by_leading_text(f"w.startswith('{prefix}')\n")
    assert not _picks_up_by_leading_text("w.startswith('other')\nx = LIMIT_WARNING_PREFIX\n")


def test_refresh_does_not_pick_warnings_up_by_their_leading_text() -> None:
    source = Path(review_module.__file__).read_text(encoding="utf-8")
    assert not _picks_up_by_leading_text(source)
    assert not _subtracts_declaration_warnings(source)


def test_subtracting_declaration_warnings_is_found() -> None:
    assert _subtracts_declaration_warnings(
        "[w for w in all if w not in review.declaration.warnings]\n"
    )
    assert _subtracts_declaration_warnings("x not in a.b.declaration.warnings\n")
    assert not _subtracts_declaration_warnings("w in review.declaration.warnings\n")
    assert not _subtracts_declaration_warnings("w not in review.content_warnings\n")
    assert not _subtracts_declaration_warnings("w not in declaration.warnings\n")


@pytest.mark.usefixtures("pr")
def test_refresh_pull_request_saves_when_only_gates_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create_pull_request_review(URL)
    real = update_gates

    def changed(
        directory: Path,
        root: Path,
        paths: list[str],
        segments: Sequence[Segment],
        catalog: list[Item],
        *,
        renames: Mapping[str, str],
        base_root: Path | None = None,
    ) -> Gates:
        gates = real(
            directory, root, paths, segments, catalog, base_root=base_root, renames=renames
        )
        return Gates(gates.violations, (*gates.warnings, EXTRA_WARNING))

    monkeypatch.setattr(review_module, "update_gates", changed)
    result = refresh_review(created.id)
    assert result.updated is True
    assert result.review.gates is not None
    assert EXTRA_WARNING in result.review.gates.warnings


def test_pull_request_refresh_marks_results_stale(
    pr: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    created = create_pull_request_review(URL)
    assert main(
        ["run", str(created.id), "macro"], invoke=MacroFake([discovered(path="added.py")])
    ) == (EXIT_OK)
    # 何も変わっていない refresh は、結果に古い印を付けない
    assert main(["refresh", str(created.id)]) == EXIT_OK
    capsys.readouterr()
    assert main(["show", str(created.id), "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["stale"] is False
    advance_head(pr)
    assert main(["refresh", str(created.id)]) == EXIT_OK
    capsys.readouterr()
    assert main(["show", str(created.id), "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["stale"] is True


@pytest.mark.usefixtures("pr")
def test_cli_refresh_intent_fails_for_a_pull_request(capsys: pytest.CaptureFixture[str]) -> None:
    review = create_pull_request_review(URL)
    exit_code = main(["refresh", str(review.id), "--intent", "x"])
    assert exit_code == EXIT_PROCESSING_FAILURE
    assert "PR のタイトルと本文" in capsys.readouterr().err
