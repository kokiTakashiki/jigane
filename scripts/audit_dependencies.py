"""uv.lock が固定した依存の既知の脆弱性を、pip-audit で確かめる。標準ライブラリだけで動く。

ゲートには入れない。pip-audit は脆弱性の情報をネットワークから引き、
同じ uv.lock でも日によって結果が変わる。make check に入れると緑の意味が日ごとに変わる。

uv.lock をハッシュつきの要件に書き出し、依存を解決し直さず（--no-deps）、
pip を介さず（--disable-pip）に渡す。情報を引けない依存があっても失敗にする（--strict）。
脆弱性が見つかれば終了コード 1。
"""

import subprocess
import sys
import tempfile
from pathlib import Path

# 配布先の無い、手元のパスの編集可能な依存。脆弱性の情報の引き先が無いので除く。
LOCAL_PACKAGES = ("jigane-language-kotlin",)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="jigane-audit-") as directory:
        requirements = Path(directory) / "requirements.txt"
        export = ["uv", "export", "--locked", "--format", "requirements-txt"]
        export += ["--no-header", "--quiet"]
        export += ["--no-emit-project", "-o", str(requirements)]
        for package in LOCAL_PACKAGES:
            export += ["--no-emit-package", package]
        exported = subprocess.run(export, check=False)
        if exported.returncode != 0:
            return exported.returncode
        audit = ["uv", "run", "--locked", "pip-audit", "--strict", "--disable-pip", "--no-deps"]
        audit += ["-r", str(requirements)]
        return subprocess.run(audit, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
