import os
import stat
from pathlib import Path

import pytest

from jigane.git import process
from jigane.groups.definitions import GroupId
from jigane.repository_docs.kinds import DocumentKind
from jigane.repository_docs.reader import (
    BaseSource,
    BaseTree,
    Document,
    HeadOfCheckout,
    _open_and_read,
    _read_base_tree,
    read_document,
    read_repository_docs,
)
from jigane.repository_docs.settings import RepositoryDocsError
from jigane.repository_docs.skip_reason import (
    MAX_DOCUMENT_BYTES,
    HasNul,
    LinkInBase,
    Missing,
    NotAbsolute,
    NotInBase,
    NotRegularFile,
    NotUtf8,
    SecretName,
    TooLarge,
    Unreadable,
)

DEV = DocumentKind.DEVELOPMENT
HEAD = HeadOfCheckout()


def git(root: Path, *arguments: str) -> None:
    identity = [
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "-c",
        "commit.gpgsign=false",
    ]
    process.run_command(["git", *identity, *arguments], cwd=root)


def make_checkout(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "remote", "add", "origin", "https://github.com/a/b.git")
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def read(path: Path | str, base: BaseSource = HEAD) -> Document | object:
    return read_document(DEV, str(path), "a", "b", base)


def test_head_of_checkout_reads_committed_content_not_work_tree(tmp_path: Path) -> None:
    root = make_checkout(tmp_path / "work", {"docs/a.md": "committed"})
    (root / "docs" / "a.md").write_text("changed", encoding="utf-8")

    result = read(root / "docs" / "a.md")

    assert isinstance(result, Document)
    assert (result.text, result.size_bytes) == ("committed", 9)
    assert len(result.sha256) == 64


def test_head_reports_untracked_file_as_not_in_base(tmp_path: Path) -> None:
    root = make_checkout(tmp_path / "work", {"x.md": "x"})
    (root / "new.md").write_text("new", encoding="utf-8")

    assert read(root / "new.md") == NotInBase()


def test_head_reports_link_and_directory(tmp_path: Path) -> None:
    root = make_checkout(tmp_path / "work", {"docs/b.md": "b"})
    (root / "a.md").symlink_to("docs/b.md")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "link")
    (root / "a.md").unlink()
    (root / "a.md").write_text("regular now", encoding="utf-8")

    assert read(root / "a.md") == LinkInBase()
    assert read(root / "docs") == NotRegularFile()


def test_head_applies_the_size_limit(tmp_path: Path) -> None:
    root = make_checkout(
        tmp_path / "work",
        {"ok.md": "a" * MAX_DOCUMENT_BYTES, "big.md": "a" * (MAX_DOCUMENT_BYTES + 1)},
    )

    assert isinstance(read(root / "ok.md"), Document)
    assert read(root / "big.md") == TooLarge(MAX_DOCUMENT_BYTES + 1)


def test_base_tree_reads_base_content_for_a_path_in_the_checkout(tmp_path: Path) -> None:
    root = make_checkout(tmp_path / "work", {"docs/a.md": "work"})
    base = tmp_path / "base"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "a.md").write_text("base", encoding="utf-8")

    result = read(root / "docs" / "a.md", BaseTree(base))

    assert isinstance(result, Document)
    assert result.text == "base"
    assert read(root / "docs" / "other.md", BaseTree(base)) == NotInBase()


def test_base_tree_refuses_a_link(tmp_path: Path) -> None:
    root = make_checkout(tmp_path / "work", {"a.md": "work"})
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "elsewhere.md").write_text("x", encoding="utf-8")
    (base / "a.md").symlink_to(tmp_path / "elsewhere.md")

    assert read(root / "a.md", BaseTree(base)) == LinkInBase()


def test_outside_path_is_read_as_written(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    path = tmp_path / "plain" / "guide.md"
    path.write_text("outside", encoding="utf-8")

    result = read(path)

    assert isinstance(result, Document)
    assert result.text == "outside"


def test_outside_failures(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "bin.md").write_bytes(b"\xff\xfe")
    (folder / "nul.md").write_bytes(b"a\x00b")
    (folder / ".env").write_text("SECRET=1", encoding="utf-8")
    (folder / "guide.md").symlink_to(folder / ".env")
    (folder / "ok.md").write_text("a" * MAX_DOCUMENT_BYTES, encoding="utf-8")
    (folder / "big.md").write_text("a" * (MAX_DOCUMENT_BYTES + 1), encoding="utf-8")

    assert read("relative/guide.md") == NotAbsolute()
    assert read(folder / "none.md") == Missing()
    assert read(folder) == NotRegularFile()
    assert read(folder / "bin.md") == NotUtf8()
    assert read(folder / "nul.md") == HasNul()
    assert read(folder / ".env") == SecretName()
    assert read(folder / "guide.md") == SecretName()
    assert isinstance(read(folder / "ok.md"), Document)
    assert read(folder / "big.md") == TooLarge(MAX_DOCUMENT_BYTES + 1)


def test_directory_is_not_regular_file_even_if_open_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "guide.md").write_text("x", encoding="utf-8")

    def deny(*_arguments: object, **_keywords: object) -> int:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", deny)

    assert read(folder) == NotRegularFile()
    assert read(folder / "guide.md") == Unreadable("Permission denied")
    assert read(folder / "none.md") == Missing()


def test_file_swapped_for_non_regular_after_stat_is_not_regular_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "guide.md"
    path.write_text("x", encoding="utf-8")
    real_fstat = os.fstat

    def swapped(descriptor: int) -> os.stat_result:
        values = list(real_fstat(descriptor))
        values[0] = stat.S_IFIFO | 0o600
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", swapped)

    assert _open_and_read(path) == NotRegularFile()


def test_warning_is_one_line_per_kind_without_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    secret_path = tmp_path / "private-user-name" / "x.md"
    (home / "repository-docs.toml").write_text(
        f'[repositories."a/b"]\ndevelopment_docs_path = "{secret_path.as_posix()}"\n'
        f'security_docs_path = "{secret_path.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("JIGANE_HOME", str(home))

    docs = read_repository_docs([GroupId.SECURITY], "a", "b", HEAD)
    security_lines = capsys.readouterr().err.splitlines()
    read_repository_docs([GroupId.MACRO], "a", "b", HEAD)
    macro_lines = capsys.readouterr().err.splitlines()

    assert [kind for kind, _ in docs.skipped] == [DEV, DocumentKind.SECURITY]
    assert len(security_lines) == 2
    assert len(macro_lines) == 1
    assert "リポジトリのセキュリティ指針" in security_lines[1]
    assert "private-user-name" not in "".join(security_lines)
    assert 'repositories."a/b"' in macro_lines[0]


def test_given_documents_and_settings_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    guide = tmp_path / "guide.md"
    guide.write_text("rules", encoding="utf-8")
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    settings = tmp_path / "repository-docs.toml"
    settings.write_text(f'development_docs_path = "{guide.as_posix()}"\n', encoding="utf-8")

    docs = read_repository_docs([GroupId.MICRO], "a", "b", HEAD)

    assert [d.kind for d in docs.given] == [DEV]
    assert capsys.readouterr().err == ""
    settings.write_text("broken = [", encoding="utf-8")
    with pytest.raises(RepositoryDocsError):
        read_repository_docs([GroupId.MICRO], "a", "b", HEAD)


def test_head_without_any_commit_is_not_in_base(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "remote", "add", "origin", "https://github.com/a/b.git")
    (root / "a.md").write_text("x", encoding="utf-8")

    assert read(root / "a.md") == NotInBase()


def test_head_reports_a_submodule_entry_as_not_a_regular_file(tmp_path: Path) -> None:
    root = make_checkout(tmp_path / "work", {"x.md": "x"})
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},sub")
    git(root, "commit", "-q", "-m", "gitlink")

    assert read(root / "sub") == NotRegularFile()


def test_os_error_while_opening_reports_the_os_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "guide.md"
    path.write_text("x", encoding="utf-8")

    def denied(*_args: object, **_kwargs: object) -> int:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("jigane.repository_docs.reader.os.open", denied)

    assert read(path) == Unreadable("Permission denied")


def test_os_error_while_reading_reports_the_os_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "guide.md"
    path.write_text("x", encoding="utf-8")

    def broken(_descriptor: int) -> os.stat_result:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr("jigane.repository_docs.reader.os.fstat", broken)

    assert read(path) == Unreadable("Input/output error")


def test_read_base_tree_maps_each_tree_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dir").mkdir()
    (tmp_path / "big.md").write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 100))
    (tmp_path / "ok.md").write_text("ok", encoding="utf-8")
    assert _read_base_tree(tmp_path, "dir") == NotRegularFile()
    assert _read_base_tree(tmp_path, "none.md") == NotInBase()
    assert _read_base_tree(tmp_path, "big.md") == TooLarge(MAX_DOCUMENT_BYTES + 1)
    assert _read_base_tree(tmp_path, "ok.md") == b"ok"

    def denied(*_args: object, **_kwargs: object) -> int:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", denied)
    assert _read_base_tree(tmp_path, "ok.md") == Unreadable("Permission denied")
