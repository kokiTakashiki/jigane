import json
import re
import threading
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import RECORD, FakeInvoke, discovered, settings_for
from review_fakes import stored_finding
from shard_prompt_support import shard_prompt

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_INCOMPLETE_COVERAGE, EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands.run import run_review
from jigane.findings.records import read_stored_state, save_findings_state, triaged_dicts
from jigane.groups.definitions import MACRO, SECURITY
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import mcp, security, shards
from jigane.skills.assembly import AssemblyInput
from jigane.skills.mcp import McpSelection
from jigane.storage.store import data_home


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


def write_mcp(servers: tuple[str, ...] = ("owasp-mas", "backendsec")) -> None:
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    config = {"mcpServers": {name: {"type": "stdio", "command": "x"} for name in servers}}
    (home / mcp.MCP_CONFIG_FILE).write_text(json.dumps(config), encoding="utf-8")


def s_finding(
    viewpoint: str = "S-STORAGE",
    path: str = "src/a.py",
    severity: str = "warning",
    references: tuple[str, ...] = ("MASVS-STORAGE-1",),
) -> dict[str, Any]:
    return {
        **discovered(viewpoint, path),
        "severity": severity,
        "references": list(references),
        "in_scope": True,
        "scope_reason": "",
    }


class SecurityFake(FakeInvoke):
    def __init__(
        self, by_shard: dict[int, list[dict[str, Any]] | None], verdict: str = "kept"
    ) -> None:
        super().__init__(verdict=verdict)
        self.by_shard = by_shard
        self.lock = threading.Lock()

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        if "担当のシャード" not in invocation.prompt:
            return super().__call__(invocation)
        match = re.search(r"担当のシャード: (\d+) / (\d+)", invocation.prompt)
        assert match
        with self.lock:
            self.calls.append(invocation)
        findings = self.by_shard.get(int(match.group(1)))
        if findings is None:
            raise claude.ClaudeInvocationError("boom", RECORD)
        return claude.InvocationResult({"findings": findings}, RECORD)


class OmitFirstRebuttal(SecurityFake):
    """反証役の出力から、いつも先頭の候補（index 0）を欠く。走らせ直しでも欠くので未検証が残る。"""

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        result = super().__call__(invocation)
        if "反証役です" not in invocation.prompt:
            return result
        items = (result.structured_output or {})["items"]
        return claude.InvocationResult({"items": [i for i in items if i["index"] != 0]}, RECORD)


def run(review: Review, fake: SecurityFake) -> security.SecurityRun:
    return security.run_security(
        AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5)
    )


def discovery_calls(fake: SecurityFake) -> list[claude.Invocation]:
    return [c for c in fake.calls if "担当のシャード" in c.prompt]


def test_default_run_needs_no_mcp_json_and_passes_no_mcp_config(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert not (data_home() / "mcp.json").exists()
    fake = SecurityFake({1: [], 2: []})
    run(review, fake)
    discovery = discovery_calls(fake)
    assert len(discovery) == 2
    for call in discovery:
        assert call.mcp_config is None
        assert call.allowed_mcp_tools == ()
        assert call.tools == ("Read", "Grep", "Glob")
        command = claude.build_command(call)
        assert "--mcp-config" not in command
        assert "--allowedTools" not in command
        assert "--strict-mcp-config" in command


def test_mcps_without_mcp_json_stops_before_invoking(project: Path) -> None:
    create_feature_review(project, ["src"], "p")
    with pytest.raises(mcp.SecurityConfigError) as raised:
        McpSelection.load(("owasp-mas",))
    assert str(data_home() / "mcp.json") in str(raised.value)


def test_mcps_naming_an_absent_server_stops_before_invoking(project: Path) -> None:
    create_feature_review(project, ["src"], "p")
    write_mcp(("owasp-mas",))
    with pytest.raises(mcp.SecurityConfigError) as raised:
        McpSelection.load(("owasp-mas", "backendsec", "other"))
    message = str(raised.value)
    assert "backendsec" in message
    assert "other" in message
    assert "owasp-mas," not in message


class ConfigCapture(SecurityFake):
    """起動の最中に、渡された設定ファイルの中身と権限を読む。"""

    def __init__(self) -> None:
        super().__init__({1: [], 2: []})
        self.seen: list[tuple[Path, dict[str, Any], int]] = []

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        if invocation.mcp_config is not None:
            path = invocation.mcp_config
            self.seen.append(
                (path, json.loads(path.read_text(encoding="utf-8")), path.stat().st_mode & 0o777)
            )
        return super().__call__(invocation)


def test_mcps_writes_only_the_selected_servers_to_a_temporary_file(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp(("owasp-mas", "backendsec", "unrelated"))
    fake = ConfigCapture()
    security.run_security(
        AssemblyInput.load(review), settings_for(fake), McpSelection.load(("owasp-mas",))
    )
    discovery = discovery_calls(fake)
    assert len(discovery) == 2
    assert fake.seen
    for path, content, _mode in fake.seen:
        assert set(content["mcpServers"]) == {"owasp-mas"}
        assert path != data_home() / "mcp.json"
        assert not path.exists()
    for call in discovery:
        assert call.allowed_mcp_tools == (
            "mcp__owasp-mas",
            "ReadMcpResourceTool",
            "ListMcpResourcesTool",
        )
        assert {"Read", "Grep", "Glob", "ReadMcpResourceTool"} <= set(call.tools)
        command = claude.build_command(call)
        assert "--strict-mcp-config" in command
        assert "--allowedTools" in command


def test_run_review_passes_mcps_to_the_security_group(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp(("owasp-mas", "backendsec"))
    fake = ConfigCapture()
    run_review(str(review.id), SECURITY, invoke=fake, mcps=("owasp-mas", "backendsec"))
    discovery = discovery_calls(fake)
    assert discovery
    for path, content, _mode in fake.seen:
        assert set(content["mcpServers"]) == {"owasp-mas", "backendsec"}
        assert not path.exists()
    for call in discovery:
        assert "mcp__owasp-mas" in call.allowed_mcp_tools
        assert "mcp__backendsec" in call.allowed_mcp_tools
        assert "--mcp-config" in claude.build_command(call)


def test_run_review_without_mcps_passes_no_mcp_config(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = SecurityFake({1: [], 2: []})
    run_review(str(review.id), SECURITY, invoke=fake)
    discovery = discovery_calls(fake)
    assert discovery
    for call in discovery:
        assert call.mcp_config is None
        assert "--mcp-config" not in claude.build_command(call)


def test_temporary_mcp_file_is_removed_when_the_run_fails(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    fake = ConfigCapture()
    fake.by_shard = {1: None, 2: None}
    with pytest.raises(Exception, match="発見役が失敗"):
        security.run_security(
            AssemblyInput.load(review), settings_for(fake), McpSelection.load(("owasp-mas",))
        )
    assert fake.seen
    assert all(not path.exists() for path, _, _ in fake.seen)


def test_mcps_is_rejected_for_other_groups_before_invoking(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = SecurityFake({1: [], 2: []})
    for group in ("micro", "macro"):
        code = main(["run", str(review.id), group, "--mcps", "owasp-mas"], invoke=fake)
        assert code != EXIT_OK
        assert "--mcps" in capsys.readouterr().err
    assert fake.calls == []


@pytest.mark.parametrize(
    ("viewpoint", "valid"),
    [
        ("S-ASVS-V1", True),
        ("S-ASVS-V9", True),
        ("S-ASVS-V17", True),
        ("S-ASVS-V18", False),
        ("S-ASVS-V0", False),
        ("S-ASVS-V117", False),
        ("S-STORAGE", True),
        ("S-UNKNOWN", False),
        ("D1", False),
    ],
)
def test_viewpoint_pattern(viewpoint: str, *, valid: bool) -> None:
    document = {"findings": [s_finding(viewpoint)]}
    validator = jsonschema.Draft202012Validator(security.discovery_schema())
    assert validator.is_valid(document) is valid


def test_references_required() -> None:
    document = {"findings": [s_finding(references=())]}
    assert not jsonschema.Draft202012Validator(security.discovery_schema()).is_valid(document)


def test_shard_without_references_is_failure(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    fake = SecurityFake({1: [s_finding(references=())], 2: []})
    result = run(review, fake)
    assert result.failed_shards == 1
    assert triaged_dicts(result.findings) == []


def test_macro_actionable_does_not_defer(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    state = read_stored_state(review.id.directory())
    state.groups[MACRO] = (
        stored_finding(
            {
                "id": "F1",
                "severity": "warning",
                "viewpoint": "V",
                "claim": "C",
                "adjudication": {
                    "kind": "rebuttal",
                    "challenge": "c",
                    "recheck": [],
                    "reason": "r",
                    "verdict": "kept",
                },
                "triage": "must_read",
            }
        ),
    )
    save_findings_state(review.id.directory(), state)
    fake = SecurityFake(
        {1: [s_finding(severity="critical")], 2: [s_finding("S-CRYPTO", "src/b.py", "nit")]}
    )
    result = run(review, fake)
    assert sorted(f["triage"] for f in triaged_dicts(result.findings)) == [
        "must_read",
        "remaining",
    ]


def test_triage_and_rejection(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    fake = SecurityFake({1: [s_finding(severity="warning")], 2: []}, verdict="rejected")
    result = run(review, fake)
    assert [f["triage"] for f in triaged_dicts(result.findings)] == ["rejected"]
    assert triaged_dicts(result.findings)[0]["group"] == "security"


def test_evidence_outside_shard_is_dropped(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    fake = SecurityFake({1: [s_finding(path="src/b.py")], 2: []})
    result = run(review, fake)
    assert triaged_dicts(result.findings) == []
    assert result.run_state.dropped_outside_shard == 1


def test_cli_exit_codes_and_show(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    name = str(review.id)
    write_mcp()
    partial = SecurityFake({1: [s_finding()], 2: None})
    assert main(["run", name, "security"], invoke=partial) == EXIT_INCOMPLETE_COVERAGE
    none = SecurityFake({1: None, 2: None})
    assert main(["run", name, "security"], invoke=none) == EXIT_PROCESSING_FAILURE
    ok = SecurityFake({1: [s_finding()], 2: []})
    assert main(["run", name, "security"], invoke=ok) == EXIT_OK
    capsys.readouterr()
    assert main(["show", name, "security"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[必読]" in out
    assert "未検証" not in out
    assert main(["show", name, "security", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["findings"][0]["group"] == "security"
    assert main(["estimate", name, "security"]) == EXIT_OK


def test_show_security_places_unverified_after_must_read_and_json_marks_it(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    name = str(review.id)
    write_mcp()
    fake = OmitFirstRebuttal({1: [s_finding(), s_finding(path="src/a.py")], 2: []})
    assert main(["run", name, "security"], invoke=fake) == EXIT_INCOMPLETE_COVERAGE
    capsys.readouterr()
    assert main(["show", name, "security"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.index("[必読]") < out.index("[未検証（1 件）]")
    assert out.index("[未検証（1 件）]") < out.index("F2 ")
    assert main(["show", name, "security", "--json"]) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert [f["triage"] for f in body["findings"]].count("unverified") == 1
    assert "unverified" in {t["name"] for t in body["triages"]}
    assert body["run"]["failed_splits"] == 1
    assert body["run"]["unverified_candidates"] == 1


def test_show_without_run_is_a_state(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert main(["show", str(review.id), "security"]) == EXIT_OK


def test_findings_always_carry_adjudication(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    result = run(review, SecurityFake({1: [s_finding()], 2: []}))
    assert triaged_dicts(result.findings)
    assert all(f["adjudication"] is not None for f in triaged_dicts(result.findings))


def test_estimate_json_has_no_blockers_key(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert main(["estimate", str(review.id), "security", "--json"]) == EXIT_OK
    assert "blockers" not in json.loads(capsys.readouterr().out)


def test_prompt_names_no_specific_mcp_with_or_without_servers(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    shard = shards.build_plan(review, security.GROUP).shards[0]
    assembly = AssemblyInput.load(review)
    without = shard_prompt(SECURITY, assembly, shard, 1, 1)
    write_mcp(("refs-a", "refs-b"))
    servers = McpSelection.load(("refs-a", "refs-b"))
    with_servers = shard_prompt(SECURITY, assembly, shard, 1, 1, servers)
    for prompt in (without, with_servers):
        assert "owasp-mas" not in prompt
        assert "backendsec" not in prompt
        assert "$mcp_note" not in prompt
    assert "MCP は渡されていない" in without
    assert "refs-a" not in without
    assert "次の MCP が使える: refs-a, refs-b" in with_servers
    assert "MCP は渡されていない" not in with_servers


def test_run_records_the_mcp_servers_and_show_prints_them(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp(("refs-a",))
    fake = SecurityFake({1: [s_finding()], 2: []})
    security.run_security(
        AssemblyInput.load(review),
        settings_for(fake, max_budget_usd=0.5),
        McpSelection.load(("refs-a",)),
    )
    stored = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))["security"]
    assert stored["mcp_servers"] == ["refs-a"]
    assert main(["show", str(review.id), "security"]) == EXIT_OK
    assert "MCP: refs-a" in capsys.readouterr().out
    assert main(["usage", str(review.id)]) == EXIT_OK
    assert "MCP: refs-a" in capsys.readouterr().out


def test_run_without_mcps_records_an_empty_list_and_old_records_read_as_unknown(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    run(review, SecurityFake({1: [s_finding()], 2: []}))
    path = review.id.directory() / "runs.json"
    runs = json.loads(path.read_text("utf-8"))
    assert runs["security"]["mcp_servers"] == []
    assert main(["usage", str(review.id)]) == EXIT_OK
    assert "MCP: 無し" in capsys.readouterr().out
    del runs["security"]["mcp_servers"]
    path.write_text(json.dumps(runs), encoding="utf-8")
    assert main(["usage", str(review.id)]) == EXIT_OK
    assert "MCP: 不明" in capsys.readouterr().out
    assert main(["show", str(review.id), "security"]) == EXIT_OK
    assert "MCP: 不明" in capsys.readouterr().out


def test_mcps_error_stops_before_consent_and_recording(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp(("owasp-mas",))
    fake = SecurityFake({1: [], 2: []})
    asked: list[str] = []

    def read(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    code = main(
        ["run", str(review.id), "security", "--mcps", "absent"],
        invoke=fake,
        is_terminal=lambda: True,
        read_answer=read,
    )
    assert code != EXIT_OK
    assert asked == []
    assert fake.calls == []
    assert not (review.id.directory() / "runs.json").exists()
    assert "absent" in capsys.readouterr().err
