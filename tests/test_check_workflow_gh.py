"""ワークフローの gh の行を、固定した実物の gh で確かめる検査の試験。"""

import re
from pathlib import Path

import pytest
from test_check_workflows import CLEAN_CHECKOUT, ROOT, load_checker, write_workflow

# v0.1.0 の github-release を落とした形。--notes-from-tag と --repo の併用は --help に書かれない
NOTES_FROM_TAG_WITH_REPO = """\
      - run: |
          gh release create "$TAG" \\
            --repo "$REPOSITORY" \\
            --verify-tag \\
            --notes-from-tag \\
            TERMS.ja.md
"""
CURRENT_RELEASE_CREATE = """\
      - run: |
          gh release create "$TAG" \\
            --repo "$REPOSITORY" \\
            --verify-tag \\
            --title "$TAG" \\
            --notes "$TAG" \\
            TERMS.ja.md artifacts/sbom.cdx.json
"""
UNKNOWN_API_FLAG = """\
      - run: total=$(gh api "repos/${REPOSITORY}/actions/runs" --jqq '.total_count')
"""
NO_GH = "      - run: echo done\n"


def test_repository_workflows_pass_the_gh_check() -> None:
    checker = load_checker()
    assert checker.run_gh_lines(ROOT / ".github") == 0


def test_current_release_create_reaches_the_network_stage(tmp_path: Path) -> None:
    checker = load_checker()
    directory = write_workflow(tmp_path, CLEAN_CHECKOUT + CURRENT_RELEASE_CREATE)
    assert checker.run_gh_lines(directory.parent) == 0


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (NOTES_FROM_TAG_WITH_REPO, "using `--notes-from-tag` with `--repo` is not supported"),
        (UNKNOWN_API_FLAG, "unknown flag: --jqq"),
    ],
    ids=["v0.1.0-notes-from-tag-with-repo", "unknown-api-flag"],
)
def test_gh_check_fails_on_a_flag_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str, message: str
) -> None:
    checker = load_checker()
    directory = write_workflow(tmp_path, CLEAN_CHECKOUT + body)
    assert checker.run_gh_lines(directory.parent) == 1
    assert message in capsys.readouterr().err


def test_gh_check_fails_when_no_gh_line_is_found(tmp_path: Path) -> None:
    checker = load_checker()
    directory = write_workflow(tmp_path, CLEAN_CHECKOUT + NO_GH)
    assert checker.run_gh_lines(directory.parent) == 1


def test_gh_calls_follow_continuations_and_command_substitutions() -> None:
    checker = load_checker()
    script = (
        'ref=$(gh api "repos/${REPOSITORY}/git/tags/$(jq -r .sha <<<"$ref")")\n'
        'gh release create "$TAG" \\\n  --notes "$TAG" >out.txt\n'
        "echo gh is a word here\n"
    )
    assert list(checker.gh_calls(script)) == [
        ["api", "repos/nobody/none/git/tags/x0"],
        ["release", "create", "v0.0.0", "--notes", "v0.0.0"],
    ]


def test_gh_unavailable_is_not_a_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checker = load_checker()

    def unavailable() -> Path:
        message = "offline"
        raise checker.GhUnavailableError(message)

    monkeypatch.setattr(checker, "fetch_gh", unavailable)
    assert checker.run_gh_lines(tmp_path) == checker.TOOL_UNAVAILABLE_EXIT_CODE


def test_gh_checksums_cover_every_supported_platform() -> None:
    checker = load_checker()
    for digest in checker.GH_SHA256.values():
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert set(checker.GH_SHA256) == {
        "macOS_amd64.zip",
        "macOS_arm64.zip",
        "linux_amd64.tar.gz",
        "linux_arm64.tar.gz",
        "windows_amd64.zip",
        "windows_arm64.zip",
    }
