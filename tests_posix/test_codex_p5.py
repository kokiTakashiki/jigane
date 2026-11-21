"""実際の差分から P5 を選び、公開 CLI で見積もりと保存を確かめる。"""

import json
import os
import sys
from pathlib import Path

import pytest
from pr_support import commit_all

from jigane.commands import main
from jigane.llm_runs import codex
from jigane.reviews.resolution import resolve_review
from tests_posix.codex_cli_support import FAKE_CODEX, fake_chatgpt_login

P5_RESPONSE = """\
from jsonschema import Draft202012Validator

props = schema["properties"]["findings"]["items"]["properties"]["viewpoint"]
viewpoints = props.get("enum", [props.get("const")])
output = {"findings": []}
if "merged" in schema["properties"]:
    output.update(merged=[], discarded=[])
if viewpoints == ["P5"]:
    assert args[args.index("--model") + 1] == "gpt-6.1-terra"
    assert 'model_reasoning_effort="high"' in args
    assert "P5 情報隠蔽" in prompt
    output["findings"].append({
        "severity": "warning",
        "viewpoint": "P5",
        "claim": "Keep the internal representation behind its boundary",
        "body": "The supplied evidence shows the representation at the boundary.",
        "evidence": [{"path": "sample.py", "start_line": 5, "end_line": 5}],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
        "deepens": None,
        "in_scope": True,
        "scope_reason": "The entry point changed.",
    })
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    validator.validate(output)
    wrong = {**output["findings"][0], "viewpoint": "P4"}
    assert not validator.is_valid({"findings": [wrong]})
Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(output))
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 5
}}))
"""


@pytest.fixture
def codex_trace(synthetic_diff: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trace = synthetic_diff / "calls"
    binary = synthetic_diff / "codex"
    binary.write_text(
        f"#!{sys.executable}\n"
        + FAKE_CODEX.replace("{marker!r}", repr(str(trace))).replace("{response}", P5_RESPONSE),
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
def test_codex_p5_uses_source_shapes_for_estimate_and_run(
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

    plans = {}
    for agent in ("claude", "codex"):
        assert main(["select-agent", agent]) == 0
        capsys.readouterr()
        assert main(["estimate", *args]) == 0
        estimate = json.loads(capsys.readouterr().out)
        assert main(["debug", "prompt", *args]) == 0
        prompts = json.loads(capsys.readouterr().out)["prompts"]
        plans[agent] = len(prompts)
        assert estimate["planned_invocations"] == len(prompts)
        p5 = [prompt for prompt in prompts if "P5 情報隠蔽" in prompt["text"]]
        if agent == "codex":
            assert [p["bytes"] + len(codex.REVIEW_BOUNDARY.encode("utf-8")) for p in prompts] == (
                estimate["prompt_bytes"]
            )
            assert "P1〜P5" in estimate["run_guide"]["text"]
            assert len(p5) == int(bool(evidence))
            if p5:
                assert all(fragment in p5[0]["text"] for fragment in evidence)
        else:
            assert "P1〜P4" in estimate["run_guide"]["text"]
            assert not p5

    assert plans["codex"] - plans["claude"] == int(bool(evidence))
    assert not codex_trace.exists()
    assert main(["run", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["run"]["invocations"]) == plans["codex"]
    assert len(codex_trace.read_text(encoding="utf-8").splitlines()) == plans["codex"]
    directory = resolve_review(args[0]).directory()
    stored = json.loads((directory / "runs.json").read_text(encoding="utf-8"))
    assert main(["show", *args]) == 0
    shown = json.loads(capsys.readouterr().out)
    runs = [row for row in stored["macro"]["invocations"] if row.get("viewpoint") == "P5"]
    findings = [row for row in shown["findings"] if row["viewpoint"] == "P5"]
    assert len(runs) == len(findings) == int(bool(evidence))
    if runs:
        assert runs[0]["role"] == "principle"
        assert runs[0]["model"] == "gpt-6.1-terra"
        p5_id = "561ff3b2-0ec3-41bd-b611-7865229b46c7"
        assert findings[0]["viewpoint_id"] == p5_id
        saved_findings = json.loads((directory / "findings.json").read_text(encoding="utf-8"))
        assert saved_findings["groups"]["macro"][0]["viewpoint"] == p5_id
