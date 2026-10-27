import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from pr_support import git

ROOT = Path(__file__).resolve().parent.parent
NOREPLY = "1+octo@users.noreply.github.com"


def load_checker() -> ModuleType:
    path = ROOT / "scripts" / "check_commit_emails.py"
    spec = importlib.util.spec_from_file_location("check_commit_emails", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commit_as(repo: Path, name: str, author: str, committer: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "-A")
    # pr_support.git が先頭に置く user.email を、後ろの -c で委託者のアドレスに上書きする
    git(
        repo,
        "-c",
        f"user.email={committer}",
        "commit",
        "-m",
        name,
        f"--author=t <{author}>",
    )


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "work"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    return repo


def test_passes_when_every_address_is_noreply(tmp_path: Path) -> None:
    checker = load_checker()
    repo = make_repo(tmp_path)
    commit_as(repo, "a", NOREPLY, NOREPLY)
    commit_as(repo, "b", "octo@users.noreply.github.com", NOREPLY)
    assert checker.find_offenders(repo, "HEAD") == []
    assert checker.main(["--root", str(repo), "HEAD"]) == 0


def test_fails_when_an_author_address_is_personal(tmp_path: Path) -> None:
    checker = load_checker()
    repo = make_repo(tmp_path)
    commit_as(repo, "a", "me@example.com", NOREPLY)
    offenders = checker.find_offenders(repo, "HEAD")
    assert len(offenders) == 1
    assert offenders[0].endswith("作者: me@example.com")
    assert checker.main(["--root", str(repo), "HEAD"]) == 1


def test_fails_when_a_committer_address_is_personal(tmp_path: Path) -> None:
    checker = load_checker()
    repo = make_repo(tmp_path)
    commit_as(repo, "a", NOREPLY, "me@example.com")
    offenders = checker.find_offenders(repo, "HEAD")
    assert len(offenders) == 1
    assert offenders[0].endswith("委託者: me@example.com")


def test_only_the_given_range_is_checked(tmp_path: Path) -> None:
    checker = load_checker()
    repo = make_repo(tmp_path)
    commit_as(repo, "old", "me@example.com", "me@example.com")
    base = git(repo, "rev-parse", "HEAD")
    commit_as(repo, "new", NOREPLY, NOREPLY)
    assert checker.find_offenders(repo, f"{base}..HEAD") == []
    assert len(checker.find_offenders(repo, "HEAD")) == 2


@pytest.mark.parametrize(
    "address",
    ["evil@x.com#a@users.noreply.github.com", "a@b@users.noreply.github.com"],
)
def test_embedded_address_is_not_noreply(address: str) -> None:
    assert not load_checker().is_noreply(address)


@pytest.mark.parametrize(
    "address",
    ["12345+name@users.noreply.github.com", "name@users.noreply.github.com"],
)
def test_noreply_forms_are_accepted(address: str) -> None:
    assert load_checker().is_noreply(address)


@pytest.mark.parametrize(
    "address",
    [
        "@users.noreply.github.com",
        "12345+@users.noreply.github.com",
        "na me@users.noreply.github.com",
        "12345+na me@users.noreply.github.com",
        "abc+name@users.noreply.github.com",
        "+name@users.noreply.github.com",
    ],
)
def test_noreply_boundaries_are_rejected(address: str) -> None:
    assert not load_checker().is_noreply(address)


def test_lookalike_domain_is_not_noreply(tmp_path: Path) -> None:
    checker = load_checker()
    repo = make_repo(tmp_path)
    commit_as(repo, "a", "me@users.noreply.github.com.example.com", NOREPLY)
    assert len(checker.find_offenders(repo, "HEAD")) == 1


@pytest.mark.parametrize("arguments", [["--output={path}"], ["--", "--output={path}"]])
def test_range_starting_with_dash_is_rejected(tmp_path: Path, arguments: list[str]) -> None:
    checker = load_checker()
    repo = make_repo(tmp_path)
    commit_as(repo, "a", NOREPLY, NOREPLY)
    target = tmp_path / "written"
    with pytest.raises(SystemExit) as raised:
        checker.main(["--root", str(repo), *(a.format(path=target) for a in arguments)])
    assert raised.value.code == 2
    assert not target.exists()
