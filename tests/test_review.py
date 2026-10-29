import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFake, discovered
from pr_support import git
from review_fakes import EXTRA_WARNING, build_content, save_review_of_kind

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands.refresh import refresh_review
from jigane.flaky.catalog import Item
from jigane.flaky.gates import update_gates
from jigane.gates.entry import Gates
from jigane.judgment_map.classification import Segment
from jigane.judgment_map.declaration import NO_DECLARATION_WARNING
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import (
    REVIEW_FILE,
    TRASH_DIRECTORY,
    DeclaredInputs,
    FeatureOrigin,
    RangeOrigin,
    ReviewError,
    ReviewKindMismatchError,
    create_feature_review,
    list_reviews,
    load_review,
    load_review_at,
    resolve_review,
    set_archived,
)
from jigane.storage.store import ReviewId, ReviewKind, data_home

PUBLIC_SOURCE = "def build_widget():\n    return 1\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text(PUBLIC_SOURCE, encoding="utf-8")
    (root / "README.md").write_text("# Shop\n\nlead\n\n## Use\n\nbuy\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_create_saves_and_loads(project: Path) -> None:
    review = create_feature_review(project, ["src"], "payment")
    directory = review.id.directory()
    for name in ("review.json", "map.json", "regions.json"):
        assert (directory / name).is_file()
    loaded = load_review(review.id)
    assert loaded.paths == ["src/a.py"]
    assert loaded.warnings == []
    assert loaded.map_kind.to_dict()["regions"][0]["id"] == "J1"
    shown = loaded.to_dict()["judgment_map"]["line_counts"]
    for level, total in shown["by_level"].items():
        assert sum(levels[level] for levels in shown["by_file"].values()) == total
    assert str(review.id) == "local/shop:payment"


def test_numbers_are_stable_and_not_reused(project: Path) -> None:
    first = create_feature_review(project, ["src"], "payment")
    (project / "src" / "b.py").write_text("def other_widget():\n    return 2\n", encoding="utf-8")
    second = refresh_review(first.id).review
    by_path = {r["path"]: r["id"] for r in second.map_kind.to_dict()["regions"]}
    assert by_path["src/a.py"] == first.map_kind.to_dict()["regions"][0]["id"] == "J1"
    assert by_path["src/b.py"] == "J2"
    (project / "src" / "b.py").unlink()
    (project / "src" / "c.py").write_text("def third_widget():\n    return 3\n", encoding="utf-8")
    third = refresh_review(first.id).review
    assert {r["path"]: r["id"] for r in third.map_kind.to_dict()["regions"]}["src/c.py"] == "J3"


def test_missing_declaration_warns_and_intent_wins(project: Path) -> None:
    (project / "README.md").unlink()
    warned = create_feature_review(project, ["src"], "one")
    assert warned.warnings
    assert "M1" in warned.warnings[0]
    given = create_feature_review(project, ["src"], "two", DeclaredInputs(intent="make widgets"))
    assert given.warnings == []
    assert given.declaration.intent == "make widgets"


def test_short_identifier_resolution(project: Path) -> None:
    create_feature_review(project, ["src"], "payment")
    expected = ReviewId("local", "shop", "payment", ReviewKind.FEATURE)
    assert resolve_review("shop:payment") == expected
    assert resolve_review("local/shop:payment").key == "payment"
    with pytest.raises(ReviewError):
        resolve_review("shop:missing")
    other = ReviewId("acme", "shop", "payment", ReviewKind.FEATURE).directory()
    other.mkdir(parents=True)
    source = ReviewId("local", "shop", "payment", ReviewKind.FEATURE).directory()
    meta = json.loads((source / "review.json").read_text(encoding="utf-8"))
    meta["id"] = "acme/shop:payment"
    (other / "review.json").write_text(json.dumps(meta), encoding="utf-8")
    (other / "map.json").write_text(
        (source / "map.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert len(list_reviews()) == 2
    with pytest.raises(ReviewError, match="曖昧"):
        resolve_review("shop:payment")


def _chdir_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """レビューを持つ shop とは別のリポジトリ（local/elsewhere）から呼んだことにする。"""
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)


def test_name_only_identifier_error_shows_candidate(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_feature_review(project, ["src"], "payment")
    _chdir_elsewhere(tmp_path, monkeypatch)
    with pytest.raises(ReviewError) as raised:
        resolve_review("payment")
    message = str(raised.value)
    assert "local/shop:payment" in message
    assert "shop:payment" in message
    assert "jigane list" in message


def test_name_only_identifier_error_shows_every_repository(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_feature_review(project, ["src"], "payment")
    _chdir_elsewhere(tmp_path, monkeypatch)
    other = ReviewId("acme", "cart", "payment", ReviewKind.FEATURE).directory()
    other.mkdir(parents=True)
    source = ReviewId("local", "shop", "payment", ReviewKind.FEATURE).directory()
    meta = json.loads((source / "review.json").read_text(encoding="utf-8"))
    meta["id"] = "acme/cart:payment"
    (other / "review.json").write_text(json.dumps(meta), encoding="utf-8")
    (other / "map.json").write_text(
        (source / "map.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ReviewError) as raised:
        resolve_review("payment")
    assert "local/shop:payment" in str(raised.value)
    assert "acme/cart:payment" in str(raised.value)


def test_name_only_identifier_does_not_load_every_review(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_feature_review(project, ["src"], "payment")
    _chdir_elsewhere(tmp_path, monkeypatch)

    def forbidden(*_: object) -> None:
        raise AssertionError("名前だけの入力で置き場の全件を読んだ")

    monkeypatch.setattr(review_module, "list_reviews", forbidden)
    monkeypatch.setattr(review_module, "load_review_at", forbidden)
    with pytest.raises(ReviewError, match="候補: local/shop:payment"):
        resolve_review("payment")


def test_name_only_identifier_resolves_in_the_repository_of_the_working_directory(
    project: Path, tmp_path: Path
) -> None:
    review = create_feature_review(project, ["src"], "payment")
    other = ReviewId("local", "cart", "payment", ReviewKind.FEATURE).directory()
    shutil.copytree(review.id.directory(), other)
    meta = json.loads((other / "review.json").read_text(encoding="utf-8"))
    meta["id"] = "local/cart:payment"
    (other / "review.json").write_text(json.dumps(meta), encoding="utf-8")
    assert resolve_review("payment") == review.id
    assert resolve_review("payment", tmp_path / "shop") == review.id


def test_name_only_identifier_without_match_in_the_working_repository_is_refused(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_feature_review(project, ["src"], "payment")
    _chdir_elsewhere(tmp_path, monkeypatch)
    with pytest.raises(ReviewError, match="候補: local/shop:payment"):
        resolve_review("payment")


def test_name_only_identifier_matching_a_range_and_a_feature_is_ambiguous(project: Path) -> None:
    save_review_of_kind(project, "c85-eval", ReviewKind.RANGE)
    save_review_of_kind(project, "c85-eval", ReviewKind.FEATURE)
    with pytest.raises(ReviewError, match="曖昧") as raised:
        resolve_review("c85-eval")
    assert "local/shop@c85-eval" in str(raised.value)
    assert "local/shop:c85-eval" in str(raised.value)


def test_name_only_identifier_outside_git_uses_the_directory_name(project: Path) -> None:
    # project は git の外にあるので、リポジトリは local/<ディレクトリ名> になる
    review_id = save_review_of_kind(project, "c85-eval", ReviewKind.RANGE)
    assert review_id.repo == "shop"
    assert resolve_review("c85-eval") == review_id


def test_name_only_identifier_in_a_git_repository_uses_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "work"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "remote", "add", "origin", "https://github.com/acme/cart.git")
    (repository / "src").mkdir()
    (repository / "src" / "a.py").write_text(PUBLIC_SOURCE, encoding="utf-8")
    monkeypatch.chdir(repository)
    saved = create_feature_review(repository, ["src"], "c85-eval").id
    assert str(saved) == "acme/cart:c85-eval"
    assert resolve_review("c85-eval") == saved


def test_name_only_identifier_reads_neither_every_review_nor_review_contents(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = create_feature_review(project, ["src"], "payment")

    def forbidden(*_: object) -> None:
        raise AssertionError("名前だけの入力でレビューの中身か全件を読んだ")

    monkeypatch.setattr(review_module, "list_reviews", forbidden)
    monkeypatch.setattr(review_module, "load_review_at", forbidden)
    monkeypatch.setattr(review_module, "load_review", forbidden)
    assert resolve_review("payment") == review.id


def test_stored_ids_named_includes_archived_reviews(project: Path) -> None:
    review_id = save_review_of_kind(project, "payment", ReviewKind.FEATURE)
    assert set_archived(review_id, archived=True)
    assert review_module._stored_ids_named("payment") == [review_id]


def test_stored_ids_named_skips_the_trash(project: Path) -> None:
    review_id = save_review_of_kind(project, "payment", ReviewKind.FEATURE)
    trashed = data_home() / TRASH_DIRECTORY / "local" / "shop" / "payment"
    trashed.parent.mkdir(parents=True)
    shutil.move(review_id.directory(), trashed)
    assert review_module._stored_ids_named("payment") == []


def test_stored_ids_named_skips_a_broken_review_file(project: Path) -> None:
    broken = save_review_of_kind(project, "payment", ReviewKind.FEATURE)
    intact = save_review_of_kind(project, "payment", ReviewKind.RANGE)
    (broken.directory() / REVIEW_FILE).write_text("{not json", encoding="utf-8")
    assert review_module._stored_ids_named("payment") == [intact]


def test_stored_ids_named_follows_the_prefix_of_each_kind(project: Path) -> None:
    ids = [
        save_review_of_kind(project, "payment", ReviewKind.FEATURE),
        save_review_of_kind(project, "payment", ReviewKind.RANGE),
        save_review_of_kind(project, "payment", ReviewKind.DESIGN_DOC),
    ]
    assert {path.name for path in (data_home() / "local" / "shop").iterdir()} == {
        "payment",
        "@payment",
        "+payment",
    }
    assert sorted(review_module._stored_ids_named("payment"), key=str) == sorted(ids, key=str)


@pytest.mark.usefixtures("project")
def test_name_only_identifier_error_without_match_guides_to_list() -> None:
    with pytest.raises(ReviewError) as raised:
        resolve_review("missing")
    message = str(raised.value)
    assert "このリポジトリ（local/" in message
    assert "のレビューはありません" in message
    assert "jigane list" in message


@pytest.mark.usefixtures("project")
def test_text_outside_the_name_rule_is_not_treated_as_a_name() -> None:
    with pytest.raises(ReviewError) as raised:
        resolve_review("two words")
    assert "名前だけでは指せません" not in str(raised.value)


@pytest.mark.usefixtures("project")
def test_new_prints_next_command_example(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "files", "src", "--name", "payment"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "jigane estimate local/shop:payment macro" in out
    assert out.rstrip().splitlines()[-1] == "このレビューの識別子: local/shop:payment"


@pytest.mark.usefixtures("project")
def test_cli_new_list_show(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "files", "src", "--name", "payment"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "local/shop:payment" in out
    assert "J1" in out
    assert main(["list"]) == EXIT_OK
    assert "local/shop:payment" in capsys.readouterr().out
    assert main(["show", "shop:payment"]) == EXIT_OK
    text = capsys.readouterr().out
    assert "J1" in text
    assert "README.md" in text
    assert main(["show", "shop:payment", "overview", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["judgment_map"]["regions"][0]["id"] == "J1"
    assert main(["list", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)[0]["id"] == "local/shop:payment"
    assert main(["new", "files", "src", "--name", "p2", "--json", "--intent", "x"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["declaration"]["intent"] == "x"


@pytest.mark.usefixtures("project")
def test_cli_failures_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "shop:none"]) == EXIT_PROCESSING_FAILURE
    assert main(["new", "files", "nope", "--name", "x"]) == EXIT_PROCESSING_FAILURE
    assert "jigane:" in capsys.readouterr().err


def test_targets_and_refreshed_at_are_saved(project: Path) -> None:
    review = create_feature_review(project, ["src"], "payment")
    assert review.origin == FeatureOrigin(("src",))
    assert review.refreshed_at is None
    assert load_review(review.id).origin == FeatureOrigin(("src",))


def test_refresh_feature_re_enumerates_files(project: Path) -> None:
    created = create_feature_review(
        project, ["src"], "payment", DeclaredInputs(intent="make widgets")
    )
    (project / "src" / "b.py").write_text("def other_widget():\n    return 2\n", encoding="utf-8")
    result = refresh_review(created.id)
    assert result.updated is True
    assert result.review.paths == ["src/a.py", "src/b.py"]
    assert result.region_changes.added == ["J2"]
    assert result.region_changes.removed == []
    # 利用者の意図は保つ
    assert result.review.declaration.intent == "make widgets"
    assert result.review.created_at == created.created_at
    assert result.review.refreshed_at is not None
    (project / "src" / "b.py").unlink()
    again = refresh_review(created.id)
    assert again.region_changes.removed == ["J2"]
    # 消えた箇所は次の更新で繰り返し報告しない
    assert refresh_review(created.id).region_changes.removed == []


def test_refresh_feature_extracts_excerpts_again(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    assert created.declaration.excerpts
    (project / "README.md").write_text("# Shop\n\nnew lead\n", encoding="utf-8")
    refreshed = refresh_review(created.id).review
    assert "new lead" in refreshed.declaration.excerpts[0].text


def test_refresh_without_targets_is_an_error(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    path = created.id.directory() / "review.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["targets"] = None
    path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ReviewError, match="作り直してください"):
        refresh_review(created.id)


def test_refresh_marks_results_stale_and_run_clears(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "files", "src", "--name", "p"]) == EXIT_OK
    assert main(["run", "shop:p", "macro"], invoke=MacroFake([discovered()])) == EXIT_OK
    capsys.readouterr()
    # 何も変わっていない refresh は、結果に古い印を付けない
    assert main(["refresh", "shop:p"]) == EXIT_OK
    assert "変更なし" in capsys.readouterr().out
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["stale"] is False
    (project / "src" / "b.py").write_text("def other_widget():\n    return 2\n", encoding="utf-8")
    assert main(["refresh", "shop:p"]) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "注: refresh の後に再実行していない結果です"
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["stale"] is True
    assert main(["run", "shop:p", "macro"], invoke=MacroFake([discovered()])) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["stale"] is False
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    assert "注:" not in capsys.readouterr().out


def test_refresh_without_results_leaves_no_findings_file(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    refresh_review(created.id)
    assert not (created.id.directory() / "findings.json").exists()


def test_refresh_feature_unchanged_writes_nothing(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    directory = created.id.directory()
    names = ("review.json", "map.json", "regions.json")
    before = {name: (directory / name).read_bytes() for name in names}
    result = refresh_review(created.id)
    assert result.updated is False
    assert result.region_changes.added == []
    assert {name: (directory / name).read_bytes() for name in names} == before


@pytest.mark.parametrize("intent", ["   ", "あ" * 16_001], ids=["blank", "over-limit"])
def test_refresh_keeps_the_intent_warnings_from_creation(project: Path, intent: str) -> None:
    created = create_feature_review(project, ["src"], "payment", DeclaredInputs(intent=intent))
    assert created.warnings
    result = refresh_review(created.id)
    assert result.updated is False
    assert result.review.warnings == created.warnings
    assert load_review(created.id).warnings == created.warnings


def test_refresh_ignores_judged_and_note_left_in_old_saves(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    directory = created.id.directory()
    regions_path = directory / "regions.json"
    state = json.loads(regions_path.read_text(encoding="utf-8"))
    entry = next(iter(state["regions"].values()))
    entry["judged"] = True
    entry["note"] = "確認済み"
    regions_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    map_path = directory / "map.json"
    saved_map = json.loads(map_path.read_text(encoding="utf-8"))
    saved_map["regions"][0]["judged"] = True
    saved_map["regions"][0]["note"] = "確認済み"
    map_path.write_text(json.dumps(saved_map, ensure_ascii=False), encoding="utf-8")
    assert refresh_review(created.id).updated is False
    loaded = load_review(created.id)
    assert loaded.map_kind.to_dict()["regions"][0]["id"] == saved_map["regions"][0]["id"]


def test_refresh_feature_compares_gates(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created = create_feature_review(project, ["src"], "payment")
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
    assert refresh_review(created.id).updated is True


def test_resolve_full_identifier_does_not_list_reviews(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = create_feature_review(project, ["src"], "payment")

    def forbidden() -> None:
        raise AssertionError("list_reviews は呼ばない")

    monkeypatch.setattr(review_module, "list_reviews", forbidden)
    assert resolve_review(str(created.id)) == created.id
    with pytest.raises(ReviewError):
        resolve_review("local/shop:missing")


def _stored_files(directory: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != "review.json"
    }


def test_save_review_takes_content_and_matches_create_and_refresh(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    directory = created.id.directory()
    after_create = _stored_files(directory)
    content = review_module._build_feature_content(project, [project / "src"], None)
    saved, _ = review_module.save_review(
        created.id,
        content,
        created.created_at,
        None,
        str(project),
    )
    assert saved.mode == "feature"
    assert _stored_files(directory) == after_create
    refresh_review(created.id)
    assert _stored_files(directory) == after_create
    stored = json.loads((directory / "review.json").read_text(encoding="utf-8"))
    assert stored["paths"] == list(content.paths)
    assert stored["declaration"]["source"] == content.declaration.source


def test_save_review_stores_a_diff_without_pull_request(project: Path) -> None:
    feature = create_feature_review(project, ["src"], "payment")
    diff_content = build_content(project, ReviewKind.RANGE)
    assert isinstance(diff_content.origin, RangeOrigin)
    source = diff_content.origin.diff
    base = project.parent / "base"
    review_id = ReviewId(feature.id.owner, feature.id.repo, "range", ReviewKind.RANGE)
    saved, _ = review_module.save_review(review_id, diff_content, "t", None, str(project))
    assert saved.mode == "diff"
    stored = json.loads((review_id.directory() / "review.json").read_text(encoding="utf-8"))
    assert "mode" not in stored
    assert stored["pull_request"] is None
    loaded = load_review(review_id)
    assert loaded.diff_origin == source
    assert not loaded.is_pull_request
    assert loaded.background is None
    assert loaded.base_tree_path == base
    assert not (review_id.directory() / "bot_comments.json").exists()


def test_save_pull_request_review_writes_bot_comments_file(project: Path) -> None:
    review_id = save_review_of_kind(project, "7", ReviewKind.PULL_REQUEST)
    assert (review_id.directory() / "bot_comments.json").is_file()


def test_range_review_goes_through_resolve_and_list(project: Path) -> None:
    feature = create_feature_review(project, ["src"], "payment")
    review_id = save_review_of_kind(project, "v1", ReviewKind.RANGE)
    assert review_id.directory().name == "@v1"
    assert resolve_review("shop@v1") == review_id
    assert resolve_review(f"{feature.id.owner}/shop@v1") == review_id
    listed = {review.id for review in list_reviews()}
    assert listed == {feature.id, review_id}
    with pytest.raises(ReviewError):
        resolve_review("shop@missing")


def test_feature_review_over_existing_is_an_error(project: Path) -> None:
    create_feature_review(project, ["src"], "payment")
    with pytest.raises(ReviewError, match="既にあります"):
        create_feature_review(project, ["src"], "payment")


def test_feature_review_with_digits_only_name_is_an_error(project: Path) -> None:
    with pytest.raises(ReviewError, match="保存先が重なります"):
        create_feature_review(project, ["src"], "7")


def test_legacy_digits_only_feature_record_does_not_break_list_or_resolve(
    project: Path,
) -> None:
    kept = create_feature_review(project, ["src"], "kept")
    legacy = create_feature_review(project, ["src"], "legacy")
    old_directory = kept.id.directory().parent / "7"
    shutil.copytree(legacy.id.directory(), old_directory)
    meta_path = old_directory / REVIEW_FILE
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = f"{legacy.id.owner}/{legacy.id.repo}:7"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    digits_id = ReviewId(legacy.id.owner, legacy.id.repo, "7", ReviewKind.FEATURE)
    listed = list_reviews()
    assert {review.id for review in listed} == {kept.id, legacy.id, digits_id}
    assert resolve_review(f"{legacy.id.owner}/{legacy.id.repo}:7") == digits_id
    assert resolve_review(f"{legacy.id.repo}:7") == digits_id
    assert load_review(digits_id).kind is ReviewKind.FEATURE
    # 同じ保存先を指す PR の識別子では読めない。種類は保存した記録の id が決める
    pull_request_id = ReviewId(legacy.id.owner, legacy.id.repo, "7", ReviewKind.PULL_REQUEST)
    with pytest.raises(ReviewKindMismatchError):
        load_review(pull_request_id)


def test_list_skips_a_record_whose_directory_kind_differs_from_its_id(project: Path) -> None:
    kept = create_feature_review(project, ["src"], "kept")
    moved = create_feature_review(project, ["src"], "moved")
    # 識別子は範囲（@moved）を名乗るが、保存先は機能のディレクトリ名（印が無い）
    meta_path = moved.id.directory() / REVIEW_FILE
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = f"{moved.id.owner}/{moved.id.repo}@moved"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.warns(UserWarning, match="保存した出所が食い違います"):
        listed = list_reviews()
    assert [review.id for review in listed] == [kept.id]


@pytest.mark.parametrize(
    "damage",
    [
        lambda _meta: "{ not json",
        lambda meta: json.dumps({**meta, "id": "not an id"}),
        lambda meta: json.dumps({key: value for key, value in meta.items() if key != "paths"}),
        lambda meta: json.dumps({**meta, "declaration": 3}),
    ],
    ids=["broken-json", "bad-id", "missing-field", "bad-field-type"],
)
def test_load_review_at_raises_only_review_error_and_list_skips(
    project: Path, damage: Callable[[dict[str, Any]], str]
) -> None:
    kept = create_feature_review(project, ["src"], "kept")
    broken = create_feature_review(project, ["src"], "broken")
    meta_path = broken.id.directory() / REVIEW_FILE
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_path.write_text(damage(meta), encoding="utf-8")
    with pytest.raises(ReviewError):
        load_review_at(broken.id.directory())
    with pytest.warns(UserWarning, match="一覧から除きました"):
        listed = list_reviews()
    assert [review.id for review in listed] == [kept.id]


def test_load_review_at_reads_a_review_from_its_directory(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    assert load_review_at(created.id.directory()).id == created.id


@pytest.mark.parametrize("wrong_kind", [ReviewKind.FEATURE, ReviewKind.PULL_REQUEST])
def test_save_review_refuses_an_id_whose_kind_differs_from_the_content(
    project: Path, wrong_kind: ReviewKind
) -> None:
    range_content = build_content(project, ReviewKind.RANGE)
    key = "7" if wrong_kind is ReviewKind.PULL_REQUEST else "name"
    review_id = ReviewId("local", "shop", key, wrong_kind)
    with pytest.raises(ReviewKindMismatchError):
        review_module.save_review(review_id, range_content, "t", None, str(project))
    assert not review_id.directory().exists()


def test_save_review_refuses_a_range_id_for_feature_content(project: Path) -> None:
    content = review_module._build_feature_content(project, [project / "src"], None)
    review_id = ReviewId("local", "shop", "name", ReviewKind.RANGE)
    with pytest.raises(ReviewKindMismatchError):
        review_module.save_review(review_id, content, "t", None, str(project))
    assert not review_id.directory().exists()


@pytest.mark.parametrize(
    ("text", "expected_kind", "expected_key"),
    [
        ("shop#7", ReviewKind.PULL_REQUEST, "7"),
        ("shop@7", ReviewKind.RANGE, "7"),
        ("shop:v1", ReviewKind.FEATURE, "v1"),
        ("shop@v1", ReviewKind.RANGE, "v1"),
    ],
)
def test_short_id_resolves_by_kind_among_reviews_with_the_same_key(
    project: Path, text: str, expected_kind: ReviewKind, expected_key: str
) -> None:
    saved = {
        (kind, key): save_review_of_kind(project, key, kind)
        for kind, key in (
            (ReviewKind.PULL_REQUEST, "7"),
            (ReviewKind.RANGE, "7"),
            (ReviewKind.FEATURE, "v1"),
            (ReviewKind.RANGE, "v1"),
        )
    }
    resolved = resolve_review(text)
    assert resolved == saved[(expected_kind, expected_key)]
    assert resolved.kind is expected_kind


def test_short_id_of_a_kind_that_does_not_exist_is_not_found_even_if_the_key_does(
    project: Path,
) -> None:
    save_review_of_kind(project, "7", ReviewKind.RANGE)
    save_review_of_kind(project, "v1", ReviewKind.FEATURE)
    with pytest.raises(ReviewError, match="レビューがありません"):
        resolve_review("shop#7")
    with pytest.raises(ReviewError, match="レビューがありません"):
        resolve_review("shop@v1")


def test_full_id_of_another_kind_does_not_resolve_to_a_digits_only_feature(
    project: Path,
) -> None:
    feature_id = save_review_of_kind(project, "7", ReviewKind.FEATURE)
    with pytest.raises(ReviewKindMismatchError):
        resolve_review("local/shop#7")
    with pytest.raises(ReviewKindMismatchError):
        load_review(ReviewId("local", "shop", "7", ReviewKind.PULL_REQUEST))
    assert resolve_review(str(feature_id)) == feature_id


def test_intent_declaration_warnings_are_stored_in_the_declaration(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment", DeclaredInputs(intent="   "))
    stored = json.loads((created.id.directory() / "review.json").read_text(encoding="utf-8"))
    assert stored["declaration"]["warnings"] == list(created.declaration.warnings)
    assert created.declaration.warnings
    assert load_review(created.id).declaration == created.declaration


def test_old_record_without_content_warnings_reads_warnings_as_content(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    path = created.id.directory() / "review.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    # 版を持たない古い記録の形にする
    del stored["format"]
    stored.pop("content_warnings")
    stored["warnings"] = ["古い記録の警告"]
    path.write_text(json.dumps(stored), encoding="utf-8")
    loaded = load_review(created.id)
    assert loaded.content_warnings == ["古い記録の警告"]
    assert loaded.warnings == ["古い記録の警告"]


def test_missing_declaration_warning_is_a_declaration_warning(project: Path) -> None:
    (project / "README.md").unlink()
    created = create_feature_review(project, ["src"], "one")
    assert created.declaration.warnings == (NO_DECLARATION_WARNING,)
    assert created.content_warnings == []
    stored = json.loads((created.id.directory() / "review.json").read_text(encoding="utf-8"))
    assert stored["content_warnings"] == []
    assert load_review(created.id).warnings == [NO_DECLARATION_WARNING]


def test_refresh_intent_replaces_the_feature_declaration_and_stales_results(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    created = create_feature_review(
        project, ["src"], "payment", DeclaredInputs(intent="make widgets")
    )
    assert main(["run", "shop:payment", "macro"], invoke=MacroFake([discovered()])) == EXIT_OK
    capsys.readouterr()
    # 何も変えない refresh は宣言を保つ
    kept = refresh_review(created.id)
    assert (kept.updated, kept.review.declaration.intent) == (False, "make widgets")
    result = refresh_review(created.id, DeclaredInputs(intent="make widgets and gadgets"))
    assert result.updated is True
    assert result.review.declaration.intent == "make widgets and gadgets"
    assert load_review(created.id).declaration.intent == "make widgets and gadgets"
    assert main(["show", "shop:payment", "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["stale"] is True
    # 同じ文を重ねて渡しても、変わらないので更新しない
    assert (
        refresh_review(created.id, DeclaredInputs(intent="make widgets and gadgets")).updated
        is False
    )
    # 以後の refresh は差し替えた宣言を保つ
    assert refresh_review(created.id).review.declaration.intent == "make widgets and gadgets"


def test_refresh_intent_file_reads_relative_to_the_working_directory(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment", DeclaredInputs(intent="old"))
    (project / "intent.txt").write_text("from a file", encoding="utf-8")
    result = refresh_review(
        created.id, DeclaredInputs(intent_file=Path("intent.txt")), working_directory=project
    )
    assert result.review.declaration.intent == "from a file"


def test_refresh_rejects_both_intent_options(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment", DeclaredInputs(intent="old"))
    with pytest.raises(ReviewError, match="同時に指定できません"):
        refresh_review(created.id, DeclaredInputs(intent="a", intent_file=Path("b")))


def test_cli_refresh_intent_replaces_the_declaration(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "payment", DeclaredInputs(intent="old"))
    assert main(["refresh", "shop:payment", "--intent", "new intent"]) == EXIT_OK
    assert "レビューを更新しました" in capsys.readouterr().out
    assert load_review(resolve_review("shop:payment")).declaration.intent == "new intent"
    assert main(["refresh", "shop:payment", "--intent", "newer", "--json"]) == EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    assert printed["updated"] is True
    assert printed["review"]["declaration"]["intent"] == "newer"


def test_cli_refresh_intent_file_replaces_the_declaration(
    project: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_feature_review(project, ["src"], "payment", DeclaredInputs(intent="old"))
    monkeypatch.chdir(project)
    (project / "intent.txt").write_text("from a file", encoding="utf-8")
    assert main(["refresh", "shop:payment", "--intent-file", "intent.txt"]) == EXIT_OK
    assert "レビューを更新しました" in capsys.readouterr().out
    assert load_review(resolve_review("shop:payment")).declaration.intent == "from a file"


def test_cli_refresh_rejects_both_intent_options(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "payment", DeclaredInputs(intent="old"))
    with pytest.raises(SystemExit) as raised:
        main(["refresh", "shop:payment", "--intent", "a", "--intent-file", "b"])
    assert raised.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def _other_repository(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "b.py").write_text(PUBLIC_SOURCE, encoding="utf-8")
    git(root, "init", "-b", "main")
    return root


@pytest.mark.usefixtures("project")
def test_new_files_roots_at_the_repository_of_the_given_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = _other_repository(tmp_path, "mall")
    assert main(["new", "files", str(other / "lib"), "--name", "p", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "local/mall:p"
    assert data["paths"] == ["lib/b.py"]
    saved = load_review(ReviewId("local", "mall", "p", ReviewKind.FEATURE))
    assert saved.repository_root == str(other.resolve())


def test_new_files_resolves_relative_paths_against_the_working_directory(
    project: Path, tmp_path: Path
) -> None:
    other = _other_repository(tmp_path, "mall")
    review = create_feature_review(project, [f"../{other.name}/lib"], "p")
    assert str(review.id) == "local/mall:p"
    assert review.paths == ["lib/b.py"]


def test_new_files_rejects_paths_spanning_repositories(project: Path, tmp_path: Path) -> None:
    mall = _other_repository(tmp_path, "mall")
    depot = _other_repository(tmp_path, "depot")
    with pytest.raises(ReviewError, match="複数のリポジトリ"):
        create_feature_review(project, [str(mall / "lib"), str(depot / "lib")], "p")


def test_new_files_rejects_paths_mixing_inside_and_outside_git(
    project: Path, tmp_path: Path
) -> None:
    mall = _other_repository(tmp_path, "mall")
    with pytest.raises(ReviewError, match="中と外"):
        create_feature_review(project, [str(mall / "lib"), "src"], "p")


def test_new_files_outside_git_roots_at_the_working_directory(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert review.repository_root == str(project.resolve())
    assert review.paths == ["src/a.py"]


def test_new_files_rejects_a_path_outside_the_working_directory_when_not_in_git(
    project: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "c.py").write_text(PUBLIC_SOURCE, encoding="utf-8")
    with pytest.raises(ReviewError, match="リポジトリの外"):
        create_feature_review(project, [str(bare / "c.py")], "p")
