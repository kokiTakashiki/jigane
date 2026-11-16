"""開発用。文法の束（全言語の共有ライブラリを固めたもの）を、作業ツリーをまたいで使い回す。

tree-sitter-language-pack は束を一度の GET で取り、要求の全体に 60 秒の上限を掛け、再試行しない。
束は 20 MB を超えるので、回線が遅いと上限を越えて取得が止まる。文法の置き場は Python の環境ごとに
あるので、新しい作業ツリーの `make setup` は毎回取り直していた。

`seed` は共有の置き場の束を環境の置き場へ写し、`save` は環境の置き場の束を共有の置き場へ写す。
共有の置き場は git の共通のディレクトリの下に置き、同じリポジトリの作業ツリーだけで分け合う。
束のファイル名は中身の sha256 を含み、パックは使う前にマニフェストの sha256 と照合して、
合わなければ消して取り直す。写した束を信じる必要は無い。
"""

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

from jigane.git import process
from jigane.git.process import CommandError
from jigane.languages.grammars import cache_root, library_dir, pack_version

# パックが束に付ける名前（<プラットフォーム>-<sha256>.tar.zst）だけを写す
_BUNDLE_NAME = re.compile(r"[a-z0-9_]+-[a-z0-9_]+-[0-9a-f]{64}\.tar\.zst")


def shared_dir(cwd: Path | None = None) -> Path:
    """作業ツリーをまたぐ置き場。パックの版ごとに分ける。"""
    common = process.run_command(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd
    )
    return Path(common.strip()) / "jigane-grammar-bundles" / pack_version()


def environment_dir() -> Path:
    """この環境の文法の置き場で、パックが束を置く場所。"""
    return library_dir(cache_root()).parent / "bundles"


def copy_bundles(source: Path, destination: Path) -> list[str]:
    """source の束のうち destination に無いものを写し、写した名前を返す。"""
    if not source.is_dir():
        return []
    copied: list[str] = []
    for bundle in sorted(source.iterdir()):
        if not _BUNDLE_NAME.fullmatch(bundle.name) or (destination / bundle.name).exists():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        # 同時に走る別の setup が途中の束を読まないよう、一時のファイルに写してから置き換える
        with tempfile.NamedTemporaryFile(dir=destination, suffix=".part", delete=False) as part:
            temporary = Path(part.name)
        try:
            shutil.copyfile(bundle, temporary)
            temporary.replace(destination / bundle.name)
        finally:
            temporary.unlink(missing_ok=True)
        copied.append(bundle.name)
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("seed", "save"))
    args = parser.parse_args(argv)
    try:
        if args.action == "seed":
            copied = copy_bundles(shared_dir(), environment_dir())
        else:
            copied = copy_bundles(environment_dir(), shared_dir())
    except (CommandError, OSError) as error:
        # 使い回しは取得を速めるだけの補い。失敗しても setup は続け、取得はパックが行う
        sys.stderr.write(f"grammar_bundle_cache {args.action}: 使い回しを飛ばす: {error}\n")
        return 0
    for name in copied:
        sys.stdout.write(f"{args.action}: {name}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
