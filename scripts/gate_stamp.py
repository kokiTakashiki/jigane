"""緑のゲートを通ったコミットに印を付け、push の前に印と照らす。

  gate_stamp.py write [--gate check|docs]
                               作業木が綺麗なら、HEAD の SHA と通ったゲートの種類を印に書く
                               （ゲートの最後が呼ぶ。既定は check）。check の印は docs の印も兼ねる
  gate_stamp.py check [--remote SHA] SHA...
                               送る各 ref の先端の SHA を、コミットまで剥がして印と照らす。
                               リモートの先端との差分が文書だけなら docs 以上の印で、
                               ほかを含むなら check の印で通す。足りなければ案内を出して 1
  gate_stamp.py install-hook   core.hooksPath を .githooks に向ける（setup が呼ぶ）

印の置き場は `git rev-parse --git-path jigane-gate-passed` が返す所で、worktree ごとに別になる。
呼び出した場所の git リポジトリを見る。
"""

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from jigane.git.process import CommandError, run_command

STAMP_NAME = "jigane-gate-passed"
HOOKS_PATH = ".githooks"
# git が push の hook に渡す、リモートに先端が無い ref（新しい枝）の sha
ZERO_SHA = "0" * 40


class Gate(StrEnum):
    CHECK = "check"
    DOCS = "docs"

    @property
    def strength(self) -> int:
        # 重いゲートは軽いゲートを含むので、check を docs より強くする
        return 1 if self is Gate.CHECK else 0

    def covers(self, needed: "Gate") -> bool:
        return self.strength >= needed.strength


GATE_COMMANDS = {
    Gate.CHECK: "make check（Windows は just check）",
    Gate.DOCS: "make docs-check（Windows は just docs-check）",
}


@dataclass(frozen=True)
class Stamp:
    commit: str
    gate: Gate


@dataclass(frozen=True)
class PushedRef:
    """push の ref ごとの対。remote_tip が ZERO_SHA なら、リモートに先端が無い（新しい枝）。"""

    commit: str
    remote_tip: str = ZERO_SHA

    @property
    def has_remote_tip(self) -> bool:
        return bool(self.remote_tip.strip("0"))


def is_document(path: str) -> bool:
    # 文書のゲートで足りる変更の決まり。docs/ の下と、根の *.md
    if not path.endswith(".md"):
        return False
    return path.startswith("docs/") or "/" not in path


def in_work_tree() -> bool:
    try:
        return run_command(["git", "rev-parse", "--is-inside-work-tree"]).strip() == "true"
    except CommandError:
        # git の作業木の外（公開の写しの木など）では、印も hook も要らない
        return False


def stamp_path() -> Path:
    # --git-path は呼び出した場所からの相対で返すことがあるので、絶対にして返す
    return Path(run_command(["git", "rev-parse", "--git-path", STAMP_NAME]).strip()).resolve()


def dirty_entries() -> list[str]:
    # ignore された追跡外のファイルは出ない。出るものは、ゲートが見ていない入力になり得る
    output = run_command(["git", "status", "--porcelain", "--untracked-files=all"])
    return [line for line in output.splitlines() if line.strip()]


def write(gate: Gate) -> int:
    if not in_work_tree():
        print("git の作業木の外なので、印を書かない", file=sys.stderr)
        return 0
    entries = dirty_entries()
    if entries:
        print(
            "未コミットの変更があるので、印を書かない（ゲートが見たのはコミットと違う木）:",
            file=sys.stderr,
        )
        print("\n".join(f"  {entry}" for entry in entries), file=sys.stderr)
        return 1
    head = run_command(["git", "rev-parse", "HEAD"]).strip()
    stamp_path().write_text(f"{head} {gate}\n", encoding="utf-8")
    print(f"印を書いた: {head} {gate}", file=sys.stderr)
    return 0


def peel_to_commit(sha: str) -> str | None:
    # 注釈付きタグの SHA はタグのオブジェクトなので、指すコミットまで剥がす
    try:
        return run_command(["git", "rev-parse", "--verify", f"{sha}^{{commit}}"]).strip()
    except CommandError:
        return None


def read_stamp() -> Stamp | None:
    path = stamp_path()
    if not path.exists():
        return None
    fields = path.read_text(encoding="utf-8").split()
    if not fields:
        return None
    # SHA だけの前の形の印は、重いゲート（check）を通した印として読む
    kind = fields[1] if len(fields) > 1 else Gate.CHECK
    try:
        return Stamp(fields[0], Gate(kind))
    except ValueError:
        # 知らない種類の印はどのゲートも通した証にならないので、印が無いのと同じに扱う
        return None


def default_branch_tip() -> str | None:
    try:
        return run_command(
            ["git", "rev-parse", "--verify", "refs/remotes/origin/HEAD^{commit}"]
        ).strip()
    except CommandError:
        # origin の既定の枝を決められない。差分の基準が無いので、呼び出し側が check を求める
        return None


def base_commit(ref: PushedRef) -> str | None:
    if ref.has_remote_tip:
        try:
            return run_command(
                ["git", "rev-parse", "--verify", f"{ref.remote_tip}^{{commit}}"]
            ).strip()
        except CommandError:
            # リモートの先端がこの複製に無い。新しい枝と同じく既定の枝を基準にする
            pass
    return default_branch_tip()


def required_gate(ref: PushedRef) -> Gate:
    base = base_commit(ref)
    if base is None:
        return Gate.CHECK
    try:
        # 改名は旧新どちらのパスも出す。文書から外へ動かした変更を文書だけと見誤らないため
        # -z は引用符と 8 進の逃がしを止め、日本語のパスを生の名前で返す
        output = run_command(
            ["git", "diff", "--name-only", "-z", "--no-renames", f"{base}...{ref.commit}"]
        )
    except CommandError:
        # 共通の祖先が無いなど、差分を取れない。文書だけとは言えない
        return Gate.CHECK
    paths = [path for path in output.split("\0") if path]
    return Gate.DOCS if paths and all(is_document(path) for path in paths) else Gate.CHECK


def check(refs: Sequence[PushedRef]) -> int:
    stamp = read_stamp()
    peeled: list[PushedRef] = []
    for ref in refs:
        commit = peel_to_commit(ref.commit)
        if commit is None:
            print(
                f"push を止めた: {ref.commit} はコミットを指さない"
                "（知らない SHA か、剥がし先が無い）",
                file=sys.stderr,
            )
            print("  コミットを指す ref だけを push する", file=sys.stderr)
            return 1
        peeled.append(dataclasses.replace(ref, commit=commit))
    refused: list[tuple[str, Gate]] = []
    for ref in peeled:
        needed = required_gate(ref)
        if stamp is None or stamp.commit != ref.commit or not stamp.gate.covers(needed):
            refused.append((ref.commit, needed))
    if not refused:
        return 0
    state = "印が無い" if stamp is None else f"印は {stamp.commit} {stamp.gate}"
    names = ", ".join(commit for commit, _ in refused)
    print(f"push を止めた: {names} はゲートを通っていない（{state}）", file=sys.stderr)
    strongest = max((needed for _, needed in refused), key=lambda gate: gate.strength)
    print(
        f"  このコミットで {GATE_COMMANDS[strongest]} を緑にしてから push する",
        file=sys.stderr,
    )
    return 1


def install_hook() -> int:
    if not in_work_tree():
        print("git の作業木の外なので、hook を入れない", file=sys.stderr)
        return 0
    run_command(["git", "config", "core.hooksPath", HOOKS_PATH])
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    write_parser = commands.add_parser("write")
    write_parser.add_argument("--gate", type=Gate, choices=list(Gate), default=Gate.CHECK)
    commands.add_parser("install-hook")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--remote", default=ZERO_SHA)
    check_parser.add_argument("shas", nargs="+")
    arguments = parser.parse_args(argv)
    if arguments.command == "write":
        return write(arguments.gate)
    if arguments.command == "check":
        return check([PushedRef(sha, arguments.remote) for sha in arguments.shas])
    return install_hook()


if __name__ == "__main__":
    sys.exit(main())
