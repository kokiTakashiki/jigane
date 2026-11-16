import pytest

from jigane.text.document_files import is_document_path
from jigane.text.path_suffix import suffix_of


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/A.PY", ".py"),
        ("src\\app\\a.py", ".py"),
        ("src/Makefile", ""),
        ("archive.tar.gz", ".gz"),
        ("docs/.gitignore", ""),
        ("docs\\.gitignore", ""),
    ],
)
def test_suffix_of_returns_lowercase_last_suffix(path: str, expected: str) -> None:
    assert suffix_of(path) == expected


def test_is_document_path_accepts_backslash_separated_path() -> None:
    assert is_document_path("docs\\guide\\README.MD")
    assert not is_document_path("docs\\guide\\main.py")
    assert not is_document_path("docs\\.md")
