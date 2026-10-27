import hashlib
import importlib.util
import io
import re
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
from dev_launch import run_tool
from workflow_yaml import load_yaml

from jigane.git.process import CommandError

ROOT = Path(__file__).resolve().parent.parent

HEADER = """name: t
on:
  push:
permissions:
  contents: read
concurrency:
  group: t
jobs:
  j:
    name: j
    runs-on: ubuntu-24.04
    steps:
"""
CLEAN_CHECKOUT = """\
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
"""
CHECKOUT_KEEPING_CREDENTIALS = (
    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
)
TITLE_IN_RUN = """      - run: echo ${{ github.event.pull_request.title }}
"""


def load_checker() -> ModuleType:
    path = ROOT / "scripts" / "check_workflows.py"
    spec = importlib.util.spec_from_file_location("check_workflows", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_workflow(tmp_path: Path, body: str, header: str = HEADER) -> Path:
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "t.yml").write_text(header + body, encoding="utf-8")
    return directory


def zizmor_returncode(directory: Path) -> int:
    try:
        run_tool(["zizmor", "--offline", "--no-progress", "--pedantic", str(directory)])
    except CommandError as error:
        return error.returncode or 1
    return 0


def zizmor_rules(directory: Path) -> set[str]:
    """zizmor が指摘した規則の名前。指摘が無ければ空。"""
    # 色は環境変数（CI など）で自動に入る。入ると行頭に制御文字が付き、規則名を拾えない
    command = ["zizmor", "--offline", "--no-progress", "--pedantic", "--format", "plain"]
    try:
        run_tool([*command, "--color", "never", str(directory)])
    except CommandError as error:
        returncode, stdout = error.returncode, error.stdout
    else:
        return set()
    rules = set(re.findall(r"^\w+\[([\w-]+)\]", stdout, re.MULTILINE))
    # 終了コードが指摘を示すのに規則名が読めないのは、出力の形が変わった合図
    assert rules, f"zizmor exited {returncode} with unreadable output: {stdout!r}"
    return rules


def test_clean_workflow_passes(tmp_path: Path) -> None:
    directory = write_workflow(tmp_path, CLEAN_CHECKOUT)
    assert zizmor_returncode(directory) == 0


@pytest.mark.parametrize(
    "body",
    [CHECKOUT_KEEPING_CREDENTIALS, CLEAN_CHECKOUT + TITLE_IN_RUN],
    ids=["checkout-without-persist-credentials", "pull-request-title-in-run"],
)
def test_zizmor_fails_on_flawed_workflow(tmp_path: Path, body: str) -> None:
    directory = write_workflow(tmp_path, body)
    assert zizmor_returncode(directory) != 0


def test_every_workflow_has_a_live_on_at_the_top_level() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        assert not any(line.startswith("# on:") for line in text.splitlines()), workflow.name
        assert "on" in load_yaml(text), workflow.name


def test_repository_workflows_pass_zizmor() -> None:
    checker = load_checker()
    assert checker.audit(ROOT / ".github" / "workflows") == 0


def test_audit_runs_every_check_on_an_unchanged_copy(tmp_path: Path) -> None:
    checker = load_checker()
    source = tmp_path / "src"
    source.mkdir()
    body = "on:\n  push:\nname: t\n"
    (source / "t.yml").write_text(body, encoding="utf-8")
    seen: list[str] = []

    def record(copy: Path) -> int:
        seen.append((copy / "workflows" / "t.yml").read_text(encoding="utf-8"))
        return 0

    assert checker.audit(source, checks=(record, record)) == 0
    assert len(seen) == 2
    assert seen == [body, body]


def test_audit_runs_later_checks_after_a_failure_and_returns_the_first_code(
    tmp_path: Path,
) -> None:
    checker = load_checker()
    source = tmp_path / "src"
    source.mkdir()
    called: list[int] = []

    def failing(_copy: Path) -> int:
        called.append(3)
        return 3

    def passing(_copy: Path) -> int:
        called.append(0)
        return 0

    assert checker.audit(source, checks=(failing, passing)) == 3
    assert called == [3, 0]


def test_audit_default_checks_are_actionlint_then_zizmor() -> None:
    checker = load_checker()
    assert (checker.run_actionlint, checker.run_zizmor) == checker.DEFAULT_CHECKS


def test_actionlint_receives_the_workflow_files_of_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = load_checker()
    directory = write_workflow(tmp_path, CLEAN_CHECKOUT)
    calls: list[list[str]] = []
    monkeypatch.setattr(checker, "fetch_actionlint", lambda: Path("/pinned/actionlint"))
    monkeypatch.setattr(checker, "run_tool", calls.append)
    assert checker.run_actionlint(directory.parent) == 0
    assert calls == [["/pinned/actionlint", "-no-color", str(directory / "t.yml")]]


def test_actionlint_is_not_given_composite_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = load_checker()
    directory = write_workflow(tmp_path, CLEAN_CHECKOUT)
    write_composite_action(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(checker, "fetch_actionlint", lambda: Path("/pinned/actionlint"))
    monkeypatch.setattr(checker, "run_tool", calls.append)
    assert checker.run_actionlint(directory.parent) == 0
    assert calls == [["/pinned/actionlint", "-no-color", str(directory / "t.yml")]]


def test_actionlint_fails_on_a_flawed_expression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = load_checker()
    flawed = "      - run: echo ${{ nosuchcontext.x }}\n"
    directory = write_workflow(tmp_path, CLEAN_CHECKOUT + flawed)
    # 実体を取得できた上で、式の誤りを実物が見つけたことを確かめる
    executable = checker.fetch_actionlint()
    assert executable.is_file()
    assert checker.run_actionlint(directory.parent) == 1
    output = capsys.readouterr().out
    assert "nosuchcontext" in output


def test_actionlint_unavailable_is_not_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = load_checker()

    def unavailable() -> Path:
        message = "offline"
        raise checker.ActionlintUnavailableError(message)

    monkeypatch.setattr(checker, "fetch_actionlint", unavailable)
    assert checker.run_actionlint(tmp_path) == checker.ACTIONLINT_UNAVAILABLE_EXIT_CODE


def fake_archive(
    checker: ModuleType, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> list[str]:
    name = checker.actionlint_platform()
    executable = "actionlint.exe" if name.startswith("windows") else "actionlint"
    buffer = io.BytesIO()
    if name.endswith(".zip"):
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(executable, content)
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar_archive:
            info = tarfile.TarInfo(executable)
            info.size = len(content)
            tar_archive.addfile(info, io.BytesIO(content))
    data = buffer.getvalue()
    monkeypatch.setitem(checker.ACTIONLINT_SHA256, name, hashlib.sha256(data).hexdigest())
    downloads: list[str] = []

    def download(url: str) -> bytes:
        downloads.append(url)
        return data

    monkeypatch.setattr(checker, "download", download)
    return downloads


def test_fetch_actionlint_reuses_an_intact_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = load_checker()
    downloads = fake_archive(checker, monkeypatch, b"binary")
    first = checker.fetch_actionlint(tmp_path)
    assert first.read_bytes() == b"binary"
    assert checker.fetch_actionlint(tmp_path) == first
    assert len(downloads) == 1
    assert not list(first.parent.glob("*.partial"))


def test_fetch_actionlint_replaces_a_corrupted_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = load_checker()
    downloads = fake_archive(checker, monkeypatch, b"binary-content")
    target = checker.fetch_actionlint(tmp_path)
    target.write_bytes(b"binary-con")
    assert checker.fetch_actionlint(tmp_path) == target
    assert target.read_bytes() == b"binary-content"
    assert len(downloads) == 2


def test_fetch_actionlint_refetches_a_binary_without_a_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = load_checker()
    downloads = fake_archive(checker, monkeypatch, b"binary")
    target = checker.fetch_actionlint(tmp_path)
    checker.sidecar_of(target).unlink()
    checker.fetch_actionlint(tmp_path)
    assert len(downloads) == 2


def test_fetch_actionlint_rejects_an_archive_with_a_different_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = load_checker()
    fake_archive(checker, monkeypatch, b"binary")
    monkeypatch.setitem(checker.ACTIONLINT_SHA256, checker.actionlint_platform(), "0" * 64)
    with pytest.raises(checker.ActionlintUnavailableError):
        checker.fetch_actionlint(tmp_path)
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_actionlint_checksums_cover_every_supported_platform() -> None:
    checker = load_checker()
    for digest in checker.ACTIONLINT_SHA256.values():
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert set(checker.ACTIONLINT_SHA256) == {
        "darwin_amd64.tar.gz",
        "darwin_arm64.tar.gz",
        "linux_amd64.tar.gz",
        "linux_arm64.tar.gz",
        "windows_amd64.zip",
        "windows_arm64.zip",
    }


def test_zizmor_runs_at_the_pedantic_persona(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = load_checker()
    calls: list[list[str]] = []
    monkeypatch.setattr(checker, "run_tool", calls.append)
    assert checker.run_zizmor(tmp_path) == 0
    assert calls[0][:4] == ["zizmor", "--offline", "--no-progress", "--pedantic"]
    assert calls[0][-1] == str(tmp_path)


NO_PERMISSIONS = HEADER.replace("permissions:\n  contents: read\n", "")
RUN_OK = "      - run: echo ok\n"

# 自作の規則を外した分。zizmor が同じ違反を落とすことを、すり抜けやすい書き方で示す。
# （header, body, 落とす規則）
ZIZMOR_HANDLES = {
    "pull_request_target": (
        HEADER.replace("  push:\n", "  pull_request_target:\n"),
        RUN_OK,
        "dangerous-triggers",
    ),
    "workflow_run": (
        HEADER.replace("  push:\n", "  workflow_run:\n    workflows: [t]\n"),
        RUN_OK,
        "dangerous-triggers",
    ),
    "最上位の permissions 無し": (NO_PERMISSIONS, RUN_OK, "excessive-permissions"),
    "文字列の中の permissions だけ": (
        NO_PERMISSIONS,
        "      - run: echo 'permissions:'\n",
        "excessive-permissions",
    ),
    "write-all": (
        HEADER.replace("permissions:\n  contents: read\n", "permissions: write-all\n"),
        RUN_OK,
        "excessive-permissions",
    ),
    "版の固定が無い uses": (HEADER, "      - uses: actions/foo@v1\n", "unpinned-uses"),
    "引用した uses": (HEADER, "      - uses: 'actions/foo@v1'\n", "unpinned-uses"),
    "フロー形式の uses": (HEADER, "      - {uses: actions/foo@v1}\n", "unpinned-uses"),
    "添字の形の github.event": (
        HEADER,
        "      - run: echo ${{ github['event']['pull_request']['title'] }}\n",
        "template-injection",
    ),
    "toJSON(secrets)": (
        HEADER,
        "      - run: echo ${{ toJSON(secrets) }}\n",
        "overprovisioned-secrets",
    ),
}


@pytest.mark.parametrize("case", ZIZMOR_HANDLES)
def test_zizmor_catches_what_the_hand_written_rules_no_longer_cover(
    case: str, tmp_path: Path
) -> None:
    header, body, rule = ZIZMOR_HANDLES[case]
    directory = write_workflow(tmp_path, body, header)
    assert rule in zizmor_rules(directory)


UNPINNED_COMPOSITE_ACTION = """\
name: x
description: x
runs:
  using: composite
  steps:
    - uses: actions/checkout@v4
      with:
        persist-credentials: false
"""


def write_composite_action(tmp_path: Path, name: str = "action.yml") -> Path:
    directory = tmp_path / ".github" / "actions" / "x"
    directory.mkdir(parents=True)
    (directory / name).write_text(UNPINNED_COMPOSITE_ACTION, encoding="utf-8")
    return directory


@pytest.mark.parametrize("name", ["action.yml", "action.yaml"])
def test_audit_fails_on_an_unpinned_use_in_a_composite_action(tmp_path: Path, name: str) -> None:
    checker = load_checker()
    workflows = write_workflow(tmp_path, CLEAN_CHECKOUT)
    write_composite_action(tmp_path, name)
    assert checker.audit(workflows, checks=(checker.run_zizmor,)) != 0


def test_audit_passes_a_tree_without_composite_actions(tmp_path: Path) -> None:
    checker = load_checker()
    workflows = write_workflow(tmp_path, CLEAN_CHECKOUT)
    assert checker.composite_actions(workflows) == []
    assert checker.audit(workflows, checks=(checker.run_zizmor,)) == 0


def test_composite_actions_are_not_searched_outside_a_github_directory(tmp_path: Path) -> None:
    checker = load_checker()
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / "action.yml").write_text(UNPINNED_COMPOSITE_ACTION, encoding="utf-8")
    assert checker.composite_actions(source) == []


def test_composite_actions_skip_files_inside_the_workflows_directory(tmp_path: Path) -> None:
    checker = load_checker()
    workflows = write_workflow(tmp_path, CLEAN_CHECKOUT)
    (workflows / "action.yml").write_text(UNPINNED_COMPOSITE_ACTION, encoding="utf-8")
    composite = write_composite_action(tmp_path)
    assert checker.composite_actions(workflows) == [(composite / "action.yml").resolve()]
