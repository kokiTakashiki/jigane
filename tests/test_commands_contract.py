"""docs/commands.md が約束する `list` の出力の形と、レビューの名前の規則を確かめる。"""

import json
import shutil
from pathlib import Path

import pytest
from pr_support import commit_all
from review_fakes import save_review_of_kind

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.reviews.catalog import list_review_summaries
from jigane.storage.store import ReviewId, ReviewKind

_NEW_COMMANDS = {
    "range": ["new", "range", "main..feature"],
    "files": ["new", "files", "cart.py"],
    "design-doc": ["new", "design-doc", "docs/shop-design.md"],
}


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "shop-design.md").write_text("# 題\n\n`Cart` を持つ。\n", encoding="utf-8")
    commit_all(root, "add files")
    monkeypatch.chdir(root)
    return root


_KINDS = {
    "range": ReviewKind.RANGE,
    "files": ReviewKind.FEATURE,
    "design-doc": ReviewKind.DESIGN_DOC,
}


def _create(kind: str, name: str) -> tuple[bool, int]:
    """作れたかと終了コードを返す。作れたかは、終了コードとレビューの数の両方で見る。"""
    before = len(list_review_summaries())
    code = main([*_NEW_COMMANDS[kind], f"--name={name}"])
    created = code == EXIT_OK and len(list_review_summaries()) == before + 1
    return created, code


@pytest.mark.parametrize("kind", sorted(_NEW_COMMANDS))
@pytest.mark.parametrize("name", ["a", "_a", "A9", "fix_1.2-a"])
def test_new_accepts_documented_names(repo: Path, kind: str, name: str) -> None:
    del repo
    assert _create(kind, name)[0]


@pytest.mark.parametrize("kind", sorted(_NEW_COMMANDS))
@pytest.mark.parametrize("name", [".hidden", "-x", "..", "a b", "a/b", "あ"])
def test_new_rejects_undocumented_names_with_the_invalid_name_reason(
    repo: Path, capsys: pytest.CaptureFixture[str], kind: str, name: str
) -> None:
    del repo
    created, code = _create(kind, name)
    assert not created
    assert code == EXIT_PROCESSING_FAILURE
    assert f"{_KINDS[kind].label}の名前が不正です: {name!r}" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("kind", "digits_only_is_accepted"),
    [("range", True), ("design-doc", True), ("files", False)],
)
def test_new_digits_only_name_is_rejected_only_where_it_meets_a_pull_request_number(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    *,
    digits_only_is_accepted: bool,
) -> None:
    del repo
    created, code = _create(kind, "123")
    assert created is digits_only_is_accepted
    if not digits_only_is_accepted:
        assert code == EXIT_PROCESSING_FAILURE
        # 文の枠は実装の文字列リテラルで定数が無いため、利用者への約束として固定で確かめる
        expected = (
            f"{ReviewKind.FEATURE.label}の名前を '123' にはできません"
            f"（{ReviewKind.PULL_REQUEST.label}の番号と保存先が重なります）"
        )
        assert expected in capsys.readouterr().err


def test_list_json_id_has_the_separator_of_each_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    monkeypatch.chdir(root)
    separators = {
        ReviewKind.PULL_REQUEST: "#",
        ReviewKind.RANGE: "@",
        ReviewKind.FEATURE: ":",
        ReviewKind.DESIGN_DOC: "+",
    }
    keys = {kind: ("5" if separators[kind] == "#" else "k") for kind in separators}
    saved = {kind: save_review_of_kind(root, keys[kind], kind) for kind in separators}
    assert main(["archive", str(saved[ReviewKind.FEATURE])]) == EXIT_OK
    capsys.readouterr()

    def listed(*options: str) -> set[str]:
        assert main(["list", *options, "--json"]) == EXIT_OK
        return {row["id"] for row in json.loads(capsys.readouterr().out)}

    expected = {f"local/shop{separators[kind]}{keys[kind]}" for kind in separators}
    archived = f"local/shop:{keys[ReviewKind.FEATURE]}"
    assert listed() == expected - {archived}
    assert listed("--archived") == {archived}
    for review_id in expected:
        assert str(ReviewId.parse(review_id)) == review_id
