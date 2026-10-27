import re
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import LOCATION, RECORD, FakeInvoke, embedded_json, settings_for
from legacy_schemas import load_finding_schema
from pydantic import BaseModel, ValidationError

from jigane.findings.records import AdjudicationKind, Verdict
from jigane.groups.definitions import MACRO
from jigane.llm_runs import claude
from jigane.llm_runs.cost import LimitSubstitute
from jigane.llm_runs.launch_failure import (
    CandidatesUnverified,
    Disposition,
    FailureScope,
    RerunKind,
    rerun_count,
    stopped_rerun_count,
)
from jigane.skills import adversarial, pipeline, shared_rules


def make_candidate(viewpoint: str = "D1", claim: str = "c") -> dict[str, Any]:
    return {
        "severity": "warning",
        "viewpoint": viewpoint,
        "claim": claim,
        "body": "b",
        "evidence": [LOCATION],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
    }


def run_verify(
    candidates: list[dict[str, Any]], fake: FakeInvoke
) -> adversarial.VerificationOutcome:
    return adversarial.verify(
        candidates,
        group=MACRO,
        changes="",
        working_directory=Path(),
        settings=settings_for(fake),
    )


def test_form_routing_and_role_order() -> None:
    fake = FakeInvoke()
    outcome = run_verify([make_candidate("M0"), make_candidate("D1")], fake)
    roles = [(r.role, r.split_index) for r in outcome.invocations]
    # 反証の形が先、代案の形が後。各形の中は役の順。
    assert roles == [
        ("rebuttal", 0),
        ("judge", 0),
        ("alternative", 1),
        ("defense", 1),
        ("judge", 1),
    ]
    by_viewpoint = {f.candidate["viewpoint"]: f.adjudication for f in outcome.findings}
    assert by_viewpoint["D1"].kind is AdjudicationKind.REBUTTAL
    assert by_viewpoint["M0"].kind is AdjudicationKind.ALTERNATIVE


@pytest.mark.parametrize("viewpoint", ["M0", "M2", "M3", "M5"])
def test_alternative_viewpoints(viewpoint: str) -> None:
    assert adversarial.uses_alternative_form(make_candidate(viewpoint))
    assert not adversarial.uses_alternative_form(make_candidate("M1"))


def test_split_by_ten() -> None:
    splits = adversarial.split_candidates([make_candidate() for _ in range(23)])
    assert [len(s) for _, s in splits] == [10, 10, 3]


def test_values_passed_to_later_roles() -> None:
    fake = FakeInvoke()
    run_verify([make_candidate("M2")], fake)
    defense_prompt, judge_prompt = fake.calls[1].prompt, fake.calls[2].prompt
    assert embedded_json(defense_prompt, "代案役の出力") == [{"index": 0, "alternative": "alt0"}]
    assert embedded_json(judge_prompt, "擁護役の出力") == [{"index": 0, "defense": "def0"}]
    assert embedded_json(judge_prompt, "代案役の出力")[0]["alternative"] == "alt0"
    assert all(c.working_directory == Path() for c in fake.calls)
    assert all(c.tools == ("Read",) for c in fake.calls)


def test_adjudication_validates_against_finding_schema() -> None:
    outcome = run_verify([make_candidate("D1"), make_candidate("M3")], FakeInvoke())
    validator = jsonschema.Draft202012Validator(load_finding_schema())
    for index, finding in enumerate(outcome.findings):
        full = {
            **finding.candidate,
            "adjudication": finding.adjudication.to_json(),
            "id": f"F{index + 1}",
            "group": "macro",
            "theme": None,
            "triage": None,
        }
        validator.validate(full)


def test_rejected_is_kept() -> None:
    outcome = run_verify([make_candidate("D1")], FakeInvoke(verdict="rejected"))
    assert [f.adjudication.verdict for f in outcome.findings] == [Verdict.REJECTED]


def omitting_claim(fake: FakeInvoke, claim: str, *, times: int | None = None) -> claude.Invoke:
    """役の出力から claim の候補の項目を欠く。times を渡すと、最初のその回数の起動だけ欠く。"""
    omitted = 0

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        nonlocal omitted
        result = fake(invocation)
        claims = {c["index"]: c["claim"] for c in embedded_json(invocation.prompt, "候補")}
        items = (result.structured_output or {})["items"]
        kept = [item for item in items if claims.get(item["index"]) != claim]
        if len(kept) == len(items) or (times is not None and omitted >= times):
            return result
        omitted += 1
        return claude.InvocationResult({"items": kept}, result.record)

    return invoke


def test_missing_index_keeps_returned_candidates_and_marks_the_rest_unverified() -> None:
    candidates = [make_candidate("D1", "x"), make_candidate("D1", "y")]
    outcome = verify_with(candidates, omitting_claim(FakeInvoke(), "y"))
    assert [f.candidate["claim"] for f in outcome.findings] == ["x"]
    assert outcome.failed_splits == 1
    assert outcome.unverified_candidates == 1
    assert [u.candidate["claim"] for u in outcome.unverified] == ["y"]
    assert [u.reason for u in outcome.unverified] == [adversarial.UnverifiedReason.STILL_MISSING]


def test_failed_split_returns_candidate_bodies() -> None:
    candidates = [make_candidate("D1", "x")]
    outcome = run_verify(candidates, FakeInvoke(fail_on=1))
    assert [u.candidate["claim"] for u in outcome.unverified] == ["x"]
    assert [u.reason for u in outcome.unverified] == [adversarial.UnverifiedReason.LAUNCH_FAILED]
    # 件数は列の長さから引くので、本体と食い違わない
    assert outcome.unverified_candidates == len(outcome.unverified)


def test_invocation_error_counts_and_continues() -> None:
    candidates = [make_candidate("D1"), make_candidate("M0")]
    fake = FakeInvoke(fail_on=1)
    outcome = run_verify(candidates, fake)
    assert outcome.failed_splits == 1
    assert outcome.unverified_candidates == 1
    assert [f.candidate["viewpoint"] for f in outcome.findings] == ["M0"]
    # 失敗した起動の記録も残る。
    assert outcome.invocations[0].role == "rebuttal"


def test_role_schemas_are_valid_and_accept_sample() -> None:
    schemas = adversarial.role_schemas()
    for schema in schemas.values():
        jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(
        {
            "items": [
                {
                    "index": 0,
                    "recheck": [LOCATION],
                    "verdict": "kept",
                    "reason": "r",
                    "verified": True,
                    "tool_claim": {"relies": False, "basis": "none", "version": ""},
                }
            ]
        },
        schemas["judge_rebuttal"],
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"items": [{"index": 0, "verdict": "maybe", "reason": "r"}]},
            schemas["judge_alternative"],
        )


@pytest.mark.parametrize(
    "name",
    ["rebuttal", "alternative", "defense", "judge_rebuttal", "judge_alternative"],
)
def test_prompt_templates_fully_substituted(name: str) -> None:
    template = adversarial.load_prompt(name)
    names = {
        m.group("named") or m.group("braced")
        for m in template.pattern.finditer(template.template)
        if m.group("named") or m.group("braced")
    }
    inserted = {rule.placeholder for rule in shared_rules.rules_for(name)}
    values: dict[str, list[str]] = {
        n: [] for n in names if n not in ("common", "changes") and n not in inserted
    }
    prompt = adversarial.render_prompt(name, "", **values).text
    assert not re.search(r"\$[A-Za-z_{]", prompt)


def test_missing_required_field_fails_split_only() -> None:
    fake = FakeInvoke()

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        result = fake(invocation)
        for item in (result.structured_output or {}).get("items", []):
            item.pop("challenge", None)
        return result

    outcome = adversarial.verify(
        [make_candidate("D1")],
        group=MACRO,
        changes="",
        working_directory=Path(),
        settings=settings_for(invoke),
    )
    assert outcome.failed_splits == 1
    assert outcome.unverified_candidates == 1


def test_verdict_out_of_enum_fails_the_split_instead_of_dropping_one_candidate() -> None:
    """語彙の外の verdict は、入口の検証で分割を BAD_OUTPUT の失敗にする。

    検証の前は、裁定を読む段で、その候補だけを落として数えていた（置き換え前の試験）。
    """
    fake = FakeInvoke()

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        result = fake(invocation)
        for item in (result.structured_output or {}).get("items", []):
            if "verdict" in item and item["index"] == 0:
                item["verdict"] = "maybe"
        return result

    outcome = adversarial.verify(
        [make_candidate("D1", "first"), make_candidate("D1", "second")],
        group=MACRO,
        changes="",
        working_directory=Path(),
        settings=settings_for(invoke),
    )
    assert outcome.findings == ()
    assert outcome.failed_splits == 1
    assert outcome.unverified_candidates == 2


def test_adjudication_missing_its_model_fails_the_split_as_bad_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Small(BaseModel):
        count: int

    try:
        Small.model_validate({"count": "x"})
    except ValidationError as error:
        failure_to_raise = error

    def missing_the_model(*_arguments: object) -> object:
        raise failure_to_raise

    monkeypatch.setattr(pipeline, "adjudication_from_model_output", missing_the_model)
    outcome = run_verify([make_candidate("D1", "first")], FakeInvoke())
    assert outcome.findings == ()
    assert outcome.failed_splits == 1
    assert outcome.unverified_candidates == 1
    failure = outcome.failures[-1]
    assert failure.kind is claude.FailureKind.BAD_OUTPUT
    assert failure.role == "judge"
    assert "裁定が裁定の型に合いません: count: " in failure.reason


def judge_prompt_for(tmp_path: Path, checked: list[dict[str, Any]]) -> str:
    fake = FakeInvoke()

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        result = fake(invocation)
        for item in (result.structured_output or {}).get("items", []):
            if "challenge" in item:
                item["checked"] = checked
        return result

    outcome = adversarial.verify(
        [make_candidate("D1")],
        group=MACRO,
        changes="",
        working_directory=tmp_path,
        settings=settings_for(invoke),
    )
    assert outcome.failed_splits == 0
    return fake.calls[-1].prompt


def excerpts_in(prompt: str) -> list[dict[str, Any]]:
    return embedded_json(prompt, "反証役が確かめた位置と抜粋")


def test_judge_prompt_carries_excerpt_built_from_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    prompt = judge_prompt_for(tmp_path, [{"path": "a.py", "start_line": 2, "end_line": 3}])
    [entry] = excerpts_in(prompt)
    assert entry["checked"] == [
        {"path": "a.py", "start_line": 2, "end_line": 3, "text": "2: two\n3: three"}
    ]
    # 位置は excerpts 側に移り、反証役の出力の側には残らない。
    assert prompt.count('"checked"') == 1


def test_excerpt_drops_parent_and_symlink_escapes_and_missing_paths(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    (root / "ok.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret\n", encoding="utf-8")
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")
    checked = [
        {"path": "../secret.txt", "start_line": 1, "end_line": 1},
        {"path": "link.txt", "start_line": 1, "end_line": 1},
        {"path": "missing.py", "start_line": 1, "end_line": 1},
        {"path": "ok.py", "start_line": 5, "end_line": 6},
        {"path": "ok.py", "start_line": 1, "end_line": 1},
    ]
    prompt = judge_prompt_for(root, checked)
    [entry] = excerpts_in(prompt)
    assert [e["path"] for e in entry["checked"]] == ["ok.py"]
    assert "secret" not in prompt


@pytest.mark.parametrize(
    "name", [".env", "config/.env.local", "keys/id_rsa", "tls/server.pem", ".ENV"]
)
def test_excerpt_drops_secret_file_names(tmp_path: Path, name: str) -> None:
    root = tmp_path / "work"
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("TOKEN=hunter2\n", encoding="utf-8")
    (root / "plain.txt").write_text("x\n", encoding="utf-8")
    (root / "alias.txt").symlink_to(root / name)
    checked = [
        {"path": name, "start_line": 1, "end_line": 1},
        {"path": "alias.txt", "start_line": 1, "end_line": 1},
        {"path": "plain.txt", "start_line": 1, "end_line": 1},
    ]
    prompt = judge_prompt_for(root, checked)
    [entry] = excerpts_in(prompt)
    assert [e["path"] for e in entry["checked"]] == ["plain.txt"]
    assert "hunter2" not in prompt


def test_excerpt_drops_reversed_ranges_and_unreadable_files(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("x\ny\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe\x00bad\n")
    (tmp_path / "dir.py").mkdir()
    # 型に合わない位置（行が 0 や文字列、絶対パス、dict でない要素）は入口の検証で
    # 分割の失敗になり、test_role_output_validation が縛る。ここには検証を通った上で
    # 捨てる位置だけを置く
    checked: list[Any] = [
        {"path": "ok.py", "start_line": 2, "end_line": 1},
        {"path": "dir.py", "start_line": 1, "end_line": 1},
        {"path": "binary.bin", "start_line": 1, "end_line": 1},
        {"path": "ok.py", "start_line": 2, "end_line": 9},
    ]
    [entry] = excerpts_in(judge_prompt_for(tmp_path, checked))
    assert entry["checked"] == [{"path": "ok.py", "start_line": 2, "end_line": 2, "text": "2: y"}]


def test_whole_float_end_line_is_read_as_an_integer(tmp_path: Path) -> None:
    """end_line が 1.0 の位置は、検証で 1 になり、抜粋が作られる。

    検証の前は、int でない位置として抜粋から捨てていた。
    """
    (tmp_path / "ok.py").write_text("x\ny\n", encoding="utf-8")
    checked = [{"path": "ok.py", "start_line": 1, "end_line": 1.0}]
    [entry] = excerpts_in(judge_prompt_for(tmp_path, checked))
    assert entry["checked"] == [{"path": "ok.py", "start_line": 1, "end_line": 1, "text": "1: x"}]


def test_excerpt_is_cut_at_limits(tmp_path: Path) -> None:
    (tmp_path / "big.py").write_text(
        "\n".join(f"line{n}" for n in range(1, 201)) + "\n", encoding="utf-8"
    )
    (tmp_path / "wide.py").write_text("w" * 20000 + "\n", encoding="utf-8")
    prompt = judge_prompt_for(tmp_path, [{"path": "big.py", "start_line": 1, "end_line": 200}])
    [entry] = excerpts_in(prompt)
    [excerpt] = entry["checked"]
    assert excerpt["end_line"] == adversarial.EXCERPT_MAX_LINES
    assert excerpt["text"].endswith(f"line{adversarial.EXCERPT_MAX_LINES}")

    many = [{"path": "big.py", "start_line": n, "end_line": n} for n in range(1, 8)]
    [entry] = excerpts_in(judge_prompt_for(tmp_path, many))
    assert len(entry["checked"]) == adversarial.EXCERPT_MAX_LOCATIONS

    wide = [{"path": "wide.py", "start_line": 1, "end_line": 1}]
    [entry] = excerpts_in(judge_prompt_for(tmp_path, wide))
    assert len(entry["checked"][0]["text"]) == adversarial.EXCERPT_MAX_CHARS


def test_rebuttal_schema_requires_checked() -> None:
    schema = adversarial.role_schemas()["rebuttal"]
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate({"items": [{"index": 0, "challenge": "c", "checked": [LOCATION]}]}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"items": [{"index": 0, "challenge": "c"}]}, schema)


OVERLOADED = "claude が is_error を返しました: API Error: 529 overloaded_error"
OVERLOADED_FAILURE = (OVERLOADED, claude.FailureKind.OVERLOADED)


TIMEOUT = claude.FailureKind.TIMEOUT
BUDGET = claude.FailureKind.BUDGET_EXCEEDED


def failing_first(fake: FakeInvoke, causes: list[tuple[str, claude.FailureKind]]) -> claude.Invoke:
    """最初の len(causes) 回の起動を、順に (理由, 種類) で失敗させ、あとは fake に任せる。"""
    calls: list[claude.Invocation] = []

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        calls.append(invocation)
        if len(calls) <= len(causes):
            reason, kind = causes[len(calls) - 1]
            raise claude.ClaudeInvocationError(reason, RECORD, kind)
        return fake(invocation)

    return invoke


def verify_with(
    candidates: list[dict[str, Any]], invoke: claude.Invoke
) -> adversarial.VerificationOutcome:
    return adversarial.verify(
        candidates,
        group=MACRO,
        changes="",
        working_directory=Path(),
        settings=settings_for(invoke),
    )


def test_transient_failure_reruns_the_split_once_and_passes() -> None:
    fake = FakeInvoke()
    outcome = verify_with([make_candidate("D1")], failing_first(fake, [OVERLOADED_FAILURE]))
    assert outcome.failed_splits == 0
    assert outcome.unverified_candidates == 0
    assert len(outcome.findings) == 1
    # 失敗した起動と走らせ直した起動の全てが記録に入る（金額と回数の集計の元）
    assert [(r.role, r.rerun) for r in outcome.invocations] == [
        ("rebuttal", RerunKind.INITIAL),
        ("rebuttal", RerunKind.SPLIT),
        ("judge", RerunKind.SPLIT),
    ]
    [failure] = outcome.failures
    assert failure.kind is claude.FailureKind.OVERLOADED
    assert failure.disposition is Disposition.RECOVERED
    assert failure.role == "rebuttal"
    assert "529" in failure.reason


def test_transient_failure_in_a_later_role_reruns_from_the_first_role() -> None:
    fake = FakeInvoke()
    calls = 0

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise claude.ClaudeInvocationError(OVERLOADED, RECORD, claude.FailureKind.OVERLOADED)
        return fake(invocation)

    outcome = verify_with([make_candidate("D1")], invoke)
    assert outcome.failed_splits == 0
    assert [(r.role, r.rerun) for r in outcome.invocations] == [
        ("rebuttal", RerunKind.INITIAL),
        ("judge", RerunKind.INITIAL),
        ("rebuttal", RerunKind.SPLIT),
        ("judge", RerunKind.SPLIT),
    ]


def test_second_transient_failure_stays_a_failure() -> None:
    fake = FakeInvoke()
    outcome = verify_with(
        [make_candidate("D1")], failing_first(fake, [OVERLOADED_FAILURE, OVERLOADED_FAILURE])
    )
    assert outcome.failed_splits == 1
    assert outcome.unverified_candidates == 1
    assert outcome.findings == ()
    assert [(f.attempt, f.disposition) for f in outcome.failures] == [
        (1, Disposition.RERUN_FAILED),
        (2, Disposition.NOT_RERUN),
    ]
    # 走らせ直しは起動して通らなかったので、見積もりで止めた走らせ直しには数えない
    assert Disposition.RERUN_STOPPED not in {f.disposition for f in outcome.failures}
    assert (rerun_count(outcome.failures), stopped_rerun_count(outcome.failures)) == (1, 0)


def test_permanent_failure_is_not_rerun() -> None:
    fake = FakeInvoke()
    outcome = verify_with(
        [make_candidate("D1")],
        failing_first(fake, [("claude が is_error を返しました: error_max_budget_usd", BUDGET)]),
    )
    assert outcome.failed_splits == 1
    assert len(outcome.invocations) == 1
    [failure] = outcome.failures
    assert failure.kind is BUDGET
    assert failure.disposition is Disposition.NOT_RERUN
    assert failure.as_record()["kind"] == "budget_exceeded"


def test_missing_index_reruns_only_the_missing_candidate_and_recovers() -> None:
    fake = FakeInvoke()
    candidates = [make_candidate("D1", "x"), make_candidate("D1", "y"), make_candidate("D1", "z")]
    outcome = verify_with(candidates, omitting_claim(fake, "y", times=1))
    assert [f.candidate["claim"] for f in outcome.findings] == ["x", "y", "z"]
    assert outcome.failed_splits == 0
    assert outcome.unverified_candidates == 0
    # 欠けた候補の走らせ直しは、分割の走らせ直しと別の種類で記録する
    assert [(r.role, r.rerun) for r in outcome.invocations] == [
        ("rebuttal", RerunKind.INITIAL),
        ("rebuttal", RerunKind.MISSING_CANDIDATES),
        ("judge", RerunKind.INITIAL),
    ]
    # 走らせ直しの起動は欠けた候補だけを受ける
    rerun_prompt = fake.calls[1].prompt
    assert [c["claim"] for c in embedded_json(rerun_prompt, "候補")] == ["y"]
    [failure] = outcome.failures
    assert failure.kind is claude.FailureKind.BAD_OUTPUT
    assert failure.role == "rebuttal"
    assert failure.disposition is Disposition.RECOVERED
    assert failure.scope is FailureScope.MISSING_CANDIDATES


def test_discarded_split_attempt_leaves_none_of_its_missing_candidate_records() -> None:
    fake = FakeInvoke()
    omit = omitting_claim(fake, "y", times=1)
    calls = 0

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        # 1 回目の試行: 反証役が y を欠き、欠けた候補の走らせ直しで揃うが、判定役が一時の失敗をする
        nonlocal calls
        calls += 1
        if calls == 3:
            raise claude.ClaudeInvocationError(OVERLOADED, RECORD, claude.FailureKind.OVERLOADED)
        return omit(invocation)

    outcome = verify_with([make_candidate("D1", "x"), make_candidate("D1", "y")], invoke)
    assert (outcome.failed_splits, outcome.unverified_candidates) == (0, 0)
    assert [f.candidate["claim"] for f in outcome.findings] == ["x", "y"]
    # 捨てた試行の欠けた候補の記録は残らず、分割の走らせ直しの記録だけが残る
    [failure] = outcome.failures
    assert failure.scope is FailureScope.LAUNCH
    assert failure.disposition is Disposition.RECOVERED
    # 起動は金額の集計の元なので、捨てた試行の分も残る
    assert [(r.role, r.rerun) for r in outcome.invocations] == [
        ("rebuttal", RerunKind.INITIAL),
        ("rebuttal", RerunKind.MISSING_CANDIDATES),
        ("judge", RerunKind.INITIAL),
        ("rebuttal", RerunKind.SPLIT),
        ("judge", RerunKind.SPLIT),
    ]


def test_missing_index_after_rerun_leaves_the_candidate_unverified() -> None:
    fake = FakeInvoke()
    candidates = [make_candidate("D1", "x"), make_candidate("D1", "y")]
    outcome = verify_with(candidates, omitting_claim(fake, "y"))
    # 判定役は返ってきた候補だけを受ける
    assert len(fake.calls) == 3
    assert [c["claim"] for c in embedded_json(fake.calls[2].prompt, "候補")] == ["x"]
    assert [f.candidate["claim"] for f in outcome.findings] == ["x"]
    assert outcome.findings[0].adjudication.verdict is Verdict.KEPT
    assert (outcome.failed_splits, outcome.unverified_candidates) == (1, 1)
    [failure] = outcome.failures
    assert failure.kind is claude.FailureKind.BAD_OUTPUT
    assert failure.disposition is Disposition.RERUN_FAILED
    assert failure.reason == "rebuttal の出力に index [1] がありません"
    assert failure.scope is FailureScope.MISSING_CANDIDATES
    assert "標準エラーを読む" not in failure.line


def test_no_later_role_runs_when_every_candidate_is_left_unverified() -> None:
    fake = FakeInvoke()
    outcome = verify_with([make_candidate("D1", "x")], omitting_claim(fake, "x"))
    # 反証役の起動とその走らせ直しだけがあり、判定役は起動しない。
    assert [(r.role, r.rerun) for r in outcome.invocations] == [
        ("rebuttal", RerunKind.INITIAL),
        ("rebuttal", RerunKind.MISSING_CANDIDATES),
    ]
    assert outcome.findings == ()
    assert (outcome.failed_splits, outcome.unverified_candidates) == (1, 1)


def test_unverified_candidates_are_stored_by_their_number_in_the_split() -> None:
    fake = FakeInvoke()
    candidates = [make_candidate("D1", "x"), make_candidate("D1", "y"), make_candidate("D1", "z")]
    outcome = verify_with(candidates, omitting_claim(fake, "y"))
    # 走らせ直しのプロンプトでは y の index は 0 になるが、記録は分割の中の番号 1 を持つ
    assert [c["claim"] for c in embedded_json(fake.calls[1].prompt, "候補")] == ["y"]
    [failure] = outcome.failures
    assert isinstance(failure, CandidatesUnverified)
    assert failure.candidates == (1,)
    assert outcome.unverified_candidates == 1


def test_rerun_is_per_split_and_leaves_other_splits_alone() -> None:
    fake = FakeInvoke()
    outcome = verify_with(
        [make_candidate("D1"), make_candidate("M0")], failing_first(fake, [OVERLOADED_FAILURE])
    )
    assert outcome.failed_splits == 0
    assert {f.candidate["viewpoint"] for f in outcome.findings} == {"D1", "M0"}
    assert [f.index for f in outcome.failures] == [0]


def test_role_failure_without_a_record_is_recorded_at_the_limit() -> None:
    fake = FakeInvoke()
    calls = 0

    def timing_out(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("claude", 1)

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            # 記録の無い失敗は claude.run が投げる地点で上限の額に代える
            return claude.run(invocation, runner=timing_out)
        return fake(invocation)

    outcome = verify_with([make_candidate("D1")], invoke)
    first = outcome.invocations[0]
    assert first.record.cost == LimitSubstitute(0.5)
