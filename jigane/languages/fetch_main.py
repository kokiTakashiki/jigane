"""`python -m jigane.languages.fetch_main fetch`。依存の導入のあとに文法を取得する入口。"""

import sys
from dataclasses import dataclass
from typing import assert_never

from jigane.languages import registry
from jigane.languages.grammars import GrammarError, fetch
from jigane.languages.plugins import sanitize_text


@dataclass(frozen=True)
class FetchAborted:
    """組み込みの文法の失敗や外の言語の不在で、取得が途中で止まった。"""


@dataclass(frozen=True)
class Fetched:
    """取得し終えた。取得できた外の言語の名前と、失敗した言語の名前を持つ。"""

    fetched_external: tuple[str, ...]
    failed_languages: tuple[str, ...]


FetchOutcome = FetchAborted | Fetched


def fetch_and_report() -> FetchOutcome:
    """文法を取得し、失敗を `jigane: …` の行にして stderr へ出す。入口はこれを呼ぶだけにする。"""
    try:
        external = registry.discovered_plugins()
        failures = fetch(external)
    except (GrammarError, registry.LanguagePluginUnavailableError) as error:
        sys.stderr.write(f"jigane: {sanitize_text(str(error))}\n")
        return FetchAborted()
    for failure in failures:
        # 言語名も失敗の文も外のコードと外の宣言が決めた文字列で、端末へ出る前に制御文字を除く
        language = sanitize_text(failure.target.language)
        reason = sanitize_text(str(failure.reason))
        sys.stderr.write(f"jigane: 言語 {language}: {reason}\n")
    failed_languages = {failure.target.language for failure in failures}
    return Fetched(
        fetched_external=tuple(
            sanitize_text(plugin.adapter.name)
            for plugin in external
            if plugin.adapter.name not in failed_languages
        ),
        failed_languages=tuple(sanitize_text(name) for name in sorted(failed_languages)),
    )


def main(argv: list[str]) -> int:
    if argv != ["fetch"]:
        sys.stderr.write("使い方: python -m jigane.languages.fetch_main fetch\n")
        return 2
    # 入口が違うので終了コードも違う。ここは make と just 向けで、使い方の誤りを 2 で区別する
    match fetch_and_report():
        case FetchAborted():
            return 1
        case Fetched(failed_languages=failed_languages):
            return 1 if failed_languages else 0
        case _ as unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
