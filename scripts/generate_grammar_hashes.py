"""開発用。パックが配る全 platform の配布物から、組み込みの文法の sha256 を作る。

jigane/languages/grammar-hashes.json と、例の外の言語の宣言（GRAMMAR_HASHES）を書き出す。
パックの版を上げたときに作り直す。

    env -u PYTHONPATH uv run --locked python scripts/generate_grammar_hashes.py

作り方は `jigane languages fetch` が照合する対象と同じである。パックはリリースの parsers.json
（マニフェスト）に、platform ごとの配布物 parsers-<platform>.tar.zst の URL と sha256 を載せる。
その中の共有ライブラリを実行時に記録と照合する。ここでは配布物を一時の場所へ落とし、
マニフェストの sha256 と照らしてから、共有ライブラリの中身の sha256 を計算する。要 zstd コマンド。
"""

import argparse
import fnmatch
import hashlib
import json
import re
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from dev_launch import run_tool

from jigane.languages.grammars import _LIBRARY_STEMS, GRAMMARS, HASHES_PATH, pack_version

# 取得先の一か所。パックの版は入っているパック（uv.lock の固定）から取る
RELEASE_BASE = "https://github.com/xberg-io/tree-sitter-language-pack/releases/download"
MANIFEST_NAME = "parsers.json"
_CHUNK = 1 << 20
# 例の外の言語（examples/jigane-language-kotlin）の宣言も、同じ配布物から作り直す
EXAMPLE_GRAMMAR = "kotlin"
EXAMPLE_PATH = (
    Path(__file__).parent.parent
    / "examples"
    / "jigane-language-kotlin"
    / "jigane_language_kotlin"
    / "__init__.py"
)
_ALL_GRAMMARS = (*GRAMMARS, EXAMPLE_GRAMMAR)
_EXAMPLE_BLOCK = re.compile(r"GRAMMAR_HASHES = \{.*?\n\}\n", re.DOTALL)


def _download(url: str, destination: Path) -> str:
    """URL の中身を destination へ書き、sha256 を返す。"""
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as out:  # noqa: S310 取得先は定数の https
        while chunk := response.read(_CHUNK):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def _library_hashes(bundle: Path) -> dict[str, str]:
    """配布物の中の、組み込みの文法の sha256。探し方は照合側の library_path と揃える。"""
    archive_path = bundle.with_suffix("")
    run_tool(["zstd", "-d", "-q", "-f", "-o", str(archive_path), str(bundle)])
    found: dict[str, dict[str, str]] = {grammar: {} for grammar in _ALL_GRAMMARS}
    with tarfile.open(archive_path, mode="r:") as archive:
        for member in archive:
            if not member.isfile():
                continue
            name = Path(member.name).name
            for grammar in _ALL_GRAMMARS:
                stem = f"tree_sitter_{_LIBRARY_STEMS.get(grammar, grammar)}"
                extracted = archive.extractfile(member)
                if extracted is not None and fnmatch.fnmatchcase(name, f"*{stem}.*"):
                    found[grammar][name] = hashlib.sha256(extracted.read()).hexdigest()
    hashes: dict[str, str] = {}
    for grammar, candidates in found.items():
        if not candidates:
            raise SystemExit(f"{bundle.name} に文法 {grammar} の共有ライブラリがありません")
        # 照合の側は、名前順に並べた先頭を使う
        hashes[grammar] = candidates[min(candidates)]
    return hashes


def generate(version: str) -> dict[str, dict[str, str]]:
    base = f"{RELEASE_BASE}/v{version}"
    with tempfile.TemporaryDirectory(prefix="jigane-grammar-hashes-") as work_name:
        work = Path(work_name)
        manifest_path = work / MANIFEST_NAME
        _download(f"{base}/{MANIFEST_NAME}", manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["version"] != version:
            raise SystemExit(
                f"マニフェストの版 {manifest['version']} が、パックの版 {version} と違います"
            )
        result: dict[str, dict[str, str]] = {}
        for platform_name, bundle in sorted(manifest["platforms"].items()):
            if not bundle["url"].startswith(f"{base}/"):
                raise SystemExit(
                    f"{platform_name} の配布物の URL が想定の取得先の外です: {bundle['url']}"
                )
            destination = work / f"{platform_name}.tar.zst"
            actual = _download(bundle["url"], destination)
            if actual != bundle["sha256"]:
                raise SystemExit(f"{platform_name} の配布物の sha256 がマニフェストと合いません")
            result[platform_name] = _library_hashes(destination)
            sys.stderr.write(f"{platform_name}: {Path(bundle['url']).name}\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=HASHES_PATH)
    arguments = parser.parse_args()
    generated = generate(pack_version())
    builtin = {
        name: {grammar: hashes[grammar] for grammar in GRAMMARS}
        for name, hashes in generated.items()
    }
    arguments.output.write_text(json.dumps(builtin, indent=2) + "\n", encoding="utf-8")
    entries = "".join(
        f'    "{name}": "{hashes[EXAMPLE_GRAMMAR]}",\n'
        for name, hashes in sorted(generated.items())
    )
    source = EXAMPLE_PATH.read_text(encoding="utf-8")
    block = f"GRAMMAR_HASHES = {{\n{entries}}}\n"
    rewritten, count = _EXAMPLE_BLOCK.subn(lambda _match: block, source, count=1)
    if count != 1:
        # 黙って書き換えずに終えると、例の宣言だけが古い記録のまま残る
        sys.stderr.write(f"{EXAMPLE_PATH} に GRAMMAR_HASHES のブロックが見つかりません\n")
        return 1
    EXAMPLE_PATH.write_text(rewritten, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
