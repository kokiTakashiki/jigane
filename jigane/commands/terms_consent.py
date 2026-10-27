"""`jigane languages fetch` が文法を取得する前に取る、利用規約への同意。"""

import sys
from collections.abc import Callable

from jigane.commands.consent import ConsentDeclinedError, ConsentUnavailableError, is_yes
from jigane.commands.public_urls import installed_version, terms_urls
from jigane.git.process import CONSENT_PROMPT_END

ACCEPT_TERMS_OPTION = "--accept-terms"


class TermsConsentDeclinedError(ConsentDeclinedError):
    """端末で尋ねたが、規約への同意が得られなかった。"""


class TermsConsentUnavailableError(ConsentUnavailableError):
    """標準入力が端末でなく、`--accept-terms` も無いので、同意を取れなかった。"""


def terms_notice(version: str | None) -> list[str]:
    urls = terms_urls(version)
    return [
        "文法を取得する前に、利用規約とプライバシーポリシーをお読みください。",
        f"  利用規約（日本語）: {urls.terms_ja}",
        f"  利用規約（英語）: {urls.terms_en}",
        f"  プライバシーポリシー（日本語）: {urls.privacy_ja}",
        f"  プライバシーポリシー（英語）: {urls.privacy_en}",
    ]


def require_terms_consent(
    *,
    accepted: bool,
    is_terminal: Callable[[], bool],
    read_answer: Callable[[str], str],
    version_lookup: Callable[[], str | None] = installed_version,
) -> None:
    """規約に同意したときだけ返る。

    `--accept-terms` があれば尋ねない。無く、端末でなければ TermsConsentUnavailableError、
    端末で y か yes でなければ TermsConsentDeclinedError。
    """
    if accepted:
        return
    if not is_terminal():
        raise TermsConsentUnavailableError(
            "規約への同意を得られないので文法を取得しません。"
            f"端末から実行するか、規約を読んだうえで {ACCEPT_TERMS_OPTION} を付けてください"
        )
    for line in terms_notice(version_lookup()):
        print(line, file=sys.stderr)
    if not is_yes(read_answer(f"規約に同意して文法を取得しますか{CONSENT_PROMPT_END}")):
        raise TermsConsentDeclinedError("規約への同意が得られなかったので文法を取得しませんでした")
