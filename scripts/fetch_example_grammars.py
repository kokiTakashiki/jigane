"""開発用。例のパッケージの言語の文法を、jigane の照合の経路で取得して照合する。

`make setup` の最後の段で走る。利用者向けの languages.json は読まず、書かない。
例の試験はここで取得した文法を読むだけで、試験の中では取得しない。
"""

import sys

from jigane_language_kotlin import plugin as kotlin_plugin

from jigane.languages.grammars import GrammarError, fetch


def main() -> int:
    try:
        failures = fetch([kotlin_plugin()])
    except GrammarError as error:
        sys.stderr.write(f"jigane: {error}\n")
        return 1
    for failure in failures:
        sys.stderr.write(f"jigane: 言語 {failure.target.language}: {failure.reason}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
