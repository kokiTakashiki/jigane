import importlib.metadata
from collections.abc import Callable

import pytest

from jigane import commands
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.public_urls import installed_version, terms_urls
from jigane.commands.terms_consent import terms_notice
from jigane.languages import fetch_main


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake() -> fetch_main.FetchOutcome:
        calls.append("fetch")
        return fetch_main.Fetched((), ())

    monkeypatch.setattr(fetch_main, "fetch_and_report", fake)
    return calls


def _forbidden(prompt: str) -> str:
    raise AssertionError(prompt)


def _answering(answer: str, prompts: list[str]) -> Callable[[str], str]:
    def read(prompt: str) -> str:
        prompts.append(prompt)
        return answer

    return read


def test_terminal_yes_shows_the_terms_and_fetches(
    downloads: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    prompts: list[str] = []
    code = commands.main(
        ["languages", "fetch"], is_terminal=lambda: True, read_answer=_answering("y", prompts)
    )
    assert code == EXIT_OK
    assert downloads == ["fetch"]
    assert len(prompts) == 1
    err = capsys.readouterr().err
    urls = terms_urls(installed_version())
    for url in (urls.terms_ja, urls.terms_en, urls.privacy_ja, urls.privacy_en):
        assert url in err


def test_terminal_no_does_not_fetch(
    downloads: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = commands.main(
        ["languages", "fetch"], is_terminal=lambda: True, read_answer=_answering("n", [])
    )
    assert code != EXIT_OK
    assert downloads == []
    assert "規約への同意が得られなかった" in capsys.readouterr().err


def test_non_terminal_with_accept_terms_fetches_without_asking(downloads: list[str]) -> None:
    code = commands.main(
        ["languages", "fetch", "--accept-terms"],
        is_terminal=lambda: False,
        read_answer=_forbidden,
    )
    assert code == EXIT_OK
    assert downloads == ["fetch"]


def test_non_terminal_without_accept_terms_fails_and_does_not_fetch(
    downloads: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = commands.main(["languages", "fetch"], is_terminal=lambda: False, read_answer=_forbidden)
    assert code != EXIT_OK
    assert downloads == []
    assert "--accept-terms" in capsys.readouterr().err


def test_terminal_uppercase_yes_fetches(downloads: list[str]) -> None:
    code = commands.main(
        ["languages", "fetch"], is_terminal=lambda: True, read_answer=_answering(" YES ", [])
    )
    assert code == EXIT_OK
    assert downloads == ["fetch"]


def test_terminal_empty_answer_does_not_fetch(downloads: list[str]) -> None:
    code = commands.main(
        ["languages", "fetch"], is_terminal=lambda: True, read_answer=_answering("", [])
    )
    assert code != EXIT_OK
    assert downloads == []


def test_terminal_with_accept_terms_fetches_without_asking(downloads: list[str]) -> None:
    code = commands.main(
        ["languages", "fetch", "--accept-terms"],
        is_terminal=lambda: True,
        read_answer=_forbidden,
    )
    assert code == EXIT_OK
    assert downloads == ["fetch"]


def test_declined_failure_uses_the_terms_wording(
    downloads: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = commands.main(
        ["languages", "fetch"], is_terminal=lambda: True, read_answer=_answering("n", [])
    )
    assert code != EXIT_OK
    assert downloads == []
    err = capsys.readouterr().err
    assert "取得しませんでした" in err
    assert "jigane languages fetch" in err
    assert "実行しませんでした" not in err


def _all_urls(version: str | None) -> list[str]:
    urls = terms_urls(version)
    return [urls.terms_ja, urls.terms_en, urls.privacy_ja, urls.privacy_en]


@pytest.mark.parametrize("version", ["1.2.3", "0.1.0", "1.2.3.post1"])
def test_release_version_points_at_its_tag(version: str) -> None:
    for url, name in zip(
        _all_urls(version),
        ["TERMS.ja.md", "TERMS.en.md", "PRIVACY.ja.md", "PRIVACY.en.md"],
        strict=True,
    ):
        assert url == f"https://github.com/kokiTakashiki/jigane/blob/v{version}/{name}"


@pytest.mark.parametrize("version", ["1.2.3.dev1", "1.2.3rc1", "1.2.3a1", "1.2.3+local", None])
def test_non_release_version_points_at_main(version: str | None) -> None:
    for url, name in zip(
        _all_urls(version),
        ["TERMS.ja.md", "TERMS.en.md", "PRIVACY.ja.md", "PRIVACY.en.md"],
        strict=True,
    ):
        assert url == f"https://github.com/kokiTakashiki/jigane/blob/main/{name}"


def test_notice_uses_the_injected_version() -> None:
    assert all(url in "\n".join(terms_notice("1.2.3")) for url in _all_urls("1.2.3"))
    assert all(url in "\n".join(terms_notice(None)) for url in _all_urls(None))


def test_installed_version_is_none_without_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert installed_version() is None
