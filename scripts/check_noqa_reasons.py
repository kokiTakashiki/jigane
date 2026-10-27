"""行ごとの `# noqa` に、規則の番号の後ろの理由が書かれているかを調べる。

ruff の PGH004 は番号の無い noqa を、RUF100 は使っていない noqa を落とすが、
番号があって理由が無い noqa は通る。その穴をここで埋める。
文字列の中の `# noqa` を拾わないよう、tokenize が返すコメントだけを見る。
ファイル全体に掛ける `# ruff: noqa` は対象にしない。

使い方: check_noqa_reasons.py [<根>]
"""

import io
import sys
import tokenize
from pathlib import Path

from jigane.git.tree_files import work_files


def _is_code(word: str) -> bool:
    letters = len(word) - len(word.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    return 0 < letters < len(word) and word[letters:].isdigit()


def _has_reason(after_noqa: str) -> bool:
    """`noqa` の直後の文字列に、規則の番号の並びを除いた理由が残るか。"""
    rest = after_noqa.lstrip()
    if not rest.startswith(":"):
        # 番号の無い noqa。番号が無ければ理由を書く場所も無い。
        return False
    # ruff は番号をカンマと空白のどちらで区切っても受けるので、番号でない語に当たるまで読む。
    words = rest[1:].replace(",", " ").split()
    position = 0
    while position < len(words) and _is_code(words[position]):
        position += 1
    return any(character not in "- " for character in " ".join(words[position:]))


def lines_without_reason(source: str) -> list[int]:
    """理由の無い行ごとの noqa がある行の番号。読めない構文では例外を投げる。"""
    numbers: list[int] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        # 別の説明の後ろに続く形でも、ruff は noqa を読む。
        for segment in token.string.split("#")[1:]:
            text = segment.lstrip()
            if text[:4].lower() != "noqa" or text[4:5].isalnum():
                continue
            if not _has_reason(text[4:]):
                numbers.append(token.start[0])
    return numbers


def find_offenders(root: Path) -> list[str]:
    offenders: list[str] = []
    for name in work_files(root, include_untracked=True).files:
        if not name.endswith(".py"):
            continue
        try:
            source = (root / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # バイナリは noqa を持たない。
            continue
        try:
            numbers = lines_without_reason(source)
        except (tokenize.TokenError, SyntaxError):
            # 読めないファイルを見逃すと、その中の noqa が検査を通り抜ける。
            offenders.append(f"{name}（構文を読めない）")
            continue
        offenders.extend(f"{name}:{number}" for number in numbers)
    return offenders


def main(argv: list[str]) -> int:
    root = Path(argv[0] if argv else ".").resolve()
    offenders = find_offenders(root)
    for offender in offenders:
        print(f"noqa の検査に通らない: {offender}", file=sys.stderr)
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
