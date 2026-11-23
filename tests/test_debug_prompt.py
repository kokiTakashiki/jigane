import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFake, MicroFake

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_USAGE
from jigane.commands.subcommands import run as run_subcommand
from jigane.groups.definitions import GroupId
from jigane.llm_runs import codex
from jigane.reviews.catalog import load_review
from jigane.reviews.lifecycle import create_feature_review
from jigane.reviews.resolution import resolve_review
from jigane.skills import adversarial, macro_compact
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimator import estimate_of
from jigane.skills.launch_prompts import LaunchInput, LaunchRole

JSON_KEYS = ["prompts", "next"]
ENTRY_KEYS = ["role", "text", "bytes"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    for name in ("a", "b", "c"):
        (root / "src" / f"{name}.py").write_text(f"def build_{name}():\n    return 1\n", "utf-8")
    monkeypatch.chdir(root)
    create_feature_review(root, ["src"], "p")
    return root


def run_json(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    capsys.readouterr()
    assert main(["debug", "prompt", *args, "--json"]) == 0
    body: dict[str, Any] = json.loads(capsys.readouterr().out)
    return body


def estimate_calls(group: GroupId) -> int:
    assembly = AssemblyInput.load(load_review(resolve_review("shop:p")))
    return estimate_of(LaunchInput(assembly, group)).discovery_calls


@pytest.mark.parametrize("group", ["macro", "micro"])
@pytest.mark.usefixtures("project", "all_principle_shapes")
def test_keys_types_bytes_and_estimate_count(
    capsys: pytest.CaptureFixture[str], group: str
) -> None:
    body = run_json(capsys, "shop:p", group, "--page-size", "20")
    assert list(body) == JSON_KEYS
    assert body["next"] is None
    for entry in body["prompts"]:
        assert list(entry) == ENTRY_KEYS
        assert isinstance(entry["role"], str)
        assert entry["text"]
        assert entry["bytes"] == len(entry["text"].encode("utf-8"))
    assert len(body["prompts"]) == estimate_calls(GroupId(group))


@pytest.mark.usefixtures("project", "all_principle_shapes")
def test_macro_has_discovery_then_principles(capsys: pytest.CaptureFixture[str]) -> None:
    body = run_json(capsys, "shop:p", "macro", "--page-size", "20")
    roles = [entry["role"] for entry in body["prompts"]]
    assert roles[0] == LaunchRole.DISCOVERY.value == "discovery"
    assert set(roles[1:]) == {LaunchRole.PRINCIPLE.value}
    assert len(roles) > 1


@pytest.fixture
def value_set_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "shop"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "defs.py").write_text(
        "NAMES = ('alpha', 'beta', 'gamma')\ndef known(value):\n    return value in NAMES\n",
        encoding="utf-8",
    )
    (src / "use.py").write_text(
        "def check(mode):\n"
        "    a = mode == 'alpha'\n"
        "    b = mode in ['beta', 'gamma']\n"
        "    c = mode.startswith('alp')\n"
        "    d = mode.endswith('ma')\n"
        "    return a or b or c or d\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    create_feature_review(root, ["src"], "p")
    return "shop:p"


@pytest.fixture
def codex_macro(monkeypatch: pytest.MonkeyPatch) -> MacroFake:
    fake = MacroFake([])

    monkeypatch.setattr(codex, "run", fake)
    monkeypatch.setattr(run_subcommand, "codex_supported", lambda: True)
    assert main(["select-agent", "codex"]) == EXIT_OK
    return fake


def test_codex_p1_sends_the_guide_and_the_definition_with_direct_comparison_locations(
    value_set_review: str, codex_macro: MacroFake, capsys: pytest.CaptureFixture[str]
) -> None:
    guide = adversarial.template_text(macro_compact.P1_GUIDE)
    prompts = run_json(capsys, value_set_review, "macro")["prompts"]
    assert (
        main(
            ["run", value_set_review, "macro"], is_terminal=lambda: True, read_answer=lambda _: "y"
        )
        == EXIT_OK
    )
    assert sorted(call.prompt for call in codex_macro.calls) == sorted(
        entry["text"] for entry in prompts
    )
    [p1] = [entry["text"] for entry in prompts if guide in entry["text"]]
    assert p1.startswith("方針の静的レビュー。- P1 ")
    assert "| 定義 | 文字列での比較 | 値 |" in p1
    [row] = [line for line in p1.splitlines() if line.startswith("| src/defs.py:1 NAMES |")]
    assert "4 (src/use.py:2 等値 'alpha'" in row
    assert "src/use.py:3 所属 'beta', 'gamma'" in row
    assert "src/use.py:4 接頭辞 'alp' ほか 1 件)" in row
    assert "src/defs.py:3 所属" not in row

    assert main(["select-agent", "claude"]) == EXIT_OK
    claude_prompts = run_json(capsys, value_set_review, "macro")["prompts"]
    assert all(guide not in entry["text"] for entry in claude_prompts)
    [claude_p1] = [
        entry["text"] for entry in claude_prompts if "- P1 単一の情報源" in entry["text"]
    ]
    assert "src/defs.py:3 所属" in claude_p1


def test_codex_run_uses_the_full_p1_prompt_from_debug_prompt(
    value_set_review: str,
    codex_macro: MacroFake,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_text = adversarial.template_text
    monkeypatch.setattr(
        adversarial,
        "template_text",
        lambda name: (
            template_text(name) + "a" * 33_000
            if name == macro_compact.P1_GUIDE
            else template_text(name)
        ),
    )
    prompts = run_json(capsys, value_set_review, "macro")["prompts"]
    [p1] = [entry for entry in prompts if "## P1の資料の読み方" in entry["text"]]
    assert p1["bytes"] > 32_768
    assert (
        main(
            ["run", value_set_review, "macro"], is_terminal=lambda: True, read_answer=lambda _: "y"
        )
        == EXIT_OK
    )
    assert [call.prompt for call in codex_macro.calls if call.principle == "P1"] == [p1["text"]]


@pytest.mark.usefixtures("project", "all_principle_shapes")
def test_pages_walk_every_prompt_once(capsys: pytest.CaptureFixture[str]) -> None:
    whole = run_json(capsys, "shop:p", "macro", "--page-size", "20")["prompts"]
    walked: list[dict[str, Any]] = []
    after: list[str] = []
    while True:
        body = run_json(capsys, "shop:p", "macro", "--page-size", "2", *after)
        assert len(body["prompts"]) <= 2
        walked += body["prompts"]
        if body["next"] is None:
            break
        after = ["--after", body["next"]]
    assert walked == whole
    assert len(whole) > 2


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        (["--page-size=0"], "--page-size"),
        (["--page-size=21"], "--page-size"),
        (["--page-size=-1"], "--page-size"),
        (["--page-size", ""], "--page-size"),
        (["--page-size", "x"], "--page-size"),
        (["--after", "x"], "--after"),
        (["--after", ""], "--after"),
        (["--after=-1"], "--after"),
        (["--page-size=+5"], "--page-size"),
        (["--page-size= 5"], "--page-size"),
        (["--page-size=5_0"], "--page-size"),
        (["--page-size=６４"], "--page-size"),
        (["--after=+5"], "--after"),
        (["--after= 5"], "--after"),
        (["--after=5_0"], "--after"),
        (["--after=６４"], "--after"),
    ],
)
@pytest.mark.usefixtures("project")
def test_bad_paging_exits_64_with_reason(
    capsys: pytest.CaptureFixture[str], extra: list[str], reason: str
) -> None:
    capsys.readouterr()
    with pytest.raises(SystemExit) as raised:
        main(["debug", "prompt", "shop:p", "micro", *extra])
    assert raised.value.code == EXIT_USAGE == 64
    assert reason in capsys.readouterr().err


@pytest.mark.usefixtures("project")
def test_bad_paging_is_refused_before_the_review_is_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    with pytest.raises(SystemExit) as raised:
        main(["debug", "prompt", "shop:nothing", "micro", "--page-size=0"])
    assert raised.value.code == EXIT_USAGE


@pytest.mark.usefixtures("project")
def test_after_at_count_is_empty_page_and_beyond_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    total = len(run_json(capsys, "shop:p", "micro", "--page-size", "20")["prompts"])
    last = run_json(capsys, "shop:p", "micro", "--after", str(total))
    assert last == {"prompts": [], "next": None}
    capsys.readouterr()
    assert main(["debug", "prompt", "shop:p", "micro", "--after", str(total + 1)]) == EXIT_USAGE
    assert "--after" in capsys.readouterr().err


@pytest.mark.usefixtures("project", "all_principle_shapes")
def test_no_llm_is_launched(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[str] = []
    real_run = subprocess.run  # noqa: TID251 - 起動を数えるために包む

    def counting(args: Any, *rest: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - subprocess.run と同じ形で受ける
        if "claude" in Path(str(args[0])).name:
            launched.append(str(args[0]))
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    for group in ("macro", "micro"):
        run_json(capsys, "shop:p", group)
    assert launched == []


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "abc"])
@pytest.mark.usefixtures("project")
def test_invalid_max_budget_exits_64_without_launch(
    capsys: pytest.CaptureFixture[str], value: str
) -> None:
    fake = MicroFake({1: []})
    capsys.readouterr()
    with pytest.raises(SystemExit) as raised:
        main(
            ["debug", "prompt", "shop:p", "micro", f"--max-budget-usd-per-call={value}"],
            invoke=fake,
        )
    assert raised.value.code == EXIT_USAGE == 64
    assert "--max-budget-usd-per-call" in capsys.readouterr().err
    assert fake.calls == []
