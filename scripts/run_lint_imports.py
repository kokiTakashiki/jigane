"""標準出力を UTF-8 にしてから lint-imports を走らせる。

import-linter は日本語の契約名を表示するため、
Windows の既定の文字コード（cp1252）のままだと落ちる。それを避ける。
"""

import sys

from importlinter.cli import lint_imports_command


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    try:
        lint_imports_command()
    except SystemExit as finished:
        # click の単独実行は終了コードを SystemExit で返す。
        # 0 でも例外で抜けるので、ここで受けて数に直す。
        return finished.code if isinstance(finished.code, int) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
