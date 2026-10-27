import json
from pathlib import Path

import pytest

from jigane import commands
from jigane.commands import failures
from jigane.flaky.catalog import CatalogError
from jigane.git.process import CommandError, run_command
from jigane.groups.definitions import MACRO, MICRO, SECURITY, GroupId
from jigane.llm_runs.claude import ClaudeInvocationError, parse_record
from jigane.reviews.lifecycle import ReviewError
from jigane.skills.mcp import SecurityConfigError
from jigane.skills.pipeline import DiscoveryError
from jigane.storage.review_error import ReviewExistsError
from jigane.storage.store import LockTimeoutError, ReviewId, ReviewKind, StoredFileInUseError

MINE = ReviewId("o", "r", "mine", ReviewKind.RANGE)

LISTED = [
    ReviewError("r"),
    ReviewExistsError(MINE, "r"),
    CommandError(["tool", "x"], "e"),
    ClaudeInvocationError("c", parse_record({}, 0.5)),
    LockTimeoutError("l"),
    StoredFileInUseError("u"),
    SecurityConfigError("s"),
    DiscoveryError(MACRO, "m"),
    CatalogError("c"),
    ValueError("v"),
    OSError("o"),
]


@pytest.mark.parametrize("error", LISTED, ids=lambda error: type(error).__name__)
def test_every_listed_type_maps_to_a_specific_kind(error: Exception) -> None:
    failure = failures.describe(error)
    assert failure.kind != failures.UNEXPECTED
    assert failure.title
    assert failure.action
    assert failure.reason


@pytest.mark.parametrize(
    ("group", "title"),
    [
        (MACRO, "方針の発見役が失敗しました"),
        (MICRO, "実装の発見役が失敗しました"),
        (SECURITY, "セキュリティの発見役が失敗しました"),
    ],
)
def test_discovery_failure_title_names_the_group(group: GroupId, title: str) -> None:
    assert failures.describe(DiscoveryError(group, "x")).title == title


def test_kinds_are_distinct_per_type() -> None:
    kinds = [kind.kind for _, kind in failures.KINDS]
    assert len(kinds) == len(set(kinds))


def test_unknown_exception_is_unexpected_and_names_its_type() -> None:
    failure = failures.describe(RuntimeError("boom"))
    assert failure.kind == failures.UNEXPECTED
    assert failure.reason == "RuntimeError: boom"


def test_gh_failure_is_reported_as_github_with_the_stderr_tail() -> None:
    failure = failures.describe(CommandError(["gh", "pr", "view", "u"], "x\n" * 900 + "not found"))
    assert failure.kind == "github"
    assert failure.reason.endswith("not found")
    assert len(failure.reason) < 1000
    assert "gh auth status" in failure.action


def test_main_prints_the_error_object_for_json_failures(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    code = commands.main(["show", "--json", "nobody/nothing:1", "overview"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.startswith("jigane: ")
    error = json.loads(captured.out)["error"]
    assert set(error) == {"kind", "title", "reason", "action"}
    assert error["kind"] != failures.UNEXPECTED


def test_main_does_not_print_json_without_the_flag(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    assert commands.main(["show", "nobody/nothing:1", "overview"]) == 2
    assert capsys.readouterr().out == ""


def test_main_reports_an_unknown_exception_as_unexpected(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr("jigane.commands.subcommands.list.run", explode)
    code = commands.main(["list", "--json"])
    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.out)["error"]["kind"] == failures.UNEXPECTED
    assert "jigane: RuntimeError: boom" in captured.err
    assert "Traceback" in captured.err


def test_missing_executable_is_reported_as_missing_tool_naming_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # PATH に何も無い空の置き場だけを指し、一覧にある git が見つからない状況を作る
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(CommandError) as raised:
        run_command(["git", "status"])
    failure = failures.describe(raised.value)
    assert failure.kind == "missing_tool"
    assert failure.title == "必要なコマンドが見つかりません"
    assert "実行ファイルが見つかりません: git" in failure.reason
    assert failure.action == "git をインストールして PATH に通してください"


@pytest.mark.parametrize(
    ("executable", "expected"),
    # ast-grep-ignore: no-claude-launch-outside-llm-runs 起動ではなく、期待する引数の列
    [("gh", "gh auth login"), ("git", "git をインストール"), ("claude", "Claude Code")],
)
def test_missing_tool_action_follows_the_missing_executable(
    executable: str, expected: str
) -> None:
    error = CommandError([executable, "x"], "実行ファイルが見つからない", missing_executable=True)
    assert expected in failures.describe(error).action


def test_missing_working_directory_is_not_a_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_command(["git", "status"], cwd=tmp_path / "absent")


def test_json_decode_error_is_corrupt_state() -> None:
    assert failures.describe(json.JSONDecodeError("bad", "{", 1)).kind == "corrupt_state"


def test_review_exists_failure_carries_the_existing_review_id() -> None:
    failure = failures.describe(ReviewExistsError(MINE, "レビューが既にあります: o/r@mine"))
    assert failure.kind == "review_exists"
    assert failure.to_dict()["review"] == "o/r@mine"


def test_other_failures_do_not_carry_the_review_key() -> None:
    assert "review" not in failures.describe(ReviewError("r")).to_dict()


def test_main_prints_the_review_key_for_an_overlapping_new_review(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def overlap(*_: object) -> int:
        raise ReviewExistsError(MINE, "レビューが既にあります: o/r@mine")

    monkeypatch.setattr("jigane.commands.subcommands.list.run", overlap)
    assert commands.main(["list", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert (error["kind"], error["review"]) == ("review_exists", "o/r@mine")
