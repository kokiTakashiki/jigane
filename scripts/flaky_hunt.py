"""試験の揺れを、乱順の種と並列の数を変えて同じ試験を繰り返し走らせて測る。

ゲートには入れない。揺れを疑ったときに手で走らせる。

  flaky_hunt.py                       全試験を既定の回数と並列の数で走らせる
  flaky_hunt.py --runs 20 --workers auto,4 tests/test_review_lock.py
                                      回数 20、並列の数 auto と 4、対象を絞る

落ちた回は出力の全文を一時の置き場へ保存し、最後に回ごとの種・並列の数・結果・落ちた試験の
表を出す。一度でも落ちたら終了コードは 1。
"""

import argparse
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from dev_launch import run_tool

from jigane.git.process import CommandError

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS = 10
DEFAULT_WORKERS = "auto,4,8"
SEED_LIMIT = 2**32
# -rfE が刷る短い要約の行。落ちた試験は「FAILED <node id>」、収集や準備の失敗は「ERROR <node id>」
FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) (?P<node>\S+)", re.MULTILINE)
HEADER = ("回", "種", "並列", "結果", "落ちた試験")

# 引数は pytest の起動の引数列。終了コードと出力の全文を返す
Runner = Callable[[list[str]], tuple[int, str]]


@dataclass(frozen=True)
class Plan:
    number: int
    seed: int
    workers: str


@dataclass(frozen=True)
class Outcome:
    plan: Plan
    returncode: int
    failed_tests: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def parse_workers(text: str) -> list[str]:
    """`auto,4,8` を並列の数の並びにする。空の要素は捨てる。"""
    workers = [item.strip() for item in text.split(",") if item.strip()]
    if not workers:
        raise ValueError("並列の数の並びが空である")
    for item in workers:
        if item != "auto" and not item.isdigit():
            raise ValueError(f"並列の数は auto か 0 以上の整数である: {item}")
    return workers


def build_plans(runs: int, workers: Sequence[str], seeds: Sequence[int]) -> list[Plan]:
    """回ごとに、種を 1 つずつ使い、並列の数を並びの順に巡らせる。"""
    if runs < 1:
        raise ValueError("回数は 1 以上である")
    if len(seeds) < runs:
        raise ValueError("種が回数に足りない")
    return [Plan(index + 1, seeds[index], workers[index % len(workers)]) for index in range(runs)]


def draw_seeds(runs: int) -> list[int]:
    # 同じ種を 2 回使うと、その回は何も新しく測れない
    seeds: list[int] = []
    while len(seeds) < runs:
        seed = secrets.randbelow(SEED_LIMIT)
        if seed not in seeds:
            seeds.append(seed)
    return seeds


def pytest_command(plan: Plan, targets: Sequence[str]) -> list[str]:
    # 既定の addopts は乱順を切っているので、ここで明示して入れる。-n も addopts より後ろが勝つ
    return [
        "uv",
        "run",
        "--locked",
        "pytest",
        "-p",
        "randomly",
        f"--randomly-seed={plan.seed}",
        "-n",
        plan.workers,
        "-rfE",
        *targets,
    ]


def parse_failed_tests(output: str) -> tuple[str, ...]:
    names = dict.fromkeys(match.group("node") for match in FAILED_LINE.finditer(output))
    return tuple(names)


def run_pytest(command: list[str]) -> tuple[int, str]:
    # 回ごとに別の一時の置き場を向ける。本物のデータの置き場は触らず、回の間で状態も持ち越さない
    with tempfile.TemporaryDirectory(prefix="jigane-home-") as home:
        return _run_pytest_in_home(command, home)


def _run_pytest_in_home(command: list[str], home: str) -> tuple[int, str]:
    try:
        output = run_tool(command, cwd=ROOT, env={**os.environ, "JIGANE_HOME": home})
    except CommandError as error:
        if error.returncode is None:
            # 起動できなかった（uv が無いなど）。落ちた試験の出力が無いので、そのまま上へ出す
            raise
        # 試験の失敗は pytest の終了コードが非 0 で返り、本文は標準出力に載る
        return error.returncode, error.stdout + error.stderr
    return 0, output


def hunt(
    plans: Sequence[Plan], targets: Sequence[str], runner: Runner, log_directory: Path
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for plan in plans:
        returncode, output = runner(pytest_command(plan, targets))
        failed_tests = parse_failed_tests(output) if returncode != 0 else ()
        if returncode != 0:
            log = log_directory / f"run-{plan.number:03d}-seed-{plan.seed}-n-{plan.workers}.log"
            log.write_text(output, encoding="utf-8")
        outcome = Outcome(plan, returncode, failed_tests)
        outcomes.append(outcome)
        print(f"[{plan.number}/{len(plans)}] {format_row(outcome)}", flush=True)
    return outcomes


def result_label(outcome: Outcome) -> str:
    return "通った" if outcome.passed else f"落ちた(終了コード {outcome.returncode})"


def format_row(outcome: Outcome) -> str:
    return f"seed={outcome.plan.seed} n={outcome.plan.workers} {result_label(outcome)}"


def build_table(outcomes: Sequence[Outcome]) -> str:
    rows = [
        (
            str(outcome.plan.number),
            str(outcome.plan.seed),
            outcome.plan.workers,
            result_label(outcome),
            ", ".join(outcome.failed_tests) or "-",
        )
        for outcome in outcomes
    ]
    lines = ["| " + " | ".join(HEADER) + " |", "|" + "|".join(["---"] * len(HEADER)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, runner: Runner = run_pytest) -> int:
    parser = argparse.ArgumentParser(description="試験の揺れを、種と並列の数を変えて測る")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="回数（既定: %(default)s）")
    parser.add_argument(
        "--workers", default=DEFAULT_WORKERS, help="並列の数の並び（既定: %(default)s）"
    )
    parser.add_argument("targets", nargs="*", help="対象。無ければ全試験")
    args = parser.parse_args(argv)
    try:
        plans = build_plans(args.runs, parse_workers(args.workers), draw_seeds(max(args.runs, 0)))
    except ValueError as error:
        parser.error(str(error))
    log_directory = Path(tempfile.mkdtemp(prefix="flaky-hunt-"))
    outcomes = hunt(plans, args.targets, runner, log_directory)
    print()
    print(build_table(outcomes))
    failures = [outcome for outcome in outcomes if not outcome.passed]
    print()
    if failures:
        print(f"{len(failures)}/{len(outcomes)} 回で落ちた。出力の全文: {log_directory}")
        return 1
    print(f"{len(outcomes)} 回すべて通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
