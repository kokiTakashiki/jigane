import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fakes import MicroFake

from jigane.commands import build_parser, context, main
from jigane.commands.consent import GroupBreakdown, LaunchPlan, consent_text
from jigane.commands.exit_codes import EXIT_INCOMPLETE_COVERAGE, EXIT_OK
from jigane.commands.public_urls import terms_urls
from jigane.commands.subcommands import run as run_command
from jigane.commands.subcommands.run import (
    ShardedGroupResult,
    print_group_result,
    run_group,
    run_review,
)
from jigane.findings.records import HISTORY_FILE
from jigane.groups.definitions import GROUP_BY_ID, MACRO, MICRO, SECURITY, Effort, GroupId
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import create_feature_review, load_review, resolve_review
from jigane.skills.assembly import AssemblyInput
from jigane.skills.mcp import McpSelection, McpServer

SKIPPING_OPTIONS = ("--yes", "--force", "--no-confirm", "--assume-yes", "-y", "-f")


@pytest.fixture
def review_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    return str(create_feature_review(root, ["src"], name="consent").id)


def _ask(answers: list[str], prompts: list[str]) -> Callable[[str], str]:
    def read(prompt: str) -> str:
        prompts.append(prompt)
        return answers.pop(0)

    return read


@pytest.mark.parametrize("answer", ["n", "", "no", "yes please"])
def test_refusal_never_invokes(review_name: str, answer: str) -> None:
    fake = MicroFake({1: []})
    prompts: list[str] = []
    code = main(
        ["run", review_name, "micro"],
        invoke=fake,
        is_terminal=lambda: True,
        read_answer=_ask([answer], prompts),
    )
    assert code != EXIT_OK
    assert fake.calls == []
    assert len(prompts) == 1


def test_non_terminal_fails_without_asking(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = MicroFake({1: []})

    def forbidden(prompt: str) -> str:
        raise AssertionError(prompt)

    code = main(
        ["run", review_name, "micro"],
        invoke=fake,
        is_terminal=lambda: False,
        read_answer=forbidden,
    )
    assert code != EXIT_OK
    assert fake.calls == []
    assert "端末から実行してください" in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
def test_consent_invokes(
    review_name: str, answer: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = MicroFake({1: []})
    code = main(
        ["run", review_name, "micro"],
        invoke=fake,
        is_terminal=lambda: True,
        read_answer=_ask([answer], []),
    )
    assert code == EXIT_OK
    assert fake.calls != []
    err = capsys.readouterr().err
    assert "起動の回数" in err
    assert "USD" in err
    assert "検証役の回数に上限はありません" in err
    assert "少なくとも 1 回" in err
    assert "発見役: 1 回 × 1 回の目安 0.5 USD = 0.5 USD" in err
    assert "超えることがあります" in err


def test_run_has_no_option_to_skip_confirmation() -> None:
    help_text = build_parser().parse_args(["run", "x", "micro"])
    assert not set(SKIPPING_OPTIONS) & set(vars(help_text))
    for option in SKIPPING_OPTIONS:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "x", "micro", option])


def _failure_of_run(
    review_name: str,
    capsys: pytest.CaptureFixture[str],
    *,
    terminal: bool,
    answer: str,
) -> tuple[dict[str, str], str]:
    def read(_prompt: str) -> str:
        return answer

    code = main(
        ["run", review_name, "micro", "--json"],
        invoke=MicroFake({1: []}),
        is_terminal=lambda: terminal,
        read_answer=read,
    )
    assert code != EXIT_OK
    captured = capsys.readouterr()
    return json.loads(captured.out)["error"], captured.err


def test_declined_is_not_reported_as_invalid_input(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    error, err = _failure_of_run(review_name, capsys, terminal=True, answer="n")
    assert error["kind"] == "consent_declined"
    assert error["title"] == "実行しませんでした"
    assert error["action"] == "実行するなら、もう一度実行して確認に y と答えてください"
    assert "同意が得られなかった" in err
    assert "値を直して" not in err


def test_non_terminal_is_not_reported_as_invalid_input(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    error, err = _failure_of_run(review_name, capsys, terminal=False, answer="y")
    assert error["kind"] == "consent_unavailable"
    assert error["action"] == "端末から jigane run を実行してください"
    assert "端末から実行してください" in err


def test_run_review_runs_without_consent(review_name: str) -> None:
    fake = MicroFake({1: []})
    assert run_review(review_name, MICRO, invoke=fake) == EXIT_OK
    assert fake.calls != []


class _StoppedError(Exception):
    def __init__(self, settings: claude.RunSettings) -> None:
        super().__init__("stopped")
        self.settings = settings


@pytest.mark.parametrize("group", [MICRO, MACRO, SECURITY])
def test_run_review_passes_the_group_default_model(
    review_name: str, group: GroupId, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stop(
        _assembly: object,
        _group: object,
        settings: claude.RunSettings,
        *,
        mcp: object = None,  # noqa: ARG001 - 本物の run_group が受けるキーワード引数。呼ぶ側が渡しても通すため名前を変えない
    ) -> None:
        raise _StoppedError(settings)

    monkeypatch.setattr(run_command, "run_group", stop)
    with pytest.raises(_StoppedError) as stopped:
        run_command.run_review(review_name, group, invoke=MicroFake({1: []}))
    assert stopped.value.settings.budget.model == GROUP_BY_ID[group].model


def _micro_settings(invoke: MicroFake) -> claude.RunSettings:
    shape = run_command.group_defaults(MICRO)
    return claude.RunSettings(
        budget=claude.RunBudget("m", 1.0, shape.reserve_margin),
        effort=None,
        invoke=invoke,
        max_turns=shape.max_turns,
    )


def test_run_group_carries_the_history_warning_and_writes_nothing(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    review = load_review(resolve_review(review_name))
    (review.id.directory() / HISTORY_FILE).write_text("{not json", encoding="utf-8")
    assembly = AssemblyInput.load(review)
    source = assembly.source
    result = run_group(assembly, MICRO, _micro_settings(MicroFake({1: []})))
    assert isinstance(result, ShardedGroupResult)
    assert capsys.readouterr().out == ""
    assert [w["path"] for w in result.history.warnings] == [
        str(review.id.directory() / HISTORY_FILE)
    ]
    assert result.history.lines == (f"警告: {source.own.warnings[0].text()}",)
    assert result.exit_code == EXIT_OK
    assert print_group_result(result, as_json=False) == EXIT_OK
    out = capsys.readouterr().out
    assert "の指摘: 0 件" in out
    assert result.history.lines[0] in out


@pytest.mark.usefixtures("review_name")
def test_incomplete_coverage_exit_code_when_a_shard_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 一つのシャードに収まる大きさでは全滅の失敗になり、一部の失敗の分岐に届かない
    big = Path("big")
    big.mkdir()
    for name in ("a", "b"):
        (big / f"{name}.py").write_text("x = 1\n" * 3500, encoding="utf-8")
    review = create_feature_review(Path.cwd(), ["big"], name="big")
    result = run_group(
        AssemblyInput.load(review),
        MICRO,
        _micro_settings(MicroFake({1: [], 2: None})),
    )
    assert result.exit_code == EXIT_INCOMPLETE_COVERAGE
    assert print_group_result(result, as_json=False) == EXIT_INCOMPLETE_COVERAGE
    assert "失敗したシャード" in capsys.readouterr().err


def test_consent_text_does_not_promise_a_ceiling(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    for group in ("macro", "micro"):
        main(
            ["run", review_name, group],
            invoke=MicroFake({1: []}),
            is_terminal=lambda: True,
            read_answer=_ask(["n"], []),
        )
    err = capsys.readouterr().err
    assert "最大" not in err
    assert "1 回の目安 0.5 USD" in err
    assert "超えることがあります" in err
    assert "effort: high" in err
    assert "effort: 渡さない" in err


def test_consent_text_shows_the_resolved_effort(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        ["run", review_name, "micro", "--effort", "high"],
        invoke=MicroFake({1: []}),
        is_terminal=lambda: True,
        read_answer=_ask(["n"], []),
    )
    assert "effort: high" in capsys.readouterr().err


def test_effort_option_takes_the_enum_values_and_argparse_rejects_others(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()
    for value in Effort:
        assert (
            parser.parse_args(["run", review_name, "micro", "--effort", value.value]).effort
            is value
        )
    with pytest.raises(SystemExit):
        parser.parse_args(["run", review_name, "micro", "--effort", "ultra"])
    assert "invalid Effort value" in capsys.readouterr().err


def test_consent_text_shows_the_mcp_line() -> None:
    plan = LaunchPlan(
        SECURITY,
        (GroupBreakdown(SECURITY, 2, 0.5, has_verifier=True, past_cost_usd=None),),
        can_increase=True,
        effort=None,
    )
    assert "MCP は使いません" in consent_text(plan)
    selected = LaunchPlan(
        SECURITY,
        plan.groups,
        can_increase=True,
        effort=None,
        mcp=McpSelection((McpServer("owasp-mas", {}, None), McpServer("backendsec", {}, None))),
    )
    lines = consent_text(selected)
    assert "使う MCP: owasp-mas, backendsec" in lines
    assert "MCP は使いません" not in lines
    assert "これらのサーバの道具はすべて承認なしで使われます。" in lines
    assert "承認なし" not in " ".join(consent_text(plan))


def test_consent_text_links_the_input_list_before_asking_to_continue() -> None:
    plan = LaunchPlan(
        SECURITY,
        (GroupBreakdown(SECURITY, 2, 0.5, has_verifier=True, past_cost_usd=None),),
        can_increase=True,
        effort=None,
    )
    expected = (
        f"渡す情報の一覧: {terms_urls(None).security} の「レビューの入力と Claude Code」の節"
    )
    assert consent_text(plan, version_lookup=lambda: None)[-1] == expected
    # 規約の同意と同じ持ち主、リポジトリ、版の ref の下にある SECURITY.md を指す
    terms_base = terms_urls("1.2.3").terms_ja.rsplit("/", 1)[0]
    assert terms_base.endswith("/blob/v1.2.3")
    release = consent_text(plan, version_lookup=lambda: "1.2.3")[-1]
    assert (
        release
        == f"渡す情報の一覧: {terms_base}/SECURITY.md の「レビューの入力と Claude Code」の節"
    )


def _consent_err(review_name: str, group: str, capsys: pytest.CaptureFixture[str]) -> str:
    main(
        ["run", review_name, group],
        invoke=MicroFake({1: []}),
        is_terminal=lambda: True,
        read_answer=_ask(["n"], []),
    )
    return capsys.readouterr().err


def test_consent_text_breaks_the_estimate_down_by_role_and_group(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _consent_err(review_name, "micro", capsys)
    assert "この群の内訳（実装）:" in err
    assert "  発見役: 1 回 × 1 回の目安 0.5 USD = 0.5 USD" in err
    assert "検証役: 回数は見つかった指摘の数で決まり、事前には分かりません" in err
    assert "事前に分かる" not in err
    assert "同じレビューの群ごとの内訳:" in err
    for label in ("方針", "実装", "セキュリティ"):
        assert f"  {label}: 発見役" in err
    assert "発見役の回数 × 1 回の目安の和（検証役の分を含まない）:" in err
    assert err.count("実際の額は、検証役の回数と出力の量で変わり") == 1
    assert "前回の実績" not in err


def test_consent_text_shows_past_cost_and_avoids_cap_words_in_the_totals() -> None:
    plan = LaunchPlan(
        SECURITY,
        (
            GroupBreakdown(MACRO, 1, 0.5, has_verifier=False, past_cost_usd=0.1234),
            GroupBreakdown(SECURITY, 2, 0.5, has_verifier=True, past_cost_usd=None),
        ),
        can_increase=True,
        effort=None,
    )
    lines = consent_text(plan)
    assert (
        "  方針: 発見役 1 回 × 1 回の目安 0.5 USD = 0.5 USD、検証役なし、前回の実績 0.1234 USD"
    ) in lines
    assert (
        "  セキュリティ: 発見役 2 回 × 1 回の目安 0.5 USD = 1 USD、検証役は回数が未定"
    ) in lines
    assert "  発見役の回数 × 1 回の目安の和（検証役の分を含まない）: 1.5 USD" in lines
    for line in lines:
        if "合計" in line or "内訳" in line or "実際の額" in line:
            assert not any(word in line for word in ("上限", "目安", "以内")), line
    assert sum("前回の実績" in line for line in lines) == 1


@pytest.mark.usefixtures("real_read_answer")
def test_read_answer_returns_empty_on_eof(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert context.read_answer("続けるか（y/N） ") == ""
    assert capsys.readouterr().err == "続けるか（y/N） \n"


@pytest.mark.usefixtures("real_read_answer")
def test_eof_at_the_question_is_a_refusal(
    review_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = MicroFake({1: []})
    declined = main(
        ["run", review_name, "micro"],
        invoke=fake,
        is_terminal=lambda: True,
        read_answer=_ask(["n"], []),
    )
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = main(["run", review_name, "micro"], invoke=fake, is_terminal=lambda: True)
    assert code == declined
    assert code != EXIT_OK
    assert fake.calls == []
    assert "Traceback" not in capsys.readouterr().err
