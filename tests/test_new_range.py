import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from fakes import MacroFake, discovered
from gates_support import write_gates_file
from pr_support import commit_all, git, remove_tree
from review_fakes import EXTRA_WARNING

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands.refresh import refresh_review
from jigane.flaky.catalog import Item
from jigane.flaky.gates import update_gates
from jigane.gates.entry import Gates
from jigane.git import process
from jigane.judgment_map.classification import Segment
from jigane.judgment_map.declaration import (
    DECLARATION_TEXT_LIMIT,
    EMPTY_COMMITS_WARNING,
    commits_declaration,
)
from jigane.pull_request_context.background import parse_background
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import (
    DeclaredInputs,
    DiffSource,
    FeatureOrigin,
    LocalRange,
    PullRequestInfo,
    PullRequestOrigin,
    RangeOrigin,
    Review,
    ReviewError,
    create_range_review,
    list_reviews,
    load_pairs,
    load_review,
    resolve_range,
    resolve_review,
)
from jigane.skills.macro import background_prompt_text
from jigane.storage.store import ReviewKind


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    return {**repo_template, "root": str(root)}


def test_range_review_is_saved(repo: dict[str, str], tmp_path: Path) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    assert str(review.id) == "octo/hello@mine"
    assert review.kind == ReviewKind.RANGE
    assert review.id.directory() == tmp_path / "home" / "octo" / "hello" / "@mine"
    assert isinstance(review.origin, RangeOrigin)
    source = review.origin.diff
    assert review.origin.range.range_text == "main..feature"
    assert source.merge_base == repo["merge_base"]
    assert source.head_sha == repo["head"]
    assert source.base_sha == git(Path(repo["root"]), "rev-parse", "main")
    assert (review.id.directory() / "trees/base/gone.py").is_file()
    assert not (review.id.directory() / "trees/base/other.py").exists()
    assert (review.id.directory() / "trees/head/new.py").is_file()
    pairs = json.loads((review.id.directory() / "pairs.json").read_text(encoding="utf-8"))["pairs"]
    assert sorted(pairs, key=str) == sorted(
        [["edit.py", "edit.py"], ["gone.py", None], [None, "new.py"]], key=str
    )
    assert review.declaration.source == "commits"
    assert review.declaration.intent == "change alpha\n\nbecause it is wrong\n\nswap files"
    assert review.background is not None
    assert [item.owners for item in review.background.owners] == [("@octo",)] * 3


def test_range_review_over_an_empty_base_tree(tmp_path: Path) -> None:
    root = tmp_path / "empty-base"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/octo/hello.git")
    git(root, "commit", "--allow-empty", "-m", "empty first commit")
    (root / "added.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    commit_all(root, "add a file")

    review = create_range_review(root, "main~1..main", "mine")

    base_tree = review.id.directory() / "trees/base"
    assert base_tree.is_dir()
    assert list(base_tree.iterdir()) == []
    assert (review.id.directory() / "trees/head/added.py").is_file()


def test_range_review_intent_and_default_name(repo: dict[str, str]) -> None:
    review = create_range_review(
        Path(repo["root"]), "main..feature", inputs=DeclaredInputs(intent="alpha を直す")
    )
    assert str(review.id) == "octo/hello@feature"
    assert (review.declaration.source, review.declaration.intent) == ("intent", "alpha を直す")
    head_review = create_range_review(
        Path(repo["root"]), "main..", inputs=DeclaredInputs(intent="x")
    )
    assert head_review.id.key == repo["head"][:8]


def test_range_review_does_not_write_working_directory(repo: dict[str, str]) -> None:
    before = git(Path(repo["root"]), "status", "--porcelain", "--ignored")
    create_range_review(Path(repo["root"]), "main..feature", "mine")
    assert git(Path(repo["root"]), "status", "--porcelain", "--ignored") == before


def test_range_review_over_existing_points_to_refresh(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    create_range_review(root, "main..feature", "mine")
    with pytest.raises(ReviewError, match=r"作り直すには refresh してください.*--name"):
        create_range_review(root, "main..feature", "mine")


def test_range_review_accepts_a_digits_only_name(repo: dict[str, str]) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "7")
    assert review.id.key == "7"


def test_range_review_rejects_both_intent_options(repo: dict[str, str]) -> None:
    with pytest.raises(ReviewError, match="同時に指定できません"):
        create_range_review(
            Path(repo["root"]),
            "main..feature",
            "x",
            DeclaredInputs(intent="a", intent_file=Path("b")),
        )


def test_unresolvable_range_fails_with_its_reason(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    with pytest.raises(ReviewError, match=r"refs を解けません: nope"):
        resolve_range(root, "main..nope")
    with pytest.raises(ReviewError, match=r"<base>\.\.<head> の形"):
        create_range_review(root, "main", "x")
    assert list_reviews() == []


@pytest.mark.parametrize("range_text", ["main..nope", "feature..feature", "main..unrelated"])
def test_stopped_range_creates_no_directory(
    repo: dict[str, str], tmp_path: Path, range_text: str
) -> None:
    root = Path(repo["root"])
    # 共通の祖先が無い範囲のために、無関係な履歴の根を作る
    git(root, "checkout", "--orphan", "unrelated")
    (root / "solo.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(root, "unrelated root")
    git(root, "switch", "feature")
    with pytest.raises(ReviewError):
        create_range_review(root, range_text, "mine")
    assert not (tmp_path / "home" / "octo" / "hello" / "@mine").exists()


def test_range_without_common_ancestor_fails_with_its_reason(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    git(root, "checkout", "--orphan", "unrelated")
    (root / "solo.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(root, "unrelated root")
    with pytest.raises(ReviewError, match=r"共通の祖先がありません: main\.\.unrelated"):
        resolve_range(root, "main..unrelated")


def test_empty_range_fails_with_its_reason(repo: dict[str, str]) -> None:
    with pytest.raises(ReviewError, match=r"範囲に変更がありません: feature\.\.feature"):
        resolve_range(Path(repo["root"]), "feature..feature")


def test_long_commits_are_cut_and_warned(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    (root / "edit.py").write_text("def alpha():\n    return 3\n", encoding="utf-8")
    commit_all(root, "long\n\n" + "あ" * (DECLARATION_TEXT_LIMIT + 5))
    review = create_range_review(root, "main..feature", "long")
    assert review.declaration.intent is not None
    assert len(review.declaration.intent) == DECLARATION_TEXT_LIMIT
    assert any("切った" in warning for warning in review.warnings)


def test_title_only_commits_warn_as_thin(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    git(root, "switch", "-c", "thin", "main")
    (root / "thin.py").write_text("y = 1\n", encoding="utf-8")
    commit_all(root, "only a title")
    review = create_range_review(root, "main..thin", "thin")
    assert review.warnings == [EMPTY_COMMITS_WARNING]


def test_many_title_only_commits_are_thin_and_cut() -> None:
    titles = "\n\n".join(f"commit {number}" for number in range(3000))
    declaration = commits_declaration(titles, has_body=False)
    assert declaration.warnings[0] == EMPTY_COMMITS_WARNING
    assert len(declaration.warnings) == 2
    assert declaration.intent is not None
    assert len(declaration.intent) == DECLARATION_TEXT_LIMIT


def test_range_review_is_readable(
    repo: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    created = create_range_review(Path(repo["root"]), "main..feature", "mine")
    assert resolve_review("octo/hello@mine") == created.id
    assert resolve_review("hello@mine") == created.id
    assert [review.id for review in list_reviews()] == [created.id]
    assert load_review(created.id).origin == created.origin
    assert main(["show", "hello@mine"]) == EXIT_OK
    assert capsys.readouterr().out


def test_background_reads_record_with_removed_github_material_key() -> None:
    background = parse_background({"issues": [], "documents": [], "github_material": False})
    assert background.documents == ()


def test_range_review_stores_the_working_directory_root(repo: dict[str, str]) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    assert isinstance(review.origin, RangeOrigin)
    assert review.origin.range.working_directory == str(Path(repo["root"]).resolve())
    stored = json.loads((review.id.directory() / "review.json").read_text(encoding="utf-8"))
    assert stored["local_range"] == {
        "range_text": "main..feature",
        "working_directory": review.origin.range.working_directory,
    }
    assert set(stored["diff_source"]) == {"base_sha", "head_sha", "merge_base", "base_tree"}


def test_range_record_without_local_range_is_rejected(repo: dict[str, str]) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    path = review.id.directory() / "review.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["local_range"] = None
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ReviewError, match="範囲の記録に"):
        load_review(review.id)


def test_origin_from_stored_follows_the_stored_kind() -> None:
    source = DiffSource("b", "h", "m", "x")
    pull_request = PullRequestInfo("u", 1, "t")
    local_range = LocalRange("a..b", "/x")
    feature, pr, rng = ReviewKind.FEATURE, ReviewKind.PULL_REQUEST, ReviewKind.RANGE
    assert (
        review_module._origin_from_stored(feature, None, None, None, None, design_doc=None)
        == FeatureOrigin()
    )
    assert review_module._origin_from_stored(
        pr, pull_request, None, source, None, design_doc=None
    ) == PullRequestOrigin(pull_request, source)
    assert review_module._origin_from_stored(
        rng, None, local_range, source, None, design_doc=None
    ) == RangeOrigin(local_range, source)
    for kind, info, stored_range, diff in (
        (pr, pull_request, local_range, source),
        (rng, None, local_range, None),
        (pr, pull_request, None, None),
        (feature, None, None, source),
        (rng, pull_request, local_range, source),
    ):
        with pytest.raises(ReviewError):
            review_module._origin_from_stored(
                kind, info, stored_range, diff, None, design_doc=None
            )


def test_new_range_cli_prints_the_review(
    repo: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo["root"])
    assert main(["new", "range", "main..feature", "--name", "mine"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "レビューを作りました: octo/hello@mine" in out
    assert f"merge-base {repo['merge_base'][:7]}" in out


def test_new_range_cli_json(
    repo: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo["root"])
    assert main(["new", "range", "main..feature", "--name", "mine", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "octo/hello@mine"
    assert data["local_range"]["range_text"] == "main..feature"
    assert data["diff_source"]["head_sha"] == repo["head"]


def test_new_range_cli_fails_on_an_unresolvable_range(
    repo: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo["root"])
    assert main(["new", "range", "main..nope"]) == EXIT_PROCESSING_FAILURE
    assert capsys.readouterr().err
    assert list_reviews() == []


def test_refresh_without_change_is_not_updated(repo: dict[str, str]) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    result = refresh_review(review.id)
    assert result.updated is False


def test_refresh_after_a_new_commit_rebuilds_the_review(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    review = create_range_review(root, "main..feature", "mine")
    (root / "extra.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    new_head = commit_all(root, "add delta")
    result = refresh_review(review.id)
    assert result.updated is True
    assert result.review.diff_origin is not None
    assert result.review.diff_origin.head_sha == new_head
    assert "extra.py" in result.review.paths
    assert ("extra.py" in [pair.new_path for pair in load_pairs(review.id)]) is True
    assert "add delta" in str(result.review.declaration.intent)
    assert result.review.refreshed_at is not None
    assert refresh_review(review.id).updated is False


def test_refresh_follows_a_moved_merge_base(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    review = create_range_review(root, "main..feature", "mine")
    git(root, "merge", "main", "-m", "merge main")
    git(root, "switch", "main")
    git(root, "merge", "--ff-only", "feature")
    (root / "later.py").write_text("y = 2\n", encoding="utf-8")
    commit_all(root, "main moves again")
    git(root, "switch", "feature")
    (root / "later_feature.py").write_text("z = 3\n", encoding="utf-8")
    commit_all(root, "feature moves")
    new_merge_base = git(root, "merge-base", "main", "feature")
    assert new_merge_base != repo["merge_base"]
    result = refresh_review(review.id)
    assert result.updated is True
    assert result.review.diff_origin is not None
    assert result.review.diff_origin.merge_base == new_merge_base


def test_refresh_cli_resolves_in_the_stored_root(
    repo: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(repo["root"])
    monkeypatch.chdir(root)
    assert main(["new", "range", "main..feature", "--name", "mine"]) == EXIT_OK
    (root / "extra.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    new_head = commit_all(root, "add delta")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    capsys.readouterr()
    assert main(["refresh", "octo/hello@mine"]) == EXIT_OK
    refreshed = load_review(resolve_review("octo/hello@mine")).diff_origin
    assert refreshed is not None
    assert refreshed.head_sha == new_head


def test_refresh_with_a_removed_working_directory_points_to_rebuild(
    repo: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(repo["root"])
    monkeypatch.chdir(root)
    assert main(["new", "range", "main..feature", "--name", "mine"]) == EXIT_OK
    monkeypatch.chdir(root.parent)
    remove_tree(root)
    capsys.readouterr()
    assert main(["refresh", "octo/hello@mine"]) == EXIT_PROCESSING_FAILURE
    err = capsys.readouterr().err
    assert "作業ディレクトリがありません" in err
    assert "new range" in err


def test_range_text_is_shown_to_the_user(
    repo: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo["root"])
    assert main(["new", "range", "main..feature", "--name", "mine"]) == EXIT_OK
    assert "範囲: main..feature" in capsys.readouterr().out
    assert main(["show", "hello@mine"]) == EXIT_OK
    assert "範囲: main..feature" in capsys.readouterr().out
    assert main(["list", "--json"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert [row["range_text"] for row in rows] == ["main..feature"]


def _region_ids(review: Review) -> dict[str, str]:
    return {region["path"]: region["id"] for region in review.map_kind.to_dict()["regions"]}


def _add_commit(root: Path) -> None:
    (root / "extra.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    commit_all(root, "add delta")


def test_refresh_keeps_a_declaration_made_from_the_intent(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(intent="alpha を直す")
    )
    _add_commit(root)
    result = refresh_review(review.id)
    assert result.updated is True
    assert "extra.py" in result.review.paths
    assert result.review.declaration == review.declaration
    assert load_review(review.id).declaration == review.declaration


def test_refresh_rebuilds_a_declaration_made_from_the_commits(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    review = create_range_review(root, "main..feature", "mine")
    assert "add delta" not in str(review.declaration.intent)
    _add_commit(root)
    result = refresh_review(review.id)
    assert result.review.declaration != review.declaration
    assert "add delta" in str(result.review.declaration.intent)


def _rewrite_declaration_intent(review: Review, intent: str) -> None:
    path = review.id.directory() / "review.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["declaration"]["intent"] = intent
    path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")


def test_refresh_with_only_the_declaration_changed_saves_it(repo: dict[str, str]) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    # コミットの列から作る宣言の規則が変わる前に保存した記録を真似る
    _rewrite_declaration_intent(review, "古い規則で作った宣言")
    assert load_review(review.id).declaration.intent == "古い規則で作った宣言"
    before = _region_ids(review)
    result = refresh_review(review.id)
    assert result.updated is True
    assert result.review.declaration == review.declaration
    assert result.review.refreshed_at is not None
    assert load_review(review.id).declaration == review.declaration
    assert _region_ids(result.review) == before
    assert refresh_review(review.id).updated is False


def test_refresh_with_a_stale_map_and_declaration_saves_both(repo: dict[str, str]) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    _rewrite_declaration_intent(review, "古い規則で作った宣言")
    map_path = review.id.directory() / "map.json"
    stored = json.loads(map_path.read_text(encoding="utf-8"))
    stored["segments"][0]["kind"] = "generated"
    map_path.write_text(json.dumps(stored), encoding="utf-8")
    result = refresh_review(review.id)
    assert result.updated is True
    assert result.review.map_kind.to_dict() == review.map_kind.to_dict()
    assert result.review.declaration == review.declaration


def test_refresh_with_a_working_directory_that_is_not_a_repository_points_to_rebuild(
    repo: dict[str, str],
) -> None:
    root = Path(repo["root"])
    review = create_range_review(root, "main..feature", "mine")
    remove_tree(root / ".git")
    assert root.is_dir()
    with pytest.raises(ReviewError, match=r"リポジトリではありません.*new range"):
        refresh_review(review.id)


def test_refresh_keeps_region_ids_across_new_commits(repo: dict[str, str]) -> None:
    root = Path(repo["root"])
    review = create_range_review(root, "main..feature", "mine")
    before = _region_ids(review)
    assert {"gone.py", "new.py"} <= set(before)
    _add_commit(root)
    result = refresh_review(review.id)
    after = _region_ids(result.review)
    assert "extra.py" in after
    assert {path: after[path] for path in before} == before
    assert result.region_changes.added == [after["extra.py"]]
    assert result.region_changes.removed == []


def test_refresh_range_saves_when_only_gates_change(
    repo: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = create_range_review(Path(repo["root"]), "main..feature", "mine")
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


def test_refresh_range_does_not_tag_the_regex_of_a_file_moved_without_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "moved"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/octo/hello.git")
    (root / "old.py").write_text(
        'import re\n\nTEXT = re.sub("abc", "", "xabc")\n', encoding="utf-8"
    )
    (root / "CODEOWNERS").write_text("*.py @octo\n", encoding="utf-8")
    commit_all(root, "init")
    git(root, "switch", "-c", "feature")
    git(root, "mv", "old.py", "new.py")
    commit_all(root, "move old")
    created = create_range_review(root, "main..feature", "moved")
    assert created.gates is not None
    assert created.gates.violations == ()
    _add_commit(root)
    result = refresh_review(created.id)
    assert result.updated is True
    assert result.review.gates is not None
    assert result.review.gates.violations == ()


def _pick_one_finding(review: Review) -> None:
    state = {
        "last_number": 1,
        "last_theme_number": 0,
        "stale_groups": [],
        "groups": {
            "macro": [],
            "micro": [
                {
                    "id": "F1",
                    "severity": "warning",
                    "group": "micro",
                    "viewpoint": "D1",
                    "claim": "range-claim",
                    "body": "body",
                    "evidence": [{"path": "edit.py", "start_line": 1, "end_line": 2}],
                    "adjudication": {
                        "kind": "rebuttal",
                        "challenge": "c",
                        "recheck": [],
                        "verdict": "kept",
                        "reason": "r",
                    },
                    "theme": None,
                    "triage": "must_read",
                    "judgment_region": None,
                }
            ],
            "security": [],
        },
        "themes": [],
    }
    (review.id.directory() / "findings.json").write_text(json.dumps(state), encoding="utf-8")
    assert main(["pick", str(review.id), "F1"]) == EXIT_OK


def test_reply_on_a_range_review_makes_text_and_notes_no_target(
    repo: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    _pick_one_finding(review)
    capsys.readouterr()
    assert main(["export-reply", str(review.id), "--template", "reply"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "range-claim" in captured.out
    assert main(["export-reply", str(review.id), "--template", "reply", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert "range-claim" in payload["body"]
    assert set(payload) == {"body"}


def test_reply_lists_the_gate_violations(
    repo: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    _pick_one_finding(review)
    violation = {
        "number": "G1",
        "id": "S1",
        "explanation": {"text": "懸念の文。理由の文。向きの文。"},
        "path": "src/a.py",
        "line": 3,
        "excerpt": "x",
        "source": "flaky",
        "reason": "",
        "cause": "c",
        "confidence": "direct",
        "origin": "o",
        "legitimate": "",
    }
    gates = {"violations": [violation], "warnings": []}
    write_gates_file(review.id.directory(), gates)
    capsys.readouterr()
    assert main(["export-reply", str(review.id), "--template", "reply"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert "## ゲート違反" in lines
    assert any("次の 1 件とゲート違反 1 件を確認してください。" in line for line in lines)
    assert "- **G1** `src/a.py:3` 懸念の文。理由の文。向きの文。" in lines


def test_reply_shows_the_strength_of_a_regex_tag(
    repo: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    _pick_one_finding(review)
    violation = {
        "number": "G1",
        "id": "R7",
        "explanation": {"text": "懸念の文。理由の文。向きの文。"},
        "path": "src/a.py",
        "line": 3,
        "excerpt": "x",
        "severity": "nit",
        "source": "regex",
    }
    gates = {"format": 2, "violations": [violation], "warnings": []}
    write_gates_file(review.id.directory(), gates)
    capsys.readouterr()
    assert main(["export-reply", str(review.id), "--template", "reply"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert "- **G1**（nit） `src/a.py:3` 懸念の文。理由の文。向きの文。" in lines


def test_range_refresh_marks_results_stale(
    repo: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(repo["root"])
    review = create_range_review(root, "main..feature", "mine")
    assert main(
        ["run", str(review.id), "macro"], invoke=MacroFake([discovered(path="edit.py")])
    ) == (EXIT_OK)
    # 何も変わっていない refresh は、結果に古い印を付けない
    assert main(["refresh", str(review.id)]) == EXIT_OK
    capsys.readouterr()
    assert main(["show", str(review.id), "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["stale"] is False
    _add_commit(root)
    assert main(["refresh", str(review.id)]) == EXIT_OK
    capsys.readouterr()
    assert main(["show", str(review.id), "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["stale"] is True


def test_refresh_intent_replaces_the_commits_declaration_and_is_then_kept(
    repo: dict[str, str],
) -> None:
    created = create_range_review(Path(repo["root"]), "main..feature", name="r")
    assert created.declaration.source == "commits"
    result = refresh_review(created.id, DeclaredInputs(intent="alpha と gamma を直す"))
    assert result.updated is True
    assert result.review.declaration.source == "intent"
    assert load_review(created.id).declaration.intent == "alpha と gamma を直す"
    assert refresh_review(created.id).updated is False
    assert load_review(created.id).declaration.intent == "alpha と gamma を直す"


def test_list_text_shows_the_range_text_under_a_range_review(
    repo: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    capsys.readouterr()
    assert main(["list"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(str(review.id)))
    assert lines[index + 1] == "  範囲: main..feature"


def test_range_background_reads_the_same_in_the_creator_and_the_macro_material(
    repo: dict[str, str],
) -> None:
    """作り手（collect_range_background）と macro の読みが同じ背景の形を前提にする。"""
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    # 作り手: GitHub の材料は空で、CODEOWNERS が入る
    assert review.background is not None
    assert review.background.documents == ()
    assert review.background.owners
    # 読み手: macro の資料は CODEOWNERS を出し、PR の本文の材料を名乗らない
    material = background_prompt_text(review)
    assert "CODEOWNERS" in material
    assert "PR の本文" not in material
    assert "issue #" not in material


@pytest.mark.parametrize("depth", ["", "sub", "sub/deeper"])
def test_create_and_refresh_use_the_same_root_from_any_depth(
    repo: dict[str, str], monkeypatch: pytest.MonkeyPatch, depth: str
) -> None:
    root = Path(repo["root"])
    working = root / depth
    working.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(working)
    review = create_range_review(working, "main..feature", "mine")
    assert isinstance(review.origin, RangeOrigin)
    stored_root = review.origin.range.working_directory
    assert stored_root == str(root.resolve())
    # 作業ディレクトリが深くても、リポジトリの識別は根で決まる
    assert str(review.id) == "octo/hello@mine"
    # head が進んだ後の refresh は、作成時に保存した根で範囲を解き直す
    git(root, "switch", "feature")
    (root / "later.py").write_text("z = 1\n", encoding="utf-8")
    advanced = commit_all(root, "later")
    result = refresh_review(review.id, working_directory=working)
    assert result.updated
    refreshed = load_review(review.id)
    assert isinstance(refreshed.origin, RangeOrigin)
    assert refreshed.origin.range.working_directory == stored_root
    assert refreshed.origin.diff.head_sha == advanced
    assert refreshed.repository_root == review.repository_root


@pytest.mark.parametrize(
    ("range_text", "branch", "expected"),
    [
        ("main..feature", None, "feature"),
        ("main..topic/x", "topic/x", "topic-x"),
        ("main..release/v1.0/rc", "release/v1.0/rc", "release-v1.0-rc"),
        ("main..", None, None),
        ("main..HEAD", None, None),
    ],
)
def test_default_name_follows_the_head_side_of_every_range_shape(
    repo: dict[str, str], range_text: str, branch: str | None, expected: str | None
) -> None:
    root = Path(repo["root"])
    if branch is not None:
        git(root, "branch", branch, "feature")
    review = create_range_review(root, range_text)
    assert review.id.key == (expected if expected is not None else repo["head"][:8])


def test_default_name_rejects_a_three_dot_range_before_naming(repo: dict[str, str]) -> None:
    # 三点は二点と同じ merge-base からの差になるので、書き方を増やさず二点だけを受ける
    with pytest.raises(ReviewError, match=r"<base>\.\.<head> の形"):
        create_range_review(Path(repo["root"]), "main...feature")
    assert list_reviews() == []


def test_resolve_commit_puts_the_end_of_options_marker_before_the_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launched: list[list[str]] = []

    def fake_run(args: list[str], **_keywords: object) -> str:
        launched.append(args)
        return "0" * 40 + "\n"

    monkeypatch.setattr(process, "run_command", fake_run)
    review_module._resolve_commit(tmp_path, "--output=x")
    (args,) = launched
    assert args[-2:] == ["--end-of-options", "--output=x^{commit}"]
