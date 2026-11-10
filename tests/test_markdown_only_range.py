import shutil
from pathlib import Path

import pytest
from fakes import MacroFake, discovered
from pr_support import commit_all, git

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.views.findings import NO_DOCUMENT_BODY_LINE
from jigane.reviews.lifecycle import create_range_review
from jigane.text.document_files import (
    DOCUMENT_ONLY_NOTICE,
    all_documents,
)


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    monkeypatch.chdir(root)
    return root


def _branch(root: Path, name: str, files: dict[str, str]) -> str:
    git(root, "switch", "-c", name, "main")
    for path, text in files.items():
        (root / path).write_text(text, encoding="utf-8")
    commit_all(root, f"change {name}\n\nbody of {name}")
    return f"main..{name}"


def test_all_documents_rejects_empty_and_code() -> None:
    assert all_documents(["a.md", "docs/b.RST", "c.txt"])
    assert not all_documents([])
    assert not all_documents(["a.md", "b.py"])


def test_range_of_documents_only_warns(repo: Path) -> None:
    range_text = _branch(repo, "docs", {"notes.md": "# n\n", "more.txt": "x\n"})
    review = create_range_review(repo, range_text, "docs")
    assert DOCUMENT_ONLY_NOTICE in review.warnings


def test_range_deleting_code_and_changing_a_document_is_documents_only_everywhere(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo, "switch", "-c", "deleting", "main")
    (repo / "edit.py").unlink()
    (repo / "notes.md").write_text("# n\n", encoding="utf-8")
    commit_all(repo, "delete code\n\nbody of deleting")
    review = create_range_review(repo, "main..deleting", "deleting")
    assert "edit.py" not in review.paths
    assert DOCUMENT_ONLY_NOTICE in review.warnings
    capsys.readouterr()
    assert main(["run", "octo/hello@deleting", "macro"], invoke=MacroFake([])) == EXIT_OK
    captured = capsys.readouterr()
    assert DOCUMENT_ONLY_NOTICE in captured.err
    assert NO_DOCUMENT_BODY_LINE in captured.out
    assert main(["show", "octo/hello@deleting", "macro"]) == EXIT_OK
    assert NO_DOCUMENT_BODY_LINE in capsys.readouterr().out


def test_range_with_one_code_file_does_not_warn(repo: Path) -> None:
    range_text = _branch(repo, "mixed", {"notes.md": "# n\n", "code.py": "y = 1\n"})
    review = create_range_review(repo, range_text, "mixed")
    assert DOCUMENT_ONLY_NOTICE not in review.warnings


def test_run_notice_and_zero_line_for_documents_only(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    range_text = _branch(repo, "docs", {"notes.md": "# n\n"})
    create_range_review(repo, range_text, "docs")
    capsys.readouterr()
    assert main(["run", "octo/hello@docs", "macro"], invoke=MacroFake([])) == EXIT_OK
    captured = capsys.readouterr()
    assert DOCUMENT_ONLY_NOTICE in captured.err
    assert NO_DOCUMENT_BODY_LINE in captured.out
    assert main(["show", "octo/hello@docs", "macro"]) == EXIT_OK
    assert NO_DOCUMENT_BODY_LINE in capsys.readouterr().out


def test_run_with_findings_omits_zero_line_but_keeps_notice(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    range_text = _branch(repo, "docs", {"notes.md": "# n\n"})
    create_range_review(repo, range_text, "docs")
    capsys.readouterr()
    assert (
        main(["run", "octo/hello@docs", "macro"], invoke=MacroFake([discovered("M2")])) == EXIT_OK
    )
    captured = capsys.readouterr()
    assert DOCUMENT_ONLY_NOTICE in captured.err
    assert NO_DOCUMENT_BODY_LINE not in captured.out
    assert main(["show", "octo/hello@docs", "macro"]) == EXIT_OK
    assert NO_DOCUMENT_BODY_LINE not in capsys.readouterr().out


def test_run_with_code_omits_notice_and_zero_line(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    range_text = _branch(repo, "mixed", {"notes.md": "# n\n", "code.py": "y = 1\n"})
    create_range_review(repo, range_text, "mixed")
    capsys.readouterr()
    assert main(["run", "octo/hello@mixed", "macro"], invoke=MacroFake([])) == EXIT_OK
    captured = capsys.readouterr()
    assert DOCUMENT_ONLY_NOTICE not in captured.err
    assert NO_DOCUMENT_BODY_LINE not in captured.out
    assert main(["show", "octo/hello@mixed", "macro"]) == EXIT_OK
    assert NO_DOCUMENT_BODY_LINE not in capsys.readouterr().out
