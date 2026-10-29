"""SARIF の URI をリポジトリの相対パスに直す規則の試験。"""

from pathlib import Path

import pytest
from sarif_support import log, result, run, write

from jigane.static_analysis.models import DropReason
from jigane.static_analysis.paths import resolve_uri
from jigane.static_analysis.reader import read_sarif

REPOSITORY = Path("/work/repo")


def resolve(uri: str, analysis_root: Path = REPOSITORY) -> str | DropReason:
    return resolve_uri(uri, analysis_root, REPOSITORY)


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("src/a.py", "src/a.py"),
        ("./src/../src/a.py", "src/a.py"),
        ("src//a.py", "src/a.py"),
        ("src\\sub\\a.py", "src/sub/a.py"),
        ("src/a%20b.py", "src/a b.py"),
        ("src/%E3%81%82.py", "src/あ.py"),
        ("src/a.py?x=1#L3", "src/a.py"),
        ("file:///work/repo/src/a.py", "src/a.py"),
        ("file:/work/repo/src/a.py", "src/a.py"),
        ("FILE:///work/repo/src/a%20b.py", "src/a b.py"),
        ("file:///work/repo/x/../src/a.py", "src/a.py"),
    ],
)
def test_resolves_to_repository_relative_path(uri: str, expected: str) -> None:
    assert resolve(uri) == expected


@pytest.mark.parametrize(
    ("uri", "reason"),
    [
        ("http://example.invalid/a.py", DropReason.UNSUPPORTED_SCHEME),
        ("https://example.invalid/a.py", DropReason.UNSUPPORTED_SCHEME),
        ("ftp://example.invalid/a.py", DropReason.UNSUPPORTED_SCHEME),
        ("vscode:open?file=a.py", DropReason.UNSUPPORTED_SCHEME),
        ("C:\\x\\a.py", DropReason.DRIVE_LETTER),
        ("C:/x/a.py", DropReason.DRIVE_LETTER),
        ("file://server/share/a.py", DropReason.FILE_URI_WITH_HOST),
        ("file://localhost/work/repo/a.py", DropReason.FILE_URI_WITH_HOST),
        ("src/a\x00.py", DropReason.NUL_CHARACTER),
        ("src/a%00.py", DropReason.NUL_CHARACTER),
        ("file:///work/repo/a%00.py", DropReason.NUL_CHARACTER),
        ("file:///work/repo/a\x00.py", DropReason.NUL_CHARACTER),
        ("C%3A/x/a.py", DropReason.DRIVE_LETTER),
        ("c%3a\\x\\a.py", DropReason.DRIVE_LETTER),
        ("//server/share/a.py", DropReason.UNC_PATH),
        ("\\\\server\\share\\a.py", DropReason.UNC_PATH),
        ("/etc/passwd", DropReason.ABSOLUTE_PATH),
        ("/work/repo/src/a.py", DropReason.ABSOLUTE_PATH),
        ("../a.py", DropReason.PARENT_REFERENCE),
        ("src/../../a.py", DropReason.PARENT_REFERENCE),
        ("%2e%2e/a.py", DropReason.PARENT_REFERENCE),
        ("..%5Ca.py", DropReason.PARENT_REFERENCE),
        ("..", DropReason.PARENT_REFERENCE),
        (".", DropReason.NOT_A_FILE),
        ("", DropReason.NOT_A_FILE),
        ("src/..", DropReason.NOT_A_FILE),
        ("file:///other/a.py", DropReason.OUTSIDE_ANALYSIS_ROOT),
        ("file:///work/repository/a.py", DropReason.OUTSIDE_ANALYSIS_ROOT),
        ("file:///work/repo/../a.py", DropReason.OUTSIDE_ANALYSIS_ROOT),
        ("file:///C:/work/repo/a.py", DropReason.OUTSIDE_ANALYSIS_ROOT),
        ("file:a.py", DropReason.MALFORMED_URI),
        ("src/a\nb.py", DropReason.CONTROL_CHARACTER),
        ("src/a\x1bb.py", DropReason.CONTROL_CHARACTER),
    ],
)
def test_drops_with_reason(uri: str, reason: DropReason) -> None:
    assert resolve(uri) == reason


def test_double_dot_inside_a_name_is_not_a_parent_reference() -> None:
    assert resolve("src/..a.py") == "src/..a.py"
    assert resolve("..a/b.py") == "..a/b.py"


def test_relative_uri_ignores_uri_base_id_and_uses_analysis_root() -> None:
    sub = REPOSITORY / "service"
    assert resolve("src/a.py", sub) == "service/src/a.py"


def test_file_uri_is_relative_to_analysis_root_then_repository() -> None:
    sub = REPOSITORY / "service"
    assert resolve("file:///work/repo/service/src/a.py", sub) == "service/src/a.py"
    assert resolve("file:///work/repo/other/a.py", sub) == DropReason.OUTSIDE_ANALYSIS_ROOT


def test_parent_reference_from_subdirectory_root_stays_inside_repository() -> None:
    sub = REPOSITORY / "service" / "deep"
    assert resolve_uri("../a.py", sub, REPOSITORY) == DropReason.PARENT_REFERENCE


def test_analysis_root_outside_repository_drops_everything() -> None:
    outside = Path("/work/elsewhere")
    assert resolve_uri("a.py", outside, REPOSITORY) == DropReason.OUTSIDE_REPOSITORY
    assert resolve_uri("file:///work/elsewhere/a.py", outside, REPOSITORY) == (
        DropReason.OUTSIDE_REPOSITORY
    )


def test_analysis_root_above_repository_drops_paths_outside_repository() -> None:
    above = Path("/work")
    assert resolve_uri("repo/a.py", above, REPOSITORY) == "a.py"
    assert resolve_uri("other/a.py", above, REPOSITORY) == DropReason.OUTSIDE_REPOSITORY


def test_dropped_counts_are_broken_down_by_reason(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    results = [
        result("ok.py"),
        result("http://x.invalid/a.py"),
        result("http://x.invalid/b.py"),
        result("../c.py"),
        result("/abs/d.py"),
        result(None),
        result("e.py"),
    ]
    path = write(tmp_path, "a.sarif", log(run(results)))
    imported = read_sarif([path], repository, repository)
    assert [f.path for f in imported.findings] == ["ok.py", "e.py"]
    assert {reason.value: count for reason, count in imported.dropped.items()} == {
        "unsupported_scheme": 2,
        "parent_reference": 1,
        "absolute_path": 1,
        "no_location": 1,
    }


def test_analysis_root_option_changes_relative_uri_base(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    (repository / "mod").mkdir(parents=True)
    path = write(tmp_path, "a.sarif", log(run([result("src/a.py")])))
    imported = read_sarif([path], repository / "mod", repository)
    assert imported.findings[0].path == "mod/src/a.py"
