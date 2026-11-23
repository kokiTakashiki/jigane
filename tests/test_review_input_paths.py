import json
from pathlib import Path

import pytest
from test_mcp_guide import GUIDE, IOS_USE, NAMES
from test_repository_docs_reader import make_checkout
from test_security import SecurityFake, write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands.refresh import refresh_review
from jigane.git import tree_files
from jigane.groups.definitions import GroupId
from jigane.judgment_map.declaration import DECLARATION_TEXT_LIMIT
from jigane.repository_docs.kinds import DocumentKind, Launch
from jigane.repository_docs.reader import (
    BaseTree,
    Document,
    HeadOfCheckout,
    read_document,
    read_repository_docs,
)
from jigane.repository_docs.section import document_section
from jigane.repository_docs.skip_reason import Unreadable
from jigane.reviews.catalog import load_review
from jigane.reviews.lifecycle import DeclaredInputs, create_feature_review, read_intent
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput
from jigane.skills.mcp import McpSelection, SecurityConfigError

DUMMY_PRIVATE_TEXT = "レビュー入力に含めないダミー文"

pytestmark = pytest.mark.usefixtures("tree_walk")


@pytest.fixture(params=["native", "lstat"])
def tree_walk(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "lstat":
        monkeypatch.setattr(tree_files, "_CAN_WALK_BY_DIRECTORY", False)


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_checkout(tmp_path / "checkout", {"src/a.py": "a = 1\n"})
    monkeypatch.chdir(root)
    return root


@pytest.fixture(params=["leaf", "parent", "parent_then_dotdot"])
def linked_input(
    request: pytest.FixtureRequest, checkout: Path, tmp_path: Path
) -> tuple[Path, Path]:
    outside = tmp_path / "outside"
    (outside / "child").mkdir(parents=True)
    target = outside / "input.txt"
    if request.param == "leaf":
        link = checkout / target.name
        link.symlink_to(target)
        return link, target
    link = checkout / "linked"
    if request.param == "parent":
        link.symlink_to(outside, target_is_directory=True)
        return link / target.name, target
    link.symlink_to(outside / "child", target_is_directory=True)
    return link / ".." / target.name, target


def test_intent_link_is_rejected_before_creating_or_refreshing(
    checkout: Path, linked_input: tuple[Path, Path]
) -> None:
    path, target = linked_input
    target.write_text(DUMMY_PRIVATE_TEXT, encoding="utf-8")
    review = create_feature_review(checkout, ["src"], "kept", DeclaredInputs(intent="通常の意図"))

    with pytest.raises(OSError, match="シンボリックリンク"):
        create_feature_review(checkout, ["src"], "refused", DeclaredInputs(intent_file=path))
    # refresh も、保存先の repo ではなく呼び出し時の作業ディレクトリが相対指定の基準。
    with pytest.raises(OSError, match="シンボリックリンク"):
        refresh_review(review.id, DeclaredInputs(intent_file=path.relative_to(checkout)))

    prompt = macro.build_diagram_prompt(AssemblyInput.load(load_review(review.id)))
    assert "通常の意図" in prompt
    assert DUMMY_PRIVATE_TEXT not in prompt


def test_intent_allows_an_explicit_file_and_a_trusted_linked_root(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    trusted = tmp_path / "trusted"
    trusted.symlink_to(actual, target_is_directory=True)
    inside = actual / "intent.txt"
    inside.write_bytes("一行目\r\n二行目\r三行目".encode())
    outside = tmp_path / "outside.txt"
    outside.write_text("木外の意図", encoding="utf-8")

    relative = read_intent(trusted, None, Path(inside.name))
    absolute = read_intent(trusted, None, outside)
    assert relative is not None
    assert relative.intent == "一行目\n二行目\n三行目"
    assert absolute is not None
    assert absolute.intent == "木外の意図"


@pytest.mark.parametrize("intent_path", ["intent.txt", "src/../intent.txt"])
def test_intent_file_keeps_the_declaration_limit(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_path: str
) -> None:
    text = "意図\n" + "a" * tree_files.MAX_REVIEW_FILE_BYTES
    (checkout / "intent.txt").write_text(text, encoding="utf-8")
    (tmp_path / "outside.txt").write_text("木外の意図", encoding="utf-8")

    review = create_feature_review(
        checkout, ["src"], "kept", DeclaredInputs(intent_file=Path(intent_path))
    )
    assert review.declaration.intent == text[:DECLARATION_TEXT_LIMIT]
    assert len(review.declaration.warnings) == 1
    monkeypatch.chdir(checkout / "src")
    result = refresh_review(review.id, DeclaredInputs(intent_file=Path("../../outside.txt")))
    assert result.review.declaration.intent == "木外の意図"


@pytest.mark.parametrize("use_head", [False, True], ids=["base", "head"])
def test_repository_doc_link_is_omitted_from_the_prompt(
    tmp_path: Path,
    linked_input: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    *,
    use_head: bool,
) -> None:
    path, target = linked_input
    target.write_text(DUMMY_PRIVATE_TEXT, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / "repository-docs.toml").write_text(
        f"development_docs_path = {json.dumps(path.as_posix(), ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    base = tmp_path / "base"
    base.mkdir()

    docs = read_repository_docs(
        [GroupId.MACRO], "a", "b", HeadOfCheckout() if use_head else BaseTree(base)
    )

    assert not docs.given
    assert len(docs.skipped) == 1
    if ".." in path.parts:
        assert isinstance(docs.skipped[0][1], Unreadable)
    assert "警告" in capsys.readouterr().err
    assert DUMMY_PRIVATE_TEXT not in document_section(docs, Launch.POLICY_PRINCIPLE)


def test_repository_doc_uses_original_checkout_when_parent_links_to_another(
    tmp_path: Path,
) -> None:
    root = make_checkout(tmp_path / "work", {"docs/guide.md": "元の HEAD の文書"})
    other = make_checkout(tmp_path / "other", {"guide.md": DUMMY_PRIVATE_TEXT})
    (root / "docs" / "guide.md").unlink()
    (root / "docs").rmdir()
    (root / "docs").symlink_to(other, target_is_directory=True)

    result = read_document(
        DocumentKind.DEVELOPMENT, str(root / "docs" / "guide.md"), "a", "b", HeadOfCheckout()
    )

    assert isinstance(result, Document)
    assert result.text == "元の HEAD の文書"


@pytest.mark.parametrize("source", ["base", "head", "outside"])
def test_repository_doc_parent_paths_preserve_the_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
) -> None:
    checkout = make_checkout(tmp_path / "work", {"docs/guide.md": "HEAD の文書"})
    monkeypatch.chdir(checkout)
    base = tmp_path / "base"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "guide.md").write_text("基底の文書", encoding="utf-8")
    (checkout / "docs" / "guide.md").write_text("作業中の文書", encoding="utf-8")
    (tmp_path / "guide.md").write_text("木外の文書", encoding="utf-8")
    path = checkout / "docs" / ".." / "docs" / "guide.md"
    if source == "outside":
        path = checkout / ".." / "guide.md"
    expected = {"base": "基底の文書", "head": "HEAD の文書", "outside": "木外の文書"}
    home = tmp_path / "home"
    home.mkdir()
    (home / "repository-docs.toml").write_text(
        f"development_docs_path = {json.dumps(path.as_posix(), ensure_ascii=False)}\n",
        encoding="utf-8",
    )

    docs = read_repository_docs(
        [GroupId.MACRO], "a", "b", HeadOfCheckout() if source == "head" else BaseTree(base)
    )

    assert docs.skipped == ()
    assert [doc.text for doc in docs.given] == [expected[source]]
    assert capsys.readouterr().err == ""


@pytest.mark.usefixtures("selected_agent")
def test_mcp_guide_link_is_rejected_before_invoking(
    checkout: Path,
    linked_input: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, target = linked_input
    target.write_text(GUIDE.replace(IOS_USE, DUMMY_PRIVATE_TEXT), encoding="utf-8")
    review = create_feature_review(checkout, ["src"], "kept")
    write_mcp()
    fake = SecurityFake({})

    assert (
        main(
            ["run", str(review.id), "security", "--mcps", *NAMES, "--mcp-guide", str(path)],
            invoke=fake,
        )
        == EXIT_PROCESSING_FAILURE
    )
    assert not fake.calls
    assert "シンボリックリンク" in capsys.readouterr().err
    with pytest.raises(SecurityConfigError, match="シンボリックリンク"):
        McpSelection.load(NAMES, path.relative_to(checkout), codex_cli=True)


@pytest.mark.usefixtures("checkout")
def test_mcp_guide_allows_an_explicit_regular_file(tmp_path: Path) -> None:
    guide = tmp_path / "outside.toml"
    guide.write_text(GUIDE, encoding="utf-8")
    write_mcp()

    assert McpSelection.load(NAMES, guide).servers[0].use_for == IOS_USE


@pytest.mark.parametrize("guide_path", ["guide.toml", "src/../guide.toml"])
def test_mcp_guide_allows_a_large_regular_file(checkout: Path, guide_path: str) -> None:
    guide = checkout / "guide.toml"
    guide.write_text(f"#{'a' * tree_files.MAX_REVIEW_FILE_BYTES}\n{GUIDE}", encoding="utf-8")
    write_mcp()

    assert McpSelection.load(NAMES, Path(guide_path)).servers[0].use_for == IOS_USE
