from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jigane.findings.records import _read_history, read_stored_state
from jigane.flaky.gates import load_gates
from jigane.pull_request_context.background import load_background
from jigane.pull_request_context.bots import read_bot_comments
from jigane.reviews.selection import load_selection
from jigane.storage.review_error import ReviewError
from jigane.storage.symlink_guard import StoredFileSymlinkError


def _link(directory: Path, name: str) -> None:
    """保存先のファイルを、外にある存在しない先への symlink にする。"""
    (directory / name).symlink_to(directory.parent / "outside-missing.json")


@pytest.mark.parametrize(
    ("name", "reader"),
    [
        ("findings.json", read_stored_state),
        ("selection.json", load_selection),
        ("gates.json", load_gates),
        ("background.json", load_background),
    ],
)
def test_reader_refuses_a_symlinked_file(
    tmp_path: Path, name: str, reader: Callable[[Path], Any]
) -> None:
    directory = tmp_path / "review"
    directory.mkdir()
    _link(directory, name)
    with pytest.raises(StoredFileSymlinkError):
        reader(directory)


@pytest.mark.parametrize(
    ("name", "reader"),
    [
        ("findings.json", read_stored_state),
        ("selection.json", load_selection),
        ("gates.json", load_gates),
        ("background.json", load_background),
        ("bot_comments.json", read_bot_comments),
        ("findings_history.json", _read_history),
    ],
)
def test_reader_does_not_return_the_content_of_a_live_symlink_target(
    tmp_path: Path, name: str, reader: Callable[[Path], Any]
) -> None:
    directory = tmp_path / "review"
    directory.mkdir()
    target = tmp_path / "outside.json"
    target.write_text('{"secret": true}', encoding="utf-8")
    (directory / name).symlink_to(target)
    try:
        result = reader(directory)
    except (StoredFileSymlinkError, ReviewError):
        return
    assert "secret" not in repr(result)


def test_read_history_does_not_treat_a_symlink_as_missing(tmp_path: Path) -> None:
    directory = tmp_path / "review"
    directory.mkdir()
    _link(directory, "findings_history.json")
    with pytest.raises(StoredFileSymlinkError):
        _read_history(directory)


def test_read_bot_comments_turns_a_symlink_into_a_review_error(tmp_path: Path) -> None:
    directory = tmp_path / "review"
    directory.mkdir()
    _link(directory, "bot_comments.json")
    with pytest.raises(ReviewError):
        read_bot_comments(directory)
