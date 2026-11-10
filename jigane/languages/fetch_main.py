"""`python -m jigane.languages.fetch_main fetch`。依存の導入のあとに文法を取得する入口。"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import assert_never

from jigane.languages import registry
from jigane.languages.grammars import GRAMMARS, GrammarError, fetch
from jigane.text.terminal import sanitize_text


@dataclass(frozen=True)
class FetchAborted:
    """組み込みの文法の失敗や追加の言語の不在で、取得が途中で止まった。"""


@dataclass(frozen=True)
class Fetched:
    """取得し終えた。取得できた追加の言語の名前と、失敗した言語の名前を持つ。"""

    fetched_external: tuple[str, ...]
    failed_languages: tuple[str, ...]
    fetched_builtin: tuple[str, ...] = field(default=GRAMMARS)


FetchOutcome = FetchAborted | Fetched


def selectable_names() -> tuple[str, ...]:
    """取得する言語として名前で選べるもの。組み込みと、有効にした追加の言語。"""
    return (*GRAMMARS, *(plugin.adapter.name for plugin in registry.discovered_plugins()))


def validated_names(names: Sequence[str]) -> tuple[str, ...]:
    """並べた名前の重複を除く。選べない名前があれば ValueError にする。"""
    accepted = selectable_names()
    unknown = [name for name in dict.fromkeys(names) if name not in accepted]
    if unknown:
        # 打った名前も、プラグインが決めた言語の名前も端末へ出る
        shown = sanitize_text(", ".join(map(repr, unknown)))
        allowed = sanitize_text(", ".join(accepted))
        raise ValueError(f"取得できない言語の名前: {shown}（受けられる名前: {allowed}）")
    return tuple(dict.fromkeys(names))


def fetch_and_report(names: tuple[str, ...] | None = None) -> FetchOutcome:
    """文法を取得し、失敗を `jigane: …` の行にして stderr へ出す。入口はこれを呼ぶだけにする。

    `names` は `validated_names` を通した名前の並び。None ならすべてを取得する。
    """
    try:
        external = registry.discovered_plugins()
        failures = fetch(external) if names is None else fetch(external, names)
    except (GrammarError, registry.LanguagePluginUnavailableError) as error:
        sys.stderr.write(f"jigane: {sanitize_text(str(error))}\n")
        return FetchAborted()
    for failure in failures:
        # 言語名も失敗の文もプラグインのコードと追加の言語の宣言が決めた文字列で、
        # 端末へ出る前に制御文字を除く
        language = sanitize_text(failure.target.language)
        reason = sanitize_text(str(failure.reason))
        sys.stderr.write(f"jigane: 言語 {language}: {reason}\n")
    failed_languages = {failure.target.language for failure in failures}
    return Fetched(
        fetched_external=tuple(
            sanitize_text(plugin.adapter.name)
            for plugin in external
            if plugin.adapter.name not in failed_languages
            and (names is None or plugin.adapter.name in names)
        ),
        failed_languages=tuple(sanitize_text(name) for name in sorted(failed_languages)),
        fetched_builtin=tuple(name for name in GRAMMARS if names is None or name in names),
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
