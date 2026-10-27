"""release.yml の verify にある「CI が main で success」の段を、偽の gh で bash に走らせる試験。

bash を実際に走らせるので POSIX だけの置き場に置く。
"""

import os
from pathlib import Path

import pytest
from dev_launch import run_tool
from workflow_yaml import job_map, load_yaml

from jigane.git.process import CommandError

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
STEP_NAME = "タグが指すコミットの CI が main で success であることを確かめる"


def step_run_in(text: str, step_name: str) -> str:
    """構文木の jobs.*.steps から name で段を引き、その run の値を返す。段は 1 つだけ。"""
    found = [
        step
        for job in job_map(load_yaml(text)).values()
        for step in job.get("steps", [])
        if step.get("name") == step_name
    ]
    assert len(found) == 1
    assert "run" in found[0], f"段 {step_name!r} に run が無い"
    return str(found[0]["run"])


def step_run_body(step_name: str) -> str:
    return step_run_in(RELEASE.read_text(encoding="utf-8"), step_name)


def run_step(tmp_path: Path, gh_output: str, gh_exit: int = 0) -> tuple[int, str]:
    """gh が gh_output を出して gh_exit で終わる状況で段を走らせ、終了コードと標準出力を返す。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        '#!/bin/sh\nprintf "%s" "$FAKE_GH_OUTPUT"\nexit "${FAKE_GH_EXIT:-0}"\n',
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_GH_OUTPUT": gh_output,
        "FAKE_GH_EXIT": str(gh_exit),
        "REPOSITORY": "example/repo",
        "TAG": "v0.0.0",
        "TAG_SHA": "0" * 40,
    }
    try:
        stdout = run_tool(["bash", "-c", step_run_body(STEP_NAME)], env=env)
    except CommandError as error:
        # run_tool は非 0 の終了のときだけ CommandError を出し、終了コードを必ず付ける。
        return error.returncode or -1, error.stdout
    return 0, stdout


@pytest.mark.parametrize("total", ["null", "", "0", "abc"])
def test_ci_step_fails_when_total_count_is_not_a_positive_integer(
    tmp_path: Path, total: str
) -> None:
    returncode, stdout = run_step(tmp_path, total)
    assert returncode != 0
    assert "::error::" in stdout


def test_ci_step_passes_when_a_successful_run_exists(tmp_path: Path) -> None:
    returncode, _ = run_step(tmp_path, "1")
    assert returncode == 0


def test_ci_step_fails_when_gh_fails(tmp_path: Path) -> None:
    # 出力は有効な値 "1" のまま、gh だけが非 0 で終わる。段が set -e で落ちることを確かめる。
    returncode, _ = run_step(tmp_path, "1", gh_exit=1)
    assert returncode != 0


def workflow_with(*steps: str) -> str:
    return "jobs:\n  v:\n    steps:\n" + "".join(steps)


def test_step_run_ignores_the_name_in_a_comment_and_in_another_run() -> None:
    text = workflow_with(
        f"      # - name: {STEP_NAME}\n",
        f"      - name: other\n        run: |\n          echo '- name: {STEP_NAME}'\n",
        f"      - name: {STEP_NAME}\n        run: |\n          echo real\n",
    )
    assert step_run_in(text, STEP_NAME) == "echo real\n"


def test_step_run_reads_a_quoted_name_and_a_folded_run() -> None:
    text = workflow_with(
        f'      - name: "{STEP_NAME}"\n        run: >-\n          echo a\n          b\n'
    )
    assert step_run_in(text, STEP_NAME) == "echo a b"


def test_step_run_reads_the_flow_form_step() -> None:
    text = workflow_with(f'      - {{name: "{STEP_NAME}", run: "echo flow"}}\n')
    assert step_run_in(text, STEP_NAME) == "echo flow"


def test_step_run_skips_a_job_without_steps() -> None:
    text = (
        "jobs:\n  reused:\n    uses: x/y/.github/workflows/z.yml@" + "a" * 40 + "\n"
        f"  v:\n    steps:\n      - name: {STEP_NAME}\n        run: echo job\n"
    )
    assert step_run_in(text, STEP_NAME) == "echo job"


def test_step_without_run_fails_with_a_message() -> None:
    text = workflow_with(f"      - name: {STEP_NAME}\n        uses: x/y@{'a' * 40}\n")
    with pytest.raises(AssertionError, match="run が無い"):
        step_run_in(text, STEP_NAME)
