"""P5 の資料がある差分でも、両版が通常の原則だけを計画することを確かめる。"""

import json
import os
import sys
from pathlib import Path

import pytest
from pr_support import commit_all

from jigane.commands import main
from jigane.groups.policy_runs import PolicyRole
from jigane.llm_runs import codex
from jigane.reviews.catalog import load_review
from jigane.reviews.resolution import resolve_review
from jigane.skills.assembly import AssemblyInput
from jigane.skills.macro_principles_run import principle_launches
from tests_posix.codex_cli_support import FAKE_CODEX, fake_chatgpt_login

RESPONSE = """\
props = schema["properties"]["findings"]["items"]["properties"]["viewpoint"]
viewpoints = props.get("enum", [props.get("const")])
assert "P5" not in viewpoints
output = {"findings": []}
if "merged" in schema["properties"]:
    output.update(merged=[], discarded=[])
Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(output))
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 100
}}))
"""


@pytest.fixture
def codex_trace(synthetic_diff: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trace = synthetic_diff / "calls"
    binary = synthetic_diff / "codex"
    binary.write_text(
        f"#!{sys.executable}\n"
        + FAKE_CODEX.replace("{marker!r}", repr(str(trace))).replace("{response}", RESPONSE),
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", f"{synthetic_diff}{os.pathsep}{os.environ['PATH']}")
    fake_chatgpt_login(synthetic_diff, monkeypatch)
    return trace


@pytest.mark.parametrize(
    ("name", "source", "evidence"),
    [
        (
            "store",
            (
                'import json\n\nSTATE = "state.json"\n\n'
                'def sample():\n    with open(STATE, "w") as stream:\n'
                '        json.dump({"written": 1}, stream)\n\n'
                "def _load():\n    with open(STATE) as stream:\n"
                '        return json.load(stream)["expected"]\n'
            ),
            (
                "state.json (JSON)",
                "書 #1:5 sample: written",
                "読 #1:9 _load: expected",
                "鍵 書 written",
                "鍵 読 expected",
            ),
        ),
        (
            "signature",
            "class _Internal:\n    pass\n\n\ndef sample() -> list[_Internal]:\n    return []\n",
            ("#1:5 sample() list[_Internal]", "型 #1:1 sample._Internal"),
        ),
        (
            "unrelated",
            (
                "class _Internal:\n    pass\n\n\n"
                "def _helper() -> _Internal:\n    return _Internal()\n\n"
                "def sample() -> int:\n    return 3\n"
            ),
            (),
        ),
    ],
    ids=["store", "signature", "unrelated"],
)
def test_neither_agent_plans_p5_when_p5_shapes_are_present(
    codex_trace: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    source: str,
    evidence: tuple[str, ...],
) -> None:
    repo = codex_trace.parent / "repo"
    (repo / "sample.py").write_text(source, encoding="utf-8")
    commit_all(repo, name)
    assert main(["new", "range", "HEAD~1..HEAD", "--name", name]) == 0
    args = [f"octo/synthetic@{name}", "macro", "--json"]
    review = load_review(resolve_review(args[0]))
    compact = principle_launches(AssemblyInput.load(review), compact=True)
    p5 = [launch.prompt for launch in compact if launch.viewpoint == "P5"]
    assert len(p5) == int(bool(evidence))
    if evidence:
        assert all(fragment in p5[0] for fragment in evidence)

    plans = {}
    for agent in ("claude", "codex"):
        assert main(["select-agent", agent]) == 0
        capsys.readouterr()
        assert main(["estimate", *args]) == 0
        estimate = json.loads(capsys.readouterr().out)
        assert main(["debug", "prompt", *args]) == 0
        prompts = json.loads(capsys.readouterr().out)["prompts"]
        plans[agent] = prompts
        assert estimate["planned_invocations"] == len(prompts)
        assert all("P5 情報隠蔽" not in prompt["text"] for prompt in prompts)
        if agent == "codex":
            assert estimate["prompt_bytes"] == [p["bytes"] for p in prompts]
            assert all(row["principle"] != "P5" for row in estimate["launches"])

    assert plans["claude"] == plans["codex"]
    assert not codex_trace.exists()
    assert main(["run", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    calls = len(plans["codex"])
    assert len(result["run"]["invocations"]) == calls
    assert len(codex_trace.read_text(encoding="utf-8").splitlines()) == calls
    stored = json.loads((review.id.directory() / "runs.json").read_text(encoding="utf-8"))
    assert all(row.get("viewpoint") != "P5" for row in stored["macro"]["invocations"])


def test_codex_keeps_the_p5_model_assignment() -> None:
    model = codex.model_for(PolicyRole.PRINCIPLE, "P5")
    assert (model.name, model.effort) == ("gpt-6.1-terra", "high")
