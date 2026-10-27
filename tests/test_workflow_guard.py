from pathlib import Path

import pytest
from export_public import find_self_hosted_workflows
from workflow_rules import (
    PUBLIC_COPY_KINDS,
    RUNNER_KINDS,
    Kind,
    Violation,
    exempt_violations,
    load_workflow,
    trigger_events,
    violations,
)


def kinds(found: list[Violation]) -> list[Kind]:
    return [violation.kind for violation in found]


SAFE = """\
name: x
permissions:
  contents: read
jobs:
  a:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - run: echo ok
"""

PIN = "0000000000000000000000000000000000000000"


def with_step(action: str, option: str) -> str:
    """SAFE の run の前に、with: で option を渡す action の step を挟む。"""
    step = f"      - uses: {action}@{PIN}\n        with:\n          {option}\n"
    return SAFE.replace("      - run: echo ok", step + "      - run: echo ok")


# 作り物と、期待する違反の文の一部の組。危険なトリガ、permissions、write-all は zizmor に任せ、
# tests/test_check_workflows.py が zizmor に落とされることを示す。
BAD_CASES = {
    "self-hosted": (
        SAFE.replace("ubuntu-24.04", "[self-hosted, linux]"),
        "self-hosted の文字列",
    ),
    "secrets": (
        SAFE.replace("echo ok", "echo ${{ secrets.TOKEN }}"),
        "許可されていない secrets: TOKEN",
    ),
    "secrets の添字参照": (
        SAFE.replace("echo ok", "echo ${{ secrets['X'] }}"),
        "許可されていない secrets: X",
    ),
    "GITHUB_TOKEN": (
        SAFE.replace("echo ok", "echo ${{ secrets.GITHUB_TOKEN }}"),
        "許可されていない secrets: GITHUB_TOKEN",
    ),
    "persist-credentials 無し": (
        SAFE.replace("        with:\n          persist-credentials: false\n", ""),
        "persist-credentials: false が無い",
    ),
    "script injection": (
        SAFE.replace(
            "- run: echo ok", "- run: |\n          echo ${{ github.event.pull_request.title }}"
        ),
        "run への github.event の直展開",
    ),
    "head_ref の直展開": (
        SAFE.replace("- run: echo ok", "- run: echo ${{ github.head_ref }}"),
        "run への github.head_ref の直展開",
    ),
    "actions/cache": (
        SAFE.replace(
            "      - run: echo ok",
            f"      - uses: actions/cache@{PIN}\n      - run: echo ok",
        ),
        "actions/cache の使用",
    ),
    "cache: を有効化": (
        with_step("actions/setup-python", "cache: pip"),
        "キャッシュの有効化: cache: pip",
    ),
    "enable-cache: を有効化": (
        with_step("astral-sh/setup-uv", "enable-cache: true"),
        "キャッシュの有効化: enable-cache: true",
    ),
}


def test_safe_workflow_passes() -> None:
    assert violations(SAFE) == []


def test_cache_option_set_to_false_passes() -> None:
    assert violations(with_step("astral-sh/setup-uv", "enable-cache: false")) == []


@pytest.mark.parametrize("case", BAD_CASES)
def test_each_forbidden_shape_is_detected(case: str, tmp_path: Path) -> None:
    text, expected = BAD_CASES[case]
    path = tmp_path / "bad.yml"
    path.write_text(text, encoding="utf-8")
    found = violations(path.read_text(encoding="utf-8"))
    assert any(expected in str(reason) for reason in found), found


WORKFLOW = ".github/workflows/x.yml"
LISTED = frozenset({WORKFLOW})
LISTED_ONLY = LISTED


def test_exemption_lifts_only_the_runner_bans() -> None:
    text, _ = BAD_CASES["self-hosted"]
    assert set(kinds(violations(text))) == RUNNER_KINDS
    assert [k for k in kinds(violations(text)) if k not in RUNNER_KINDS] == []
    found = exempt_violations(WORKFLOW, text, LISTED_ONLY)
    assert found == [Violation(Kind.NOT_PUSH_OR_SCHEDULE, "[]")]


@pytest.mark.parametrize("case", [name for name in BAD_CASES if name != "self-hosted"])
def test_exemption_keeps_every_other_ban(case: str) -> None:
    text, expected = BAD_CASES[case]
    found = exempt_violations(
        WORKFLOW, text.replace("ubuntu-24.04", "[self-hosted, Linux]"), LISTED_ONLY
    )
    assert any(expected in str(reason) for reason in found), found


PUSH_ONLY = SAFE.replace("jobs:", "on:\n  push:\n    branches: ['**']\njobs:").replace(
    "ubuntu-24.04", "[self-hosted, Linux]"
)


def test_listed_workflow_may_use_self_hosted_on_push_only() -> None:
    assert exempt_violations(WORKFLOW, PUSH_ONLY, LISTED) == []


def test_listed_workflow_may_use_self_hosted_on_push_and_schedule() -> None:
    on_block = "on:\n  push:\n    branches: ['**']\n  schedule:\n    - cron: '0 0 * * 1'\n"
    text = PUSH_ONLY.replace("on:\n  push:\n    branches: ['**']\n", on_block)
    assert exempt_violations(WORKFLOW, text, LISTED) == []


def test_self_hosted_outside_the_exclude_list_fails() -> None:
    assert set(kinds(exempt_violations(WORKFLOW, PUSH_ONLY, frozenset()))) == RUNNER_KINDS


@pytest.mark.parametrize(
    "on_block",
    [
        "on:\n  push:\n  pull_request:\n",
        "on: [push, pull_request]\n",
        "on:\n  push:\n  workflow_dispatch:\n",
        "on:\n  push:\n  schedule:\n    - cron: '0 0 * * *'\n  pull_request:\n",
        "on:\n  schedule:\n    - cron: '0 0 * * *'\n  pull_request:\n",
        "on:\n  schedule:\n    - cron: '0 0 * * *'\n  workflow_dispatch:\n",
        "on:\n  push:\n  schedule:\n    - cron: '0 0 * * *'\n  pull_request_target:\n",
        "on:\n  push:\n  schedule:\n    - cron: '0 0 * * *'\n  workflow_run:\n",
        "on: pull_request\n",
        "on:\n  workflow_dispatch:\n",
        "",
    ],
)
def test_listed_self_hosted_workflow_must_start_only_on_push_or_schedule(on_block: str) -> None:
    text = PUSH_ONLY.replace("on:\n  push:\n    branches: ['**']\n", on_block)
    found = exempt_violations(WORKFLOW, text, LISTED)
    assert Kind.NOT_PUSH_OR_SCHEDULE in kinds(found), found


def test_listed_workflow_without_self_hosted_may_start_on_pull_request() -> None:
    text = SAFE.replace("jobs:", "on:\n  pull_request:\njobs:")
    assert exempt_violations(WORKFLOW, text, LISTED) == []


def test_listed_workflow_keeps_every_other_ban() -> None:
    text = PUSH_ONLY.replace("echo ok", "echo ${{ secrets.TOKEN }}")
    assert exempt_violations(WORKFLOW, text, LISTED) == [
        Violation(Kind.DISALLOWED_REFERENCE, "TOKEN")
    ]


HOSTED_PASSING = ["ubuntu-24.04", "ubuntu-latest", "macos-15", "windows-latest", "'ubuntu-22.04'"]


@pytest.mark.parametrize("label", HOSTED_PASSING)
def test_github_hosted_runner_names_pass(label: str) -> None:
    assert violations(SAFE.replace("ubuntu-24.04", label)) == []


RUNNER_BAD_CASES = {
    "matrix の式": SAFE.replace("runs-on: ubuntu-24.04", "runs-on: ${{ matrix.os }}").replace(
        "    steps:", "    strategy:\n      matrix:\n        os: [self-hosted]\n    steps:"
    ),
    "matrix の式だけ": SAFE.replace("ubuntu-24.04", "${{ matrix.os }}"),
    "vars の参照": SAFE.replace("ubuntu-24.04", "${{ vars.RUNNER }}"),
    "大文字の Self-Hosted": SAFE.replace("ubuntu-24.04", "[Self-Hosted, Linux]"),
    "group の指定": SAFE.replace("runs-on: ubuntu-24.04", "runs-on:\n      group: my-group"),
    "未知のラベル": SAFE.replace("ubuntu-24.04", "ubuntu-24.04-arm"),
    "配列の他のラベル": SAFE.replace("ubuntu-24.04", "[ubuntu-24.04, gpu]"),
    "ブロックの配列の他のラベル": SAFE.replace(
        "runs-on: ubuntu-24.04", "runs-on:\n      - ubuntu-24.04\n      - gpu"
    ),
    "コメントの中の self-hosted": SAFE.replace("jobs:", "# runs-on: self-hosted\njobs:"),
}


@pytest.mark.parametrize("case", RUNNER_BAD_CASES)
def test_runner_outside_the_allowlist_fails(case: str) -> None:
    assert set(kinds(violations(RUNNER_BAD_CASES[case]))) & RUNNER_KINDS, case


def test_runner_check_gives_the_same_verdict_to_the_gate_and_the_exporter(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    texts = [
        *RUNNER_BAD_CASES.values(),
        *(SAFE.replace("ubuntu-24.04", n) for n in HOSTED_PASSING),
    ]
    for number, text in enumerate(texts):
        (workflows / f"w{number}.yml").write_text(text, encoding="utf-8")

    flagged = {line.split(":")[0] for line in find_self_hosted_workflows(tmp_path)}

    expected = {
        f".github/workflows/w{number}.yml"
        for number, text in enumerate(texts)
        if set(kinds(violations(text))) & RUNNER_KINDS
    }
    assert flagged == expected
    assert len(expected) == len(RUNNER_BAD_CASES)


@pytest.mark.parametrize(
    "on_block",
    ["on:\n", "on: 3\n", "on: [push, {x: 1}]\n", "on: [push\n"],
    ids=["空", "数", "列の中の対応表", "閉じていない列"],
)
def test_listed_workflow_with_unreadable_on_fails(on_block: str) -> None:
    text = PUSH_ONLY.replace("on:\n  push:\n    branches: ['**']\n", on_block)
    assert Kind.ON_UNREADABLE in kinds(exempt_violations(WORKFLOW, text, LISTED))


@pytest.mark.parametrize(
    ("on_block", "events"),
    [
        ("on: push\n", {"push"}),
        ("on: [push, pull_request]\n", {"push", "pull_request"}),
        ("on:\n  - push\n  - workflow_dispatch\n", {"push", "workflow_dispatch"}),
        (
            "on:\n  push:\n    branches: ['**']\n  schedule:\n    - cron: '0 0 * * *'\n",
            {"push", "schedule"},
        ),
        ("", set()),
        ("on: {push: {}}\n", {"push"}),
        ("on:\n  'push':\n    branches: ['**']\n", {"push"}),
        ('on:\n  "push":\n', {"push"}),
        ('"on": [push,\n  schedule]\n', {"push", "schedule"}),
        ("x: &events [push, schedule]\non: *events\n", {"push", "schedule"}),
        ("on:\n  push:\n    branches: [\n      main,\n    ]\n", {"push"}),
    ],
)
def test_readable_on_forms_are_read(on_block: str, events: set[str]) -> None:
    assert trigger_events(load_workflow(on_block + "jobs:\n")) == events


CHECKOUT_STEP = (
    "- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    "        with:\n          persist-credentials: false"
)

# 行ごとの読み方ですり抜けた書き方。判定は構文木の上で行うので、どれも落ちる。
ESCAPES = {
    "引用符の中の # のあとの github.event": (
        SAFE.replace(
            "- run: echo ok",
            "- run: |\n          echo 'a #b'; echo ${{ github.event.pull_request.title }}",
        ),
        Kind.EVENT_EXPANSION,
    ),
    "添字の github.event": (
        SAFE.replace("- run: echo ok", "- run: echo ${{ github['event']['title'] }}"),
        Kind.EVENT_EXPANSION,
    ),
    "引用符の中の # のあとの head_ref": (
        SAFE.replace(
            "- run: echo ok", "- run: |\n          echo 'a #b'; echo ${{ github.head_ref }}"
        ),
        Kind.HEAD_REF_EXPANSION,
    ),
    "添字の head_ref": (
        SAFE.replace("- run: echo ok", "- run: echo ${{ github['head_ref'] }}"),
        Kind.HEAD_REF_EXPANSION,
    ),
    "toJSON(secrets)": (
        SAFE.replace("- run: echo ok", "- run: echo ${{ toJSON(secrets) }}"),
        Kind.DISALLOWED_REFERENCE,
    ),
    "動的な添字の secrets": (
        SAFE.replace("- run: echo ok", "- run: echo ${{ secrets[matrix.name] }}"),
        Kind.DISALLOWED_REFERENCE,
    ),
    "run 以外の値の secrets": (
        SAFE.replace(
            "- run: echo ok", "- run: echo ok\n        env:\n          T: ${{ secrets.T }}"
        ),
        Kind.DISALLOWED_REFERENCE,
    ),
    "引用した cache": (
        SAFE.replace("- run: echo ok", f"- uses: 'actions/cache@{PIN}'"),
        Kind.CACHE_ACTION,
    ),
    "フロー形式の cache": (
        SAFE.replace("- run: echo ok", f"- {{uses: actions/cache@{PIN}}}"),
        Kind.CACHE_ACTION,
    ),
    "引用した checkout": (
        SAFE.replace(CHECKOUT_STEP, f"- uses: 'actions/checkout@{PIN}'"),
        Kind.CHECKOUT_CREDENTIALS,
    ),
    "フロー形式の checkout": (
        SAFE.replace(CHECKOUT_STEP, f"- {{uses: actions/checkout@{PIN}}}"),
        Kind.CHECKOUT_CREDENTIALS,
    ),
    "persist-credentials: true": (
        SAFE.replace("persist-credentials: false", "persist-credentials: true"),
        Kind.CHECKOUT_CREDENTIALS,
    ),
    "フロー形式の with の cache": (
        SAFE.replace(
            "- run: echo ok", f"- uses: actions/setup-python@{PIN}\n        with: {{cache: pip}}"
        ),
        Kind.CACHE_OPTION,
    ),
    "複数行のフロー列の runs-on": (
        SAFE.replace("runs-on: ubuntu-24.04", "runs-on: [ubuntu-24.04,\n      self-hosted]"),
        Kind.RUNNER_NOT_HOSTED,
    ),
    "アンカーで共有した runs-on": (
        SAFE.replace("jobs:", "x: &r [self-hosted]\njobs:").replace(
            "runs-on: ubuntu-24.04", "runs-on: *r"
        ),
        Kind.RUNNER_NOT_HOSTED,
    ),
    "YAML として壊れている": (SAFE + "  bad: [\n", Kind.UNREADABLE),
}


@pytest.mark.parametrize("case", ESCAPES)
def test_escapes_of_the_old_line_based_reading_are_detected(case: str, tmp_path: Path) -> None:
    text, kind = ESCAPES[case]
    path = tmp_path / "bad.yml"
    path.write_text(text, encoding="utf-8")
    assert kind in kinds(violations(path.read_text(encoding="utf-8")))


# 文字列の中身が規則の形に似ているだけで、YAML の構造としては違反でないもの
LOOKALIKES = {
    "run の文字列の中の cache: true": SAFE.replace(
        "- run: echo ok",
        "- run: |\n          echo 'cache: true'\n          echo 'enable-cache: true'",
    ),
    "run の文字列の中の uses": SAFE.replace(
        "- run: echo ok", "- run: |\n          echo 'uses: actions/cache@v4'"
    ),
    "式の外の github.event": SAFE.replace(
        "- run: echo ok", "- run: echo github.event.pull_request"
    ),
    "フロー形式の persist-credentials": SAFE.replace(
        "        with:\n          persist-credentials: false",
        "        with: {persist-credentials: false}",
    ),
}


@pytest.mark.parametrize("case", LOOKALIKES)
def test_lookalike_strings_are_not_violations(case: str) -> None:
    assert violations(LOOKALIKES[case]) == []


def test_runner_violation_reports_the_line_of_the_runs_on_value() -> None:
    found = violations(SAFE.replace("ubuntu-24.04", "ubuntu-24.04-arm"))
    assert [v.line for v in found if v.kind is Kind.RUNNER_NOT_HOSTED] == [
        SAFE.splitlines().index("    runs-on: ubuntu-24.04") + 1
    ]


def test_unreadable_workflow_is_blocked_from_the_public_copy() -> None:
    assert Kind.UNREADABLE in PUBLIC_COPY_KINDS
