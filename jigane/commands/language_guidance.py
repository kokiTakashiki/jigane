"""使えない言語の理由に、公開のリポジトリの案内先を足す。"""

from jigane.commands.public_urls import blob_base_url, installed_version
from jigane.languages.states import LanguageUnavailable, ReasonKind

_GUIDE_PATH = "docs/adding-languages.md#言語プラグインのインターフェースの改名"


def guide_url() -> str:
    return f"{blob_base_url(installed_version())}/{_GUIDE_PATH}"


def detail_with_guidance(state: LanguageUnavailable) -> str:
    """理由の文。古いバージョンなら直し方の URL を、import の失敗なら案内の一行を足す。"""
    detail = state.detail
    if state.reason_kind == ReasonKind.API_VERSION_OLD:
        detail = f"{detail}直し方は {guide_url()} にあります。"
    if state.import_failed:
        detail = (
            f"{detail}\n言語プラグインの書き方と、0.1.9 での変更点は {guide_url()} にあります。"
        )
    return detail
