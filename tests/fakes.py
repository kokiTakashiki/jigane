import json
import re
import shlex
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, overload

import pytest
from tree_sitter import Node

from jigane.findings.records import GroupResult
from jigane.groups.definitions import MACRO, Effort
from jigane.languages import registry
from jigane.languages.base import Declaration
from jigane.languages.java import JavaAdapter
from jigane.languages.plugin import Distribution
from jigane.llm_runs import claude
from jigane.llm_runs.cost import Measured
from jigane.reviews.lifecycle import Review
from jigane.skills import micro, pipeline
from jigane.skills.pipeline import GroupRun, PolicyGroupRun, ShardedGroupRun

RECORD = claude.InvocationRecord(
    1,
    2,
    0,
    0,
    Measured(0.1),
    10,
    1,
    is_error=False,
    terminal_reason=None,
    permission_denials=0,
    max_budget_usd=0.5,
)
RECORD_ROW = {"role": "discovery", "split_index": 0, **RECORD.as_row()}
LOCATION = {"path": "a.py", "start_line": 1, "end_line": 2}
# 偽の起動が束ねの役を見分ける印。prompts/bundle_disclosed.md の冒頭の文と合わせる
PROMPT_MARKER = "開示済みの限界の指摘を、同じ既知の限界ごとに束ねる役"


def run_command_stub(handler: Callable[[list[str]], str]) -> Callable[..., str]:
    """process.run_command と同じ引数の形を持ち、args だけを handler へ渡す偽物を返す。"""

    def run_command(
        args: list[str],
        cwd: Path | None = None,  # noqa: ARG001 - 本物と同じ形にして、呼ぶ側の渡し方を確かめさせる
        stdin: str | None = None,  # noqa: ARG001 - 同上
        env: dict[str, str] | None = None,  # noqa: ARG001 - 同上
    ) -> str:
        return handler(args)

    return run_command


FAKE_CLAUDE_SCRIPT = Path(__file__).parent.parent / "scripts" / "fake_claude.py"


def write_fake_claude_wrapper(
    directory: Path,
    python: str = sys.executable,
    script: Path = FAKE_CLAUDE_SCRIPT,
    leading_arguments: Sequence[str] = (),
) -> Path:
    """偽の claude を起こす sh の包みを書く。

    shebang は空白を含むパスの解釈器を指せないため、包みの中で python と
    スクリプトを引用して呼ぶ。leading_arguments は claude の引数の前に足す。
    """
    wrapper = directory / "fake-claude"
    command = " ".join(shlex.quote(part) for part in (python, str(script), *leading_arguments))
    wrapper.write_text(f'#!/bin/sh\nexec {command} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def clear_policy(review: Review) -> None:
    """方針を実行済みで必読が 0 件の状態にする。実装の実行の関門を通すための前置き。"""
    pipeline.save_run(review, GroupResult(()), pipeline.PolicyGroupRun.from_json({}), MACRO)


def embedded_json(prompt: str, marker: str) -> list[dict[str, Any]]:
    # プロンプトの「marker (JSON):」の次の行から、次の空行までの JSON を取り出す。
    after = prompt.split(f"{marker} (JSON):\n", 1)[1]
    records: list[dict[str, Any]] = json.loads(re.split(r"\n\n|\Z", after, maxsplit=1)[0])
    return records


class FakeInvoke:
    def __init__(
        self, verdict: str = "kept", drop_index: int | None = None, fail_on: int | None = None
    ) -> None:
        self.calls: list[claude.Invocation] = []
        self.verdict = verdict
        self.drop_index = drop_index
        self.fail_on = fail_on
        # 判定役の各項目に添える開示の場所。None なら添えない
        self.disclosed_at: str | None = None
        # 開示済みの限界の束ねの起動への応答。空なら何もまとめない
        self.bundle_groups: list[list[int]] = []

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        self.calls.append(invocation)
        if PROMPT_MARKER in invocation.prompt:
            return claude.InvocationResult({"groups": self.bundle_groups}, RECORD)
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise claude.ClaudeInvocationError("boom", RECORD)
        prompt = invocation.prompt
        candidates = embedded_json(prompt, "候補")
        items = []
        for candidate in candidates:
            i = candidate["index"]
            if i == self.drop_index:
                continue
            item = {"index": i}
            if "反証役です" in prompt:
                item |= {"challenge": f"ch{i}", "checked": [LOCATION]}
            elif "代案役です" in prompt:
                item["alternative"] = f"alt{i}"
            elif "擁護役です" in prompt:
                item["defense"] = f"def{i}"
            elif "反証を受けて判定" in prompt:
                item |= {
                    "recheck": [LOCATION],
                    "verdict": self.verdict,
                    "reason": "r",
                    "verified": True,
                    "tool_claim": {"relies": False, "basis": "none", "version": ""},
                }
            else:
                item |= {"verdict": self.verdict, "reason": "r"}
            if self.disclosed_at is not None and "を受けて判定" in prompt:
                item["disclosed_at"] = self.disclosed_at
            items.append(item)
        return claude.InvocationResult({"items": items}, RECORD)


def discovered(viewpoint: str = "M2", path: str = "src/a.py") -> dict[str, Any]:
    return {
        "severity": "warning",
        "viewpoint": viewpoint,
        "claim": f"claim {viewpoint}",
        "body": "b",
        "evidence": [{"path": path, "start_line": 1, "end_line": 2}],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
    }


class MacroFake:
    """図だけの形の一回の起動を受ける偽の起動関数。

    findings は応答の指摘で、in_scope を真にして返す。output を渡すと、その辞書をそのまま返す。
    """

    def __init__(
        self,
        findings: list[dict[str, Any]],
        *,
        fail_discovery: bool = False,
        output: dict[str, Any] | None = None,
    ) -> None:
        self.findings = findings
        self.fail_discovery = fail_discovery
        self.output = output
        self.calls: list[claude.Invocation] = []

    @property
    def prompt(self) -> str:
        return self.calls[-1].prompt

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        self.calls.append(invocation)
        if self.fail_discovery:
            raise claude.ClaudeInvocationError("boom", RECORD)
        if self.output is not None:
            return claude.InvocationResult(self.output, RECORD)
        findings = [{**f, "in_scope": True, "scope_reason": ""} for f in self.findings]
        return claude.InvocationResult(
            {"findings": findings, "merged": [], "discarded": []}, RECORD
        )


def d_finding(viewpoint: str = "D1", path: str = "src/a.py") -> dict[str, Any]:
    # 実装とセキュリティの発見役の出力は、範囲の欄（in_scope と scope_reason）を必ず持つ
    return {**discovered(viewpoint, path), "in_scope": True, "scope_reason": ""}


THEMES_MARKER = "テーマへ束ねる役"


class MicroFake(FakeInvoke):
    def __init__(
        self, by_shard: dict[int, list[dict[str, Any]] | None], verdict: str = "kept"
    ) -> None:
        super().__init__(verdict=verdict)
        self.by_shard = by_shard
        # 束ねの起動への応答。None なら何も束ねない（全指摘が単独のテーマになる）。
        self.themes: list[dict[str, Any]] | None = []
        self.theme_prompts: list[str] = []
        self.lock = threading.Lock()
        self.shard_prompts: dict[int, str] = {}

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        if THEMES_MARKER in invocation.prompt:
            with self.lock:
                self.calls.append(invocation)
                self.theme_prompts.append(invocation.prompt)
            if self.themes is None:
                raise claude.ClaudeInvocationError("boom", RECORD)
            return claude.InvocationResult({"themes": self.themes}, RECORD)
        if invocation.tools != micro.DISCOVERY_TOOLS:
            return super().__call__(invocation)
        match = re.search(r"担当のシャード: (\d+) / (\d+)", invocation.prompt)
        assert match
        number = int(match.group(1))
        with self.lock:
            self.calls.append(invocation)
            self.shard_prompts[number] = invocation.prompt
        findings = self.by_shard.get(number)
        if findings is None:
            raise claude.ClaudeInvocationError("boom", RECORD)
        return claude.InvocationResult({"findings": findings}, RECORD)


def settings_for(
    invoke: claude.Invoke, *, effort: Effort | None = None, max_budget_usd: float = 0.5
) -> claude.RunSettings:
    return claude.RunSettings(
        budget=claude.RunBudget("m", max_budget_usd, reserve_margin=True),
        effort=effort,
        invoke=invoke,
    )


def with_budget(
    settings: claude.RunSettings,
    *,
    model: str | None = None,
    max_budget_usd: float | None = None,
    reserve_margin: bool | None = None,
) -> claude.RunSettings:
    """settings の RunBudget の、渡した欄だけを差し替えた設定。"""
    budget = settings.budget
    return replace(
        settings,
        budget=claude.RunBudget(
            budget.model if model is None else model,
            budget.max_budget_usd if max_budget_usd is None else max_budget_usd,
            budget.reserve_margin if reserve_margin is None else reserve_margin,
        ),
    )


class NoSequenceAdapter:
    """宣言の読み取りだけを持ち、シーケンス図の口を持たない言語アダプタ。拡張子は .nosq。"""

    name = "no-sequence"
    grammar = "java"
    suffixes = frozenset({".nosq"})
    comment_node_types = JavaAdapter.comment_node_types
    import_node_types = JavaAdapter.import_node_types
    test_directory_names = JavaAdapter.test_directory_names
    test_file_patterns = JavaAdapter.test_file_patterns
    build_file_patterns = JavaAdapter.build_file_patterns

    def declarations(self, root_node: Node, source: bytes) -> list[Declaration]:
        return JavaAdapter().declarations(root_node, source)


def register_no_sequence_adapter(
    monkeypatch: pytest.MonkeyPatch, *, external: bool = False
) -> None:
    """external が真なら外から入れた言語として、偽なら組み込みの言語の複製として登録する。"""
    java = next(plugin for plugin in registry.builtin_plugins() if plugin.adapter.name == "java")
    origin = Distribution("fake-nosq", "1.0.0") if external else java.origin
    fake = replace(java, adapter=NoSequenceAdapter(), label="no-sequence", origin=origin)
    monkeypatch.setattr(registry, "_plugins", (*registry.builtin_plugins(), fake))


@overload
def as_run(value: None, *, policy: bool = False) -> None: ...
@overload
def as_run(value: "GroupRun | dict[str, Any]", *, policy: bool = False) -> GroupRun: ...
def as_run(
    value: "GroupRun | dict[str, Any] | None", *, policy: bool = False
) -> "GroupRun | None":
    """runs.json の辞書を型へ読む。型と None はそのまま返す。policy は方針の群の記録として読む。"""
    if value is None or isinstance(value, GroupRun):
        return value
    return PolicyGroupRun.from_json(value) if policy else ShardedGroupRun.from_json(value)
