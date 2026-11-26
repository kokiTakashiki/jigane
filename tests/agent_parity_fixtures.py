"""E1c の合成レビュー。起動要求を記録し、上流 CLI は呼ばない。"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fakes import RECORD
from pr_support import commit_all, git

from jigane.commands import main
from jigane.commands.subcommands import run as run_subcommand
from jigane.design.review_documents import create_design_doc_review
from jigane.git import process
from jigane.groups.definitions import GroupId
from jigane.llm_runs import claude, codex, pricing
from jigane.llm_runs.adapter import adapter_for
from jigane.llm_runs.invocation import Invocation, InvocationResult
from jigane.reviews.lifecycle import Review, create_range_review
from jigane.skills import mcp
from jigane.storage.agent_selection import Agent, save_agent
from jigane.storage.store import data_home

MCP_NAMES = ("fixture_one", "fixture_two")
MCP_GUIDE = ("合成のバックエンド基準", "合成の CI 基準")
SOURCE = (
    "class _Internal:\n    pass\n\n"
    "NAMES = ('alpha', 'beta', 'gamma')\n\n"
    "def sample() -> list[_Internal]:\n    return []\n\n"
    "def known(value):\n    return value in NAMES\n"
)
USE = """\
def check(mode):
    a = mode == 'alpha'
    b = mode in ['beta', 'gamma']
    return a or b or mode.startswith('alp') or mode.endswith('ma')
"""
DESIGN = "# 設計\n\n## 目的\n\n`sample` は項目を返す。\n"


@dataclass(frozen=True)
class AgentPlan:
    calls: tuple[Invocation, ...]
    estimated_bytes: int

    @property
    def bodies(self) -> tuple[str, ...]:
        return tuple(call.prompt for call in self.calls)

    @property
    def prompt_lines(self) -> set[str]:
        return {line for body in self.bodies for line in body.splitlines()}

    @property
    def roles(self) -> tuple[tuple[str, str | None], ...]:
        return tuple((call.policy_role or "discovery", call.principle) for call in self.calls)

    @property
    def body_bytes(self) -> int:
        return sum(len(body.encode("utf-8")) for body in self.bodies)


def _fixed_price(monkeypatch: pytest.MonkeyPatch) -> None:
    claude_price = pricing.ModelPrices(1.0, (pricing.PriceTier(None, 1.0, 1.0, 1.0),))
    for model in pricing.MODEL_PRICES:
        monkeypatch.setitem(pricing.MODEL_PRICES, model, claude_price)
        monkeypatch.setitem(pricing.INPUT_USD_PER_MILLION_TOKENS, model, 1.0)
    price = 1_000
    monkeypatch.setattr(codex, "LOGIN_MODEL", codex.Model("fixture", price, price, price, price))
    for principle in codex._PRINCIPLE_MODELS:
        monkeypatch.setitem(codex._PRINCIPLE_MODELS, principle, codex.LOGIN_MODEL)


def _mcp_args(root: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    claude_registrations = home / mcp.MCP_CONFIG_DIRECTORY
    claude_registrations.mkdir(mode=0o700)
    (home / mcp.CODEX_MCP_DIRECTORY).mkdir(mode=0o700)
    for name in MCP_NAMES:
        registration = claude_registrations / f"{name}.json"
        registration.write_text("{}", encoding="utf-8")
        registration.chmod(0o600)
    # 実行ファイルの確認だけを差し替え、名前と TOML の検査および用途の読み込みを通す。
    monkeypatch.setattr(mcp, "_codex_executable", lambda path, _name: path)
    guide = root / "guide.toml"
    guide.write_text(
        "".join(
            f'[mcps.{name}]\nuse_for = "{use}"\n'
            for name, use in zip(MCP_NAMES, MCP_GUIDE, strict=True)
        ),
        encoding="utf-8",
    )
    return ["--mcps", *MCP_NAMES, "--mcp-guide", str(guide)]


def _create_review(root: Path, case: str, group: GroupId) -> Review:
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/example/parity.git")
    sample = root / "sample.py"
    sharded = group is not GroupId.MACRO
    source = SOURCE.replace("    return", "    # unchanged context\n" * 8 + "    return")
    sample.write_text(source if sharded else "def sample() -> int:\n    return 1\n", "utf-8")
    commit_all(root, "initial")
    if sharded:
        # 同じファイルの離れた 2 ハンク。縮約経路だけがファイルを分割する。
        padding = "    # " + "abcdefghij" * 30 + "\n"
        sample.write_text(source.replace("    return", padding * 61 + "    return"), "utf-8")
    else:
        sample.write_text(SOURCE, encoding="utf-8")
        (root / "use.py").write_text(USE, encoding="utf-8")
    commit_all(root, "change sample")
    if case == "design":
        (root / "design.md").write_text(DESIGN, encoding="utf-8")
        return create_design_doc_review(root, "design.md", select=["sample.py"])
    return create_range_review(root, "HEAD~1..HEAD", case)


@pytest.fixture
def plans(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[AgentPlan, AgentPlan]:
    _fixed_price(monkeypatch)
    case: str = request.param
    group = GroupId.MACRO if case in {"diagram", "design"} else GroupId(case)
    root = tmp_path / "repo"
    review = _create_review(root, case, group)
    monkeypatch.chdir(root)
    home = data_home()
    args = [str(review.id), group.value, "--json"]
    if group is GroupId.SECURITY:
        args += _mcp_args(root, home, monkeypatch)

    def unexpected_launch(*_args: object, **_kwargs: object) -> InvocationResult:
        pytest.fail("外部のレビュー CLI は起動しない")

    monkeypatch.setattr(process, "run_codex_resolved", unexpected_launch)
    monkeypatch.setattr(claude, "run_claude_resolved", unexpected_launch)
    monkeypatch.setattr(run_subcommand, "codex_supported", lambda: True)

    def record(agent: Agent) -> AgentPlan:
        calls: list[Invocation] = []

        def invoke(call: Invocation) -> InvocationResult:
            calls.append(call)
            output: dict[str, Any] = {"findings": []}
            if group is GroupId.MACRO and call.principle is None:
                output.update(merged=[], discarded=[])
            return InvocationResult(output, RECORD)

        # 両方の実行は同じ初期履歴の写しを読む。
        agent_home = tmp_path / agent.value
        shutil.copytree(home, agent_home)
        agent_home.chmod(0o700)
        monkeypatch.setenv("JIGANE_HOME", str(agent_home))
        save_agent(agent)
        monkeypatch.setattr(codex, "run", unexpected_launch)
        estimate_status = main(["estimate", *args], invoke=unexpected_launch)
        estimate = capsys.readouterr()
        if estimate_status != 0:
            pytest.fail(estimate.err)
        adapter = adapter_for(agent, invoke_override=invoke)
        # Codex の入口は run を直接呼ぶ。起動口を注入できるようになったら差し替えを外す。
        monkeypatch.setattr(codex, "run", adapter.invoke)
        status = main(
            ["run", *args],
            invoke=adapter.invoke,
            is_terminal=lambda: True,
            read_answer=lambda _: "y",
        )
        result = capsys.readouterr()
        if status != 0 or not calls:
            pytest.fail(f"{agent}: {status=} {result.err} {result.out}")
        # 並列の起動が偽物へ着く順序には意味がない。
        ordered = tuple(sorted(calls, key=lambda call: (call.principle or "", call.prompt)))
        report = json.loads(estimate.out)
        sizes = report.get("prompt_bytes")
        return AgentPlan(ordered, sum(sizes) if sizes is not None else report["materials_bytes"])

    return record(Agent.CLAUDE), record(Agent.CODEX)
