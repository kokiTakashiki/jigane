"""指定した範囲のコミットの作者と委託者のメールアドレスが、GitHub の noreply だけかを確かめる。

ゲートには入れない。履歴を公開する前など、範囲を決めて手で走らせる。

  check_commit_emails.py origin/main..HEAD   origin/main から HEAD までのコミットを見る
  check_commit_emails.py --root DIR RANGE    DIR の git リポジトリを見る

範囲は git log に渡す版の指定そのままで、必須。全履歴を見るときも HEAD と明示する。
noreply 以外のアドレスのコミットがあれば、短縮した SHA と役割とアドレスを出して終了コード 1。
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from jigane.git.process import run_command

NOREPLY_DOMAIN = "users.noreply.github.com"
ADDRESS_PARTS = 2  # ローカル部とドメイン
# 区切りは改行やアドレスに現れない制御文字にする
SEPARATOR = "\x1f"
LOG_FORMAT = SEPARATOR.join(["%h", "%ae", "%ce"])


def is_noreply(address: str) -> bool:
    if not address.endswith(f"@{NOREPLY_DOMAIN}"):
        return False
    # @ が一つだけなら、ローカル部に別のアドレスを紛れ込ませた値は落ちる
    parts = address.split("@")
    if len(parts) != ADDRESS_PARTS:
        return False
    user_id, plus, name = parts[0].partition("+")
    local_part = name if plus else user_id
    if plus and not user_id.isdigit():
        return False
    return bool(local_part) and not any(character.isspace() for character in local_part)


def find_offenders(root: Path, revision_range: str) -> list[str]:
    log = run_command(["git", "log", f"--format={LOG_FORMAT}", revision_range, "--"], cwd=root)
    offenders: list[str] = []
    for line in log.splitlines():
        short_sha, author, committer = line.split(SEPARATOR)
        for role, address in (("作者", author), ("委託者", committer)):
            if not is_noreply(address):
                offenders.append(f"{short_sha} {role}: {address}")
    return offenders


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("range", help="git log に渡す版の範囲（例: origin/main..HEAD）")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="git リポジトリの場所")
    arguments = parser.parse_args(argv)
    if arguments.range.startswith("-"):
        # - 始まりの範囲は log のオプションと読まれ、--output=FILE ならファイルを書き出す
        parser.error("範囲は - で始まれない")
    offenders = find_offenders(arguments.root.resolve(), arguments.range)
    for offender in offenders:
        print(f"noreply 以外のメールアドレス: {offender}", file=sys.stderr)
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
