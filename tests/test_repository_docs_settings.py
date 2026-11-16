from pathlib import Path

import pytest

from jigane.repository_docs.kinds import DocumentKind
from jigane.repository_docs.settings import (
    ConfiguredPath,
    RepositoryDocsError,
    load_settings,
    parse_settings,
)

BOTH = """
development_docs_path = "/d/default.md"

[repositories."Example-Org/Example-Repo"]
development_docs_path = "/d/repo.md"
security_docs_path = "/s/repo.md"
"""
DEFAULT = 'development_docs_path = "/d/default.md"\n'


def test_repository_table_wins_and_lookup_ignores_case() -> None:
    found = parse_settings(BOTH).resolve("example-org", "example-repo")

    assert found[DocumentKind.DEVELOPMENT] == ConfiguredPath(
        "/d/repo.md", '[repositories."example-org/example-repo"] の development_docs_path'
    )
    assert found[DocumentKind.SECURITY].value == "/s/repo.md"


def test_default_fills_only_missing_keys() -> None:
    text = DEFAULT + '[repositories."a/b"]\nsecurity_docs_path = "/s.md"\n'
    found = parse_settings(text).resolve("a", "b")

    assert found[DocumentKind.DEVELOPMENT] == ConfiguredPath(
        "/d/default.md", "最上位の development_docs_path"
    )
    assert found[DocumentKind.SECURITY].value == "/s.md"


def test_empty_string_falls_through_to_default_and_then_unspecified() -> None:
    text = DEFAULT + '[repositories."a/b"]\ndevelopment_docs_path = ""\nsecurity_docs_path = ""\n'
    found = parse_settings(text).resolve("a", "b")

    assert found[DocumentKind.DEVELOPMENT].value == "/d/default.md"
    assert DocumentKind.SECURITY not in found


def test_other_repository_gets_only_the_default() -> None:
    assert list(parse_settings(BOTH).resolve("x", "y")) == [DocumentKind.DEVELOPMENT]


@pytest.mark.parametrize(
    "text",
    [
        "this is not toml",
        'unknown_key = "x"',
        "development_docs_path = 1",
        '[unknown_table]\nx = "y"',
        '[repositories."no-slash"]\ndevelopment_docs_path = "/x"',
        '[repositories."a/b"]\nunknown = "x"',
        '[repositories."a/b"]\ndevelopment_docs_path = ["/x"]',
        (
            '[repositories."A/B"]\ndevelopment_docs_path = "/x"\n'
            '[repositories."a/b"]\ndevelopment_docs_path = "/y"'
        ),
        'repositories = "x"',
    ],
)
def test_broken_settings_raise(text: str) -> None:
    with pytest.raises(RepositoryDocsError):
        parse_settings(text)


def test_missing_file_means_nothing_specified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))

    assert load_settings().resolve("a", "b") == {}


def test_file_in_data_home_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    (tmp_path / "repository-docs.toml").write_text(BOTH, encoding="utf-8")

    found = load_settings().resolve("example-org", "example-repo")

    assert found[DocumentKind.SECURITY].value == "/s/repo.md"


def test_unreadable_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    (tmp_path / "repository-docs.toml").write_bytes(b"\xff\xfe")

    with pytest.raises(RepositoryDocsError):
        load_settings()


def test_too_deep_toml_raises_repository_docs_error() -> None:
    with pytest.raises(RepositoryDocsError, match="TOML として読めません"):
        parse_settings("a = " + "[" * 5000 + "]" * 5000)
