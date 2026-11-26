import json
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from subprocess import CompletedProcess
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fakes import (
    PROMPT_MARKER,
    RECORD,
    THEMES_MARKER,
    FakeInvoke,
    d_finding,
    embedded_json,
    settings_for,
)
from review_fakes import make_review

from jigane.groups.definitions import MICRO, SECURITY, GroupId
from jigane.judgment_map import changes
from jigane.judgment_map.changes import CHANGES_FILE, render_changes, without_whole_deletions
from jigane.judgment_map.classification import FilePair, is_whole_deletion
from jigane.judgment_map.configuration import Config
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review
from jigane.reviews.review_formats import PAIRS_FILE, dump_pairs
from jigane.skills import adversarial, config_head, macro, micro, security, sharded
from jigane.skills.assembly import AssemblyInput
from jigane.skills.shards import ShardPlan, ShardPlanKind
from jigane.storage.store import ReviewKind

CHANGES = (
    "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n--- a/not_a_header\n"
    "--- a/kept.py\n+++ b/kept.py\n@@ -1 +1 @@\n-a\n+b\n"
    "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+c\n"
    "--- a/old.py\n+++ b/renamed.py\n@@ -1 +1 @@\n-d\n+e\n"
)
PAIRS = [
    FilePair("gone.py", None),
    FilePair("kept.py", "kept.py"),
    FilePair(None, "new.py"),
    FilePair("old.py", "renamed.py"),
]


def test_whole_deletion_body_is_dropped_and_listed() -> None:
    kept, removed = without_whole_deletions(CHANGES, PAIRS)
    assert removed == ["gone.py"]
    assert "x = 1" not in kept
    assert "--- a/gone.py" not in kept


def test_changed_added_and_renamed_sections_stay() -> None:
    kept, _ = without_whole_deletions(CHANGES, PAIRS)
    assert kept == CHANGES[CHANGES.index("--- a/kept.py") :]


def test_deletion_body_line_that_looks_like_a_header_does_not_split() -> None:
    kept, removed = without_whole_deletions(CHANGES, PAIRS)
    assert removed == ["gone.py"]
    assert "not_a_header" not in kept


def test_missing_header_is_an_error() -> None:
    with pytest.raises(ValueError, match="見出し"):
        without_whole_deletions("--- a/x\n+++ b/x\n", [FilePair("y", "y")])


@pytest.fixture
def diff_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    (tmp_path / CHANGES_FILE).write_text(CHANGES, encoding="utf-8")
    review = SimpleNamespace(id=SimpleNamespace(directory=lambda: tmp_path))
    monkeypatch.setattr(macro, "load_pairs", lambda _id: PAIRS)
    return cast("Review", review)


def test_verifier_changes_omit_deleted_body_and_list_paths(diff_review: Review) -> None:
    text = macro.verifier_changes(diff_review).text
    assert "x = 1" not in text
    assert "### 丸ごと削除したファイル（本文は省いた）\n\n- gone.py\n" in text
    assert "+++ b/renamed.py" in text


def test_verifier_changes_without_deletion_equal_policy_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes = "--- a/kept.py\n+++ b/kept.py\n@@ -1 +1 @@\n-a\n+b\n"
    (tmp_path / CHANGES_FILE).write_text(changes, encoding="utf-8")
    monkeypatch.setattr(macro, "load_pairs", lambda _id: [FilePair("kept.py", "kept.py")])
    identifier = SimpleNamespace(directory=lambda: tmp_path)
    review = cast("Any", SimpleNamespace(id=identifier, is_diff=True))
    assert macro.verifier_changes(review).text == macro.changes_prompt_text(review)


def test_policy_changes_keep_the_deleted_body(diff_review: Review) -> None:
    review = cast("Any", diff_review)
    review.is_diff = True
    assert "x = 1" in macro.changes_prompt_text(review)


def test_sharded_diff_plan_uses_trimmed_changes_and_full_plan_does_not(
    diff_review: Review, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = cast("Any", diff_review)
    review.is_diff = True
    review.repository_root = "."
    monkeypatch.setattr(config_head, "config_heads_section", lambda _root, _paths: "")
    diff_plan = ShardPlan(ShardPlanKind.DIFF, (), ())
    assert "x = 1" not in sharded._changes_with_config_heads(review, diff_plan).text
    files_plan = ShardPlan(ShardPlanKind.FILES, (), ())
    assert sharded._changes_with_config_heads(
        review, files_plan
    ).text == macro.changes_prompt_text(review)


def test_discovery_and_verifier_share_the_deletion_criterion() -> None:
    assert is_whole_deletion(PAIRS[0])
    assert not any(is_whole_deletion(pair) for pair in PAIRS[1:])


def test_rendered_changes_split_with_excluded_and_over_limit_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 書く側（render_changes）の見出しと、切る側の見出しが一致することを、実際の出力で確かめる
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "gone.py").write_text("x = 1\n", encoding="utf-8")
    (base / "kept.py").write_text("a\n", encoding="utf-8")
    (head / "kept.py").write_text("b\n", encoding="utf-8")
    (head / "uv.lock").write_text("lock\n", encoding="utf-8")
    (head / "big.py").write_text("y = 2\n" * 20, encoding="utf-8")
    pairs = [
        FilePair("gone.py", None),
        FilePair("kept.py", "kept.py"),
        FilePair(None, "uv.lock"),
        FilePair(None, "big.py"),
    ]
    monkeypatch.setattr(changes, "CHANGES_CHAR_LIMIT", 120)
    text, omitted = render_changes(base, head, pairs, Config(exclude=("uv.lock",)))
    assert omitted >= 1
    kept, removed = without_whole_deletions(text, pairs)
    assert removed == ["gone.py"]
    assert "x = 1" not in kept
    assert "+++ b/kept.py" in kept
    assert "+++ b/uv.lock" in kept
    assert "+++ b/big.py" in kept


@pytest.fixture
def synthetic_range(tmp_path: Path) -> Review:
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    files = [
        (
            FilePair("sample-old.pem", "renamed.txt"),
            "fixture before rename",
            "fixture after rename",
        ),
        (
            FilePair("archive/sample [draft].txt", "archive/sample.pem"),
            "fixture before reverse rename",
            "fixture after reverse rename",
        ),
        (FilePair("a.py", "a.py"), "# line one\nx = 1", "# line one\nx = 2"),
        (FilePair(None, ".env.fixture"), None, "fixture addition"),
        (FilePair("sample-deleted.pem", None), "fixture deletion", None),
        (FilePair("removed.py", None), "fixture ordinary deletion", None),
    ]
    (base / "archive").mkdir()
    (head / "archive").mkdir()
    for pair, before, after in files:
        if pair.old_path is not None:
            (base / pair.old_path).write_text(f"{before}\n", encoding="utf-8")
        if pair.new_path is not None:
            (head / pair.new_path).write_text(f"{after}\n", encoding="utf-8")
    pairs = [pair for pair, _, _ in files]
    paths = [pair.new_path for pair in pairs if pair.new_path is not None]
    review = make_review(head, paths, base_tree=base, kind=ReviewKind.RANGE)
    directory = review.id.directory()
    directory.mkdir(parents=True)
    diff, omitted = render_changes(base, head, pairs, Config())
    assert not omitted
    (directory / CHANGES_FILE).write_text(diff, encoding="utf-8")
    (directory / PAIRS_FILE).write_text(json.dumps(dump_pairs(pairs)), encoding="utf-8")
    return review


@pytest.mark.parametrize("group", [MICRO, SECURITY])
def test_sharded_claude_launch_denies_paths_excluded_from_the_diff(
    synthetic_range: Review, group: GroupId
) -> None:
    launches: list[tuple[str, list[str]]] = []
    fake = FakeInvoke()
    finding = {
        **d_finding("D1" if group == MICRO else "S-CODE", "a.py"),
        "references": ["fixture reference"],
    }

    def response(invocation: claude.Invocation) -> tuple[str, dict[str, Any] | None]:
        if invocation.discovery_group is not None:
            return "discovery", {"findings": [finding]}
        if THEMES_MARKER in invocation.prompt:
            return "themes", {"themes": []}
        output = fake(invocation).structured_output
        if "反証役です" in invocation.prompt:
            return "rebuttal", output
        assert "反証を受けて判定" in invocation.prompt
        return "judge", output

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        def runner(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
            role, output = response(invocation)
            launches.append((role, command))
            payload = {"structured_output": output, "num_turns": 1}
            return CompletedProcess(command, 0, json.dumps(payload), "")

        return claude.run(invocation, runner=runner)

    settings = settings_for(invoke)
    run = micro.run_micro if group == MICRO else security.run_security
    result = run(AssemblyInput.load(synthetic_range), settings)

    assert result.failed_splits == 0
    expected_roles = ["discovery", "rebuttal", "judge"]
    if group == MICRO:
        expected_roles.append("themes")
    assert [role for role, _ in launches] == expected_roles
    static_denials = set(claude.secret_read_denials())
    denials_by_role: dict[str, frozenset[str]] = {}
    for role, command in launches:
        permissions = json.loads(command[command.index("--settings") + 1])["permissions"]
        assert permissions["blockReadsOutsideWorkingDirectories"] is True
        assert "Read" in command[command.index("--tools") + 1].split(",")
        assert static_denials <= set(permissions["deny"])
        denials_by_role[role] = frozenset(permissions["deny"]) - static_denials
    absolute_root = Path(synthetic_range.repository_root).resolve().as_posix()
    if ":" in absolute_root[:2]:
        absolute_root = f"/{absolute_root[0].lower()}{absolute_root[2:]}"
    absolute_root = "".join(f"\\{char}" if char in "\\*?[] " else char for char in absolute_root)
    assert denials_by_role == dict.fromkeys(
        expected_roles,
        frozenset(
            {
                f"Read(/{absolute_root}/sample-old.pem)",
                f"Read(/{absolute_root}/renamed.txt)",
                rf"Read(/{absolute_root}/archive/sample\ \[draft\].txt)",
                f"Read(/{absolute_root}/archive/sample.pem)",
                f"Read(/{absolute_root}/.env.fixture)",
                f"Read(/{absolute_root}/sample-deleted.pem)",
            }
        ),
    )


def test_absolute_read_denial_normalizes_windows_drive() -> None:
    path = PureWindowsPath("D:/fictional archive/sample [draft].txt")
    expected = r"Read(//d/fictional\ archive/sample\ \[draft\].txt)"
    assert claude._absolute_read_denial(path) == expected


@pytest.mark.parametrize("group", [MICRO, SECURITY])
@pytest.mark.parametrize(
    ("allow_tools", "compact"),
    [(True, False), (False, False), (False, True)],
    ids=["with-read", "without-read", "hunks"],
)
def test_sharded_roles_share_secret_file_exclusion(
    synthetic_range: Review, group: GroupId, *, allow_tools: bool, compact: bool
) -> None:
    discovery: list[str] = []
    bundles: list[str] = []
    fake = FakeInvoke()
    finding = {
        **d_finding("D1" if group == MICRO else "S-CODE", "a.py"),
        "references": ["fixture reference"],
    }

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        if invocation.discovery_group is not None:
            discovery.append(invocation.prompt)
            findings = [
                finding,
                {**finding, "claim": "fixture concern one", "disclosed_at": "a.py:1"},
                {**finding, "claim": "fixture concern two", "disclosed_at": "a.py:2"},
            ]
            return claude.InvocationResult({"findings": findings}, RECORD)
        if THEMES_MARKER in invocation.prompt:
            return claude.InvocationResult({"themes": []}, RECORD)
        if PROMPT_MARKER in invocation.prompt:
            bundles.append(invocation.prompt)
        return fake(invocation)

    settings = settings_for(invoke)
    settings = replace(
        settings,
        allow_tools=allow_tools,
        budget=replace(settings.budget, compact_materials=compact),
    )
    run = micro.run_micro if group == MICRO else security.run_security
    result = run(AssemblyInput.load(synthetic_range), settings)
    assert result.failed_splits == 0
    [discovery_prompt] = discovery
    rebuttal, judge = [call.prompt for call in fake.calls if call.prompt not in bundles]
    assert "反証役です" in rebuttal
    assert "反証を受けて判定" in judge
    for prompt in (discovery_prompt, rebuttal, judge):
        assert "+++ b/a.py\n@@ -1,2 +1,2 @@\n # line one\n-x = 1\n+x = 2" in prompt
    if allow_tools:
        assert "2: x = 2" in judge
        assert "- removed.py\n" in rebuttal
    [bundle] = bundles
    assert "fixture concern one" in bundle
    assert "fixture concern two" in bundle
    for prompt in (discovery_prompt, rebuttal, judge, bundle):
        for withheld in (
            "sample-old.pem",
            "renamed.txt",
            "archive/sample [draft].txt",
            "archive/sample.pem",
            ".env.fixture",
            "sample-deleted.pem",
            "fixture before rename",
            "fixture after rename",
            "fixture before reverse rename",
            "fixture after reverse rename",
            "fixture addition",
            "fixture deletion",
        ):
            assert withheld not in prompt


@pytest.mark.parametrize("group", [MICRO, SECURITY])
@pytest.mark.parametrize(
    "pair",
    [FilePair("fixture.pem", "after.txt"), FilePair("before.txt", "fixture.pem")],
    ids=["excluded-old-name", "excluded-new-name"],
)
def test_verdict_excerpts_skip_both_paths_of_excluded_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: GroupId, pair: FilePair
) -> None:
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (head / "a.py").write_text("x = 1\nx = 2\n", encoding="utf-8")
    pairs = [pair, FilePair(None, "a.py")]
    review = make_review(head, ["a.py"], base_tree=base, kind=ReviewKind.RANGE)
    directory = review.id.directory()
    directory.mkdir(parents=True)
    diff, _ = render_changes(base, head, pairs, Config())
    (directory / CHANGES_FILE).write_text(diff, encoding="utf-8")
    (directory / PAIRS_FILE).write_text(json.dumps(dump_pairs(pairs)), encoding="utf-8")

    reads: list[str] = []

    def read_lines(_root: Path, path: str) -> bytes | None:
        reads.append(path)
        return b"x = 1\nx = 2\n" if path == "a.py" else None

    monkeypatch.setattr(adversarial, "read_tree_file", read_lines)
    fake = FakeInvoke()

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        if invocation.discovery_group is not None:
            finding = {
                **d_finding("D1" if group == MICRO else "S-CODE", "a.py"),
                "references": ["fixture reference"],
            }
            return claude.InvocationResult({"findings": [finding]}, RECORD)
        if THEMES_MARKER in invocation.prompt:
            return claude.InvocationResult({"themes": []}, RECORD)
        if "反証役です" in invocation.prompt:
            locations = [
                {"path": path, "start_line": 2, "end_line": 2}
                for path in (pair.old_path, pair.new_path, "a.py")
            ]
            return claude.InvocationResult(
                {"items": [{"index": 0, "challenge": "fixture", "checked": locations}]}, RECORD
            )
        return fake(invocation)

    run = micro.run_micro if group == MICRO else security.run_security
    result = run(AssemblyInput.load(review), settings_for(invoke))

    assert result.failed_splits == 0
    assert reads == ["a.py"]
    [judge] = fake.calls
    [entry] = embedded_json(judge.prompt, "反証役が確かめた位置と抜粋")
    assert entry["checked"] == [
        {"path": "a.py", "start_line": 2, "end_line": 2, "text": "2: x = 2"}
    ]
