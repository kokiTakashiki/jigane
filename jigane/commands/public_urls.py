"""公開のリポジトリに置く文書の URL。規約の同意と `jigane run` の確認が使う。"""

import importlib.metadata
import re
from dataclasses import dataclass

# 公開のリポジトリの根。規約、プライバシーポリシー、SECURITY.md はここに置く
PUBLIC_REPOSITORY_BLOB_URL = "https://github.com/kokiTakashiki/jigane/blob"
DISTRIBUTION_NAME = "jigane"
# リリースのタグは `v<版>` で、版は数字の組と、あれば `.post<数>` だけでできている。
# 開発版（.dev）、前の版（a、b、rc）、local（+）は、タグが打たれないのでここに当たらない
_RELEASE_VERSION = re.compile(r"\d+(?:\.\d+)*(?:\.post\d+)?")


@dataclass(frozen=True)
class TermsUrls:
    terms_ja: str
    terms_en: str
    privacy_ja: str
    privacy_en: str
    security: str


def installed_version() -> str | None:
    """入っている jigane の版。配布物の記録が無いとき（ソースの木から動かすとき）は None。"""
    try:
        return importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        return None


def terms_urls(version: str | None) -> TermsUrls:
    """リリースの版なら、その版のタグの文書。そうでなければ main の文書。"""
    is_release = version is not None and _RELEASE_VERSION.fullmatch(version) is not None
    ref = f"v{version}" if is_release else "main"
    base = f"{PUBLIC_REPOSITORY_BLOB_URL}/{ref}"
    return TermsUrls(
        terms_ja=f"{base}/TERMS.ja.md",
        terms_en=f"{base}/TERMS.en.md",
        privacy_ja=f"{base}/PRIVACY.ja.md",
        privacy_en=f"{base}/PRIVACY.en.md",
        security=f"{base}/SECURITY.md",
    )
