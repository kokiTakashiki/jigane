import os
from pathlib import Path

import pytest
from shard_prompt_support import shard_prompt
from test_security import SecurityFake, discovery_calls, write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.subcommands.run import run_review
from jigane.groups.definitions import SECURITY
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import security, shards
from jigane.skills.assembly import AssemblyInput
from jigane.skills.mcp import MCP_CONFIG_DIRECTORY, McpSelection, McpServer, SecurityConfigError
from jigane.storage.store import data_home

pytestmark = pytest.mark.usefixtures("selected_agent")


IOS_USE = "iOS と Android のアプリのコード"
SERVER_USE = "サーバ、API、サーバ上で動くプラグインのコード"
GUIDE = f"""
[mcps.mobile-sec]
use_for = "{IOS_USE}"

[mcps.server-sec]
use_for = "{SERVER_USE}"
"""
NAMES = ("mobile-sec", "server-sec")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    for name in ("a", "b"):
        (root / "src" / f"{name}.py").write_text(
            f"# {name}\n" + "x = 1\n" * 3500, encoding="utf-8"
        )
    monkeypatch.chdir(root)
    return root


def write_guide(directory: Path, text: str = GUIDE) -> Path:
    path = directory / "guide.toml"
    path.write_text(text, encoding="utf-8")
    return path


def prompt_of(project: Path, selection: McpSelection) -> str:
    review = create_feature_review(project, ["src"], "p")
    shard = shards.build_plan(review, security.GROUP).shards[0]
    return shard_prompt(SECURITY, AssemblyInput.load(review), shard, 1, 1, selection)


def test_valid_guide_adds_name_and_use_for_lines_to_the_prompt(project: Path) -> None:
    write_mcp()
    selection = McpSelection.load(NAMES, write_guide(project))
    prompt = prompt_of(project, selection)
    assert f"- mobile-sec: {IOS_USE}" in prompt
    assert f"- server-sec: {SERVER_USE}" in prompt
    assert "$mcp_guide" not in prompt


def test_without_guide_the_prompt_has_no_guide_section(project: Path) -> None:
    write_mcp()
    prompt = prompt_of(project, McpSelection.load(NAMES))
    assert "MCP の使い分け" not in prompt
    assert "\n\n\n" not in prompt.split("## レビューの対象")[0]


@pytest.mark.parametrize(
    "text",
    [
        GUIDE + "\n[other]\nx = 1\n",
        GUIDE.replace(f'use_for = "{SERVER_USE}"', f'use_for = "{SERVER_USE}"\nnote = "y"'),
        GUIDE.replace(f'use_for = "{IOS_USE}"', 'title = "a"'),
        GUIDE.replace(f'"{IOS_USE}"', '""'),
        GUIDE.replace(f'"{IOS_USE}"', "3"),
        GUIDE.replace(IOS_USE, "a\\nb"),
        GUIDE.replace(IOS_USE, "a\\rb"),
        "[mcps\n",
    ],
    ids=["unknown_top", "unknown_key", "missing_use_for", "empty", "not_str", "lf", "cr", "toml"],
)
def test_malformed_guide_is_an_error(project: Path, text: str) -> None:
    write_mcp()
    with pytest.raises(SecurityConfigError):
        McpSelection.load(NAMES, write_guide(project, text))


def test_guide_name_absent_from_mcp_json_is_an_error(project: Path) -> None:
    write_mcp(("mobile-sec",))
    with pytest.raises(SecurityConfigError, match="server-sec"):
        McpSelection.load(NAMES, write_guide(project))


@pytest.mark.parametrize("names", [("mobile-sec",), (), (*NAMES, "extra")])
def test_guide_names_must_equal_the_mcps_set(project: Path, names: tuple[str, ...]) -> None:
    write_mcp((*NAMES, "extra"))
    with pytest.raises(SecurityConfigError, match="同じ集合"):
        McpSelection.load(names, write_guide(project))


def test_run_review_with_guide_reaches_the_prompts(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    fake = SecurityFake({1: [], 2: []})
    run_review(
        str(review.id), security.GROUP, invoke=fake, mcps=NAMES, mcp_guide=write_guide(project)
    )
    discovery = discovery_calls(fake)
    assert discovery
    assert all(f"- mobile-sec: {IOS_USE}" in call.prompt for call in discovery)


def test_run_command_with_a_bad_guide_stops_before_invoking(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    fake = SecurityFake({1: [], 2: []})
    guide = write_guide(project, GUIDE.replace(f'"{IOS_USE}"', '""'))
    code = main(
        ["run", str(review.id), "security", "--mcps", *NAMES, "--mcp-guide", str(guide)],
        invoke=fake,
    )
    assert code != EXIT_OK
    assert fake.calls == []


@pytest.mark.parametrize("command", ["run", "estimate"])
def test_guide_is_rejected_for_other_groups(
    project: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    guide = write_guide(project)
    fake = SecurityFake({1: [], 2: []})
    for group in ("micro", "macro"):
        code = main([command, str(review.id), group, "--mcp-guide", str(guide)], invoke=fake)
        assert code != EXIT_OK
        assert "--mcp-guide はセキュリティの群だけが受け付けます" in capsys.readouterr().err
    assert fake.calls == []


def test_estimate_counts_the_guide_in_the_prompt_bytes(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    assembly = AssemblyInput.load(review)
    plain = security.estimate_security(assembly, McpSelection.load(NAMES))
    guided = security.estimate_security(assembly, McpSelection.load(NAMES, write_guide(project)))
    pairs = zip(guided.prompt_bytes, plain.prompt_bytes, strict=True)
    assert all(after > before for after, before in pairs)


def test_run_command_with_a_multiline_use_for_stops_before_invoking(project: Path) -> None:
    write_mcp()
    review = create_feature_review(project, ["src"], "p")
    fake = SecurityFake({1: [], 2: []})
    guide = write_guide(project, GUIDE.replace(IOS_USE, "a\\nb"))
    code = main(
        ["run", str(review.id), "security", "--mcps", *NAMES, "--mcp-guide", str(guide)],
        invoke=fake,
    )
    assert code != EXIT_OK
    assert not fake.calls


def test_selection_servers_are_named_servers_with_use_for_only_when_guided(project: Path) -> None:
    write_mcp()
    guided = McpSelection.load(NAMES, write_guide(project)).servers
    plain = McpSelection.load(NAMES).servers
    assert all(isinstance(server, McpServer) for server in (*guided, *plain))
    assert [(server.name, server.use_for) for server in guided] == [
        ("mobile-sec", IOS_USE),
        ("server-sec", SERVER_USE),
    ]
    assert [server.use_for for server in plain] == [None, None]


def test_too_deep_guide_toml_is_an_error(project: Path) -> None:
    write_mcp()
    deep = "a = " + "[" * 5000 + "]" * 5000
    with pytest.raises(SecurityConfigError, match="TOML として読めません"):
        McpSelection.load(NAMES, write_guide(project, deep))


def test_selection_does_not_open_native_or_legacy_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_mcp()
    legacy = data_home() / "mcp.json"
    legacy.write_bytes(b"legacy config is opaque")

    def unexpected_open(*_args: object, **_kwargs: object) -> None:
        pytest.fail("jigane opened a config")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", unexpected_open)
        patch.setattr(os, "open", unexpected_open)
        selection = McpSelection.load(NAMES)
    assert selection.config_paths == tuple(
        data_home().resolve() / MCP_CONFIG_DIRECTORY / f"{name}.json" for name in NAMES
    )
    assert legacy.read_bytes() == b"legacy config is opaque"


def test_claude_can_use_a_separate_registration_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_home = data_home()
    registrations = tmp_path / "mcp registry"
    path = registrations / MCP_CONFIG_DIRECTORY / "refs.json"
    path.parent.mkdir(mode=0o700, parents=True)
    path.write_text("opaque Claude Code configuration", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("JIGANE_MCP_HOME", str(registrations))

    assert McpSelection.load(("refs",)).config_paths == (path.resolve(),)
    assert data_home() == review_home
    assert not (review_home / MCP_CONFIG_DIRECTORY).exists()


@pytest.mark.parametrize("value", ["", "relative", "file", "missing"])
def test_mcp_home_requires_an_existing_absolute_directory(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if value == "file":
        (tmp_path / value).touch()
    override = str(tmp_path / value) if value in {"file", "missing"} else value
    monkeypatch.setenv("JIGANE_MCP_HOME", override)
    assert McpSelection.load(()) == McpSelection.none()
    with pytest.raises(SecurityConfigError, match="既存のディレクトリの絶対パス"):
        McpSelection.load(("refs",))
