"""実行時の依存のライセンスが許可の一覧に収まるかを確かめる。"""

import re
import sys
from importlib import metadata
from importlib.metadata import Distribution, PackageMetadata

ROOT_DISTRIBUTION = "jigane"

# MPL-2.0 は、改変して配る場合だけ義務が生じる。
ALLOWED_LICENSES = frozenset(
    {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0", "ISC", "MPL-2.0"}
)

# Classifier の "License ::" の文から、許可の一覧の識別子への対応。
CLASSIFIER_LICENSES = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}

# 自由記述の License から識別子への対応。空白を除き小文字に直した値で引く。
FREE_TEXT_LICENSES = {
    "mit": "MIT",
    "mitlicense": "MIT",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd": "BSD-3-Clause",
    "bsdlicense": "BSD-3-Clause",
    "apache-2.0": "Apache-2.0",
    "apachelicense2.0": "Apache-2.0",
    "apachesoftwarelicense": "Apache-2.0",
    "psf-2.0": "PSF-2.0",
    "isc": "ISC",
    "mpl-2.0": "MPL-2.0",
}

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_EXTRA_MARKER = re.compile(r"extra\s*==")
_EXPRESSION_SPLIT = re.compile(r"\s+(?:AND|OR|WITH)\s+|[()]", re.IGNORECASE)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def license_identifiers(headers: PackageMetadata) -> list[str] | None:
    """依存の宣言から、ライセンスの識別子を求める。記載が無ければ None を返す。

    License-Expression、License、Classifier の順に読み、最初に見つかった宣言だけを採る。
    対応が無い宣言は、元の文字列のまま返す。
    """
    expression = headers.get("License-Expression")
    if expression:
        parts = [part.strip() for part in _EXPRESSION_SPLIT.split(expression)]
        return [part for part in parts if part]
    free_text = (headers.get("License") or "").strip()
    # 改行を含む License は、ライセンス本文の貼り付けであり識別子として読めない。
    # Classifier へ進む。
    if free_text and "\n" not in free_text:
        key = re.sub(r"\s+", "", free_text).lower()
        return [FREE_TEXT_LICENSES.get(key, free_text)]
    classifiers = [
        value for value in headers.get_all("Classifier") or [] if value.startswith("License ::")
    ]
    if classifiers:
        return [CLASSIFIER_LICENSES.get(value, value) for value in classifiers]
    return None


def license_violation(headers: PackageMetadata) -> str | None:
    """許可の一覧に収まらなければ理由を、収まれば None を返す。

    複合の式は、含まれる識別子を全て許可の一覧に入るものとして扱う（OR でも緩めない）。
    """
    identifiers = license_identifiers(headers)
    if identifiers is None:
        return "ライセンスの記載がない"
    rejected = [value for value in identifiers if value not in ALLOWED_LICENSES]
    if rejected:
        return f"許可の一覧に無いライセンス: {', '.join(rejected)}"
    return None


def runtime_distributions(root: str = ROOT_DISTRIBUTION) -> dict[str, Distribution]:
    """root の Requires-Dist から、extra の付かない要求を再帰で辿った集合を返す。"""
    found: dict[str, Distribution] = {}
    pending = [root]
    while pending:
        requirement = pending.pop()
        name_match = _NAME.match(requirement)
        if name_match is None:
            msg = f"読めない要求: {requirement}"
            raise ValueError(msg)
        key = _normalize(name_match.group())
        if key in found:
            continue
        try:
            distribution = metadata.distribution(key)
        except metadata.PackageNotFoundError:
            # 環境の印（python_version など）付きの要求は、入っていなくてよい。印の無い要求は必須。
            if ";" in requirement:
                continue
            raise
        found[key] = distribution
        pending.extend(
            value for value in distribution.requires or [] if not _EXTRA_MARKER.search(value)
        )
    return found


def check() -> dict[str, str | None]:
    """root を除く依存ごとに、違反の理由か None を返す。"""
    distributions = runtime_distributions()
    return {
        name: license_violation(distribution.metadata)
        for name, distribution in sorted(distributions.items())
        if name != ROOT_DISTRIBUTION
    }


def main() -> int:
    results = check()
    for name, violation in results.items():
        print(f"{name}: {violation or 'ok'}")
    return 1 if any(results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
