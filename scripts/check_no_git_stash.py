"""共有の git stash を呼ぶ手順を、ソースの語で探す。

stash の積み重ねは、同じリポジトリの全ての worktree と並行するセッションで共有される。
規則はここにだけ置き、jigane の開発のリポジトリもこのスクリプトをコマンドとして呼ぶ。
Markdown は規約の説明で語が散文に出るため、コードブロックの中の行だけを見る。
.py の呼び出しは ast-grep の規則（ast-grep/rules/）が見るため、ここでは読まない。
残るのは justfile、Makefile、Markdown のコードブロックなど、bash の文法では読めない書式の行である。
ast-grep が走査する範囲は、DEFAULT_PREFIXES のディレクトリと同じである。
entrypoints.toml の `ast-grep scan` の引数と、試験で突き合わせる。

使い方: check_no_git_stash.py <根> [<接頭辞>...]
接頭辞は `scripts/` のようなディレクトリでも、`justfile` のようなファイル名でもよい。
省略すると DEFAULT_PREFIXES を見る。

見るのは追跡したファイルと、追跡外で ignore されていない新規ファイルである。
ただし追跡外の隠しディレクトリ（.claude/ など）の下の新規ファイルは見ない。
.claude/worktrees の下にある他の作業木の写しを読まないための除外であり、
追跡したものは隠しディレクトリの下でも見る。
"""

import re
import shlex
import sys
from collections.abc import Iterator
from pathlib import Path

from jigane.git.tree_files import work_files

# 子と親の両方で意味を持つ接頭辞の和。無い接頭辞は何も拾わない。
DEFAULT_PREFIXES = (
    "jigane/",
    "tests/",
    "scripts/",
    "docs/",
    "justfile",
    "Makefile",
    "entrypoints.toml",
)
CODE_FENCE = re.compile(r"^\s*(```|~~~)")


def _has_git_stash(line: str) -> bool:
    # 記号を別の語にして、`git stash;` や `(git stash)` の記号を stash の語から切り離す。
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # shlex.split と同じく # を注釈にしない。Makefile の行は # の後ろまで見る。
    lexer.commenters = ""
    try:
        words = list(lexer)
    except ValueError:
        # 閉じていない引用符は justfile や Makefile の構文に由来するため、空白で割って読む。
        words = line.split()
    # `git -C dir stash` のようにオプションを挟む形も、git の後に stash が来る並びとして拾う。
    return "git" in words and "stash" in words[words.index("git") + 1 :]


def _lines_to_check(name: str, text: str) -> Iterator[tuple[int, str]]:
    numbered = enumerate(text.splitlines(), 1)
    if not name.endswith(".md"):
        yield from numbered
        return
    inside_code_block = False
    for number, line in numbered:
        if CODE_FENCE.match(line):
            inside_code_block = not inside_code_block
        elif inside_code_block:
            yield number, line


def find_offenders(root: Path, prefixes: tuple[str, ...]) -> list[str]:
    this_file = Path(__file__).resolve()
    offenders: list[str] = []
    for name in work_files(root, include_untracked=True).files:
        path = root / name
        if name.endswith(".py") or not name.startswith(prefixes) or path.resolve() == this_file:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # バイナリは手順を持たない。
            continue
        offenders.extend(
            f"{name}:{number}"
            for number, line in _lines_to_check(name, text)
            if _has_git_stash(line)
        )
    return offenders


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    prefixes = tuple(argv[1:]) or DEFAULT_PREFIXES
    offenders = find_offenders(Path(argv[0]).resolve(), prefixes)
    for offender in offenders:
        print(f"共有の git stash を使っている: {offender}", file=sys.stderr)
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
