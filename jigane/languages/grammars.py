"""tree-sitter の文法を、固定の置き場に取得し、読み込む前に sha256 を記録と照合する。

tree-sitter-language-pack は文法を同梱せず、キャッシュに無い文法を暗黙に取得する。
取得先の照合は同じリリースのマニフェストだけで署名が無く、展開後の共有ライブラリは読み込み時に
再照合されない。そこで取得は `jigane languages fetch`（開発の木では `make setup`）を
走らせたときに限り、実行時は置き場の実物を記録と照合する。
"""

import hashlib
import json
import os
import platform
import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

import tree_sitter_language_pack as pack
from tree_sitter import Language, Parser

if TYPE_CHECKING:
    # plugin.py は base.py を経て本モジュールを読み込むため、型の注釈にだけ使う
    from collections.abc import Iterable

    from jigane.languages.plugin import GrammarSpec, LanguagePlugin

CACHE_ENV = "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR"
HASHES_PATH = Path(__file__).with_name("grammar-hashes.json")
GRAMMARS = ("python", "java", "swift", "csharp")
# パックの共有ライブラリのファイル名は、言語名と一致しない言語がある
_LIBRARY_STEMS = {"csharp": "c_sharp"}
_SETUP_ACTION = "`jigane languages fetch` を走らせ直してください（開発の木では `make setup`）"
_FETCH_ACTION = "`jigane languages fetch` を走らせてください"
# 文法名は共有ライブラリを探す glob に入る。外のコードが決める文字列なので、字種を絞る
_GRAMMAR_NAME = re.compile(r"[a-z0-9_]+")


class GrammarError(RuntimeError):
    """文法が置き場に無い、または記録と合わない。

    `problem` は何が起きたか、`action` は直す操作。文字列にすると両方が並ぶ。失敗の表示は
    次の操作を別に出すため、`problem` だけを使い、案内が重ならないようにする。
    """

    def __init__(self, problem: str, action: str = "") -> None:
        super().__init__(f"{problem}。{action}" if action else problem)
        self.problem = problem
        self.action = action


def pack_version() -> str:
    return metadata.version("tree-sitter-language-pack")


def cache_root() -> Path:
    # 文法は Python の環境に付く物で、利用者のレビューの記録の置き場（data_home）とは分ける
    return Path(sys.prefix) / "share" / "jigane" / "grammars" / pack_version()


def library_dir(root: Path) -> Path:
    return root / "tree-sitter-language-pack" / f"v{pack_version()}" / "libs"


def platform_key() -> str:
    """パックのマニフェストと同じ表記（macos-arm64、linux-x86_64 など）。"""
    system = {"darwin": "macos", "linux": "linux", "windows": "windows"}.get(
        platform.system().lower(), platform.system().lower()
    )
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        machine = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        machine = "arm64" if system == "macos" else "aarch64"
    return f"{system}-{machine}"


def library_path(root: Path, grammar: str) -> Path | None:
    stem = f"tree_sitter_{_LIBRARY_STEMS.get(grammar, grammar)}"
    # 拡張子（.dylib・.so・.dll）と接頭辞（lib の有無）は platform で変わる。名前の前に別の語が
    # 付いた物は拾わない。候補が複数あると、照合した物とパックが読む物が食い違いうるため止める
    pattern = re.compile(rf"(?:lib)?{re.escape(stem)}\.[A-Za-z0-9]+")
    found = sorted(
        path for path in library_dir(root).glob(f"*{stem}.*") if pattern.fullmatch(path.name)
    )
    if len(found) > 1:
        names = ", ".join(path.name for path in found)
        raise GrammarError(
            f"文法 {grammar} の共有ライブラリが複数あります: {names}", _SETUP_ACTION
        )
    return found[0] if found else None


def _recorded() -> dict[str, dict[str, str]]:
    recorded: dict[str, dict[str, str]] = json.loads(HASHES_PATH.read_text(encoding="utf-8"))
    return recorded


def recorded_hashes() -> dict[str, dict[str, str]]:
    """platform の名前 → 文法名 → sha256。記録の写しを返す。"""
    return _recorded()


def verify(root: Path, grammar: str) -> None:
    """組み込みの文法の実物を確かめる。無い、または記録と違えば GrammarError。"""
    if grammar not in GRAMMARS:
        raise GrammarError(f"未知の文法: {grammar}")
    path = library_path(root, grammar)
    if path is None:
        raise GrammarError(f"文法 {grammar} が {library_dir(root)} にありません", _SETUP_ACTION)
    expected = _recorded().get(platform_key(), {}).get(grammar)
    if expected is None:
        # 記録の無い platform では照合の手段が無い。確かめられない実物は読み込まない
        raise GrammarError(
            f"{platform_key()} には文法 {grammar} のハッシュの記録がありません。"
            "README の「文法の取得」の節に、記録のある platform の一覧があります",
            "この platform への対応は https://github.com/kokiTakashiki/jigane/issues "
            "で知らせてください",
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise GrammarError(f"文法 {grammar} のハッシュが記録と合いません", _SETUP_ACTION)


def require_present(root: Path, grammar: str) -> None:
    """組み込みの文法の共有ライブラリが置き場に在るかだけを見る。無ければ GrammarError。

    sha256 の照合は読み込みの時（`verify`）に行う。起動のたびに照合すると、試験が遅くなるため。
    """
    if library_path(root, grammar) is None:
        raise GrammarError(f"文法 {grammar} が {library_dir(root)} にありません", _SETUP_ACTION)


def _declaration_error(grammar: str, spec: "GrammarSpec") -> GrammarError | None:
    """置き場を見ずに分かる、宣言の誤り。"""
    if not _GRAMMAR_NAME.fullmatch(grammar):
        return GrammarError(f"文法名 {grammar!r} は英小文字、数字、_ だけで書きます")
    if spec.pack_version != pack_version():
        return GrammarError(
            f"文法 {grammar} の宣言のパックの版 {spec.pack_version} が、"
            f"入っているパックの版 {pack_version()} と違います"
        )
    if platform_key() not in spec.hashes:
        return GrammarError(f"文法 {grammar} の宣言に {platform_key()} のハッシュがありません")
    return None


def verify_declared(root: Path, grammar: str, spec: "GrammarSpec") -> None:
    """外の言語の文法の実物を、その言語の宣言と照合する。組み込みと違い、漏れは全て誤りにする。"""
    error = _declaration_error(grammar, spec)
    if error is not None:
        raise error
    path = library_path(root, grammar)
    if path is None:
        raise GrammarError(f"文法 {grammar} が {library_dir(root)} にありません", _FETCH_ACTION)
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec.hashes[platform_key()]:
        raise GrammarError(f"文法 {grammar} のハッシュが宣言と合いません", _FETCH_ACTION)


@dataclass(frozen=True)
class BuiltinGrammar:
    """組み込みの記録で照合する文法。外の言語の宣言が組み込みと同じ文法名を指す場合もここに入る。"""

    name: str

    def verify(self, root: Path) -> None:
        verify(root, self.name)


@dataclass(frozen=True)
class DeclaredGrammar:
    """外の言語の宣言で照合する文法。"""

    name: str
    language: str
    spec: "GrammarSpec"

    def verify(self, root: Path) -> None:
        verify_declared(root, self.name, self.spec)


GrammarTarget = BuiltinGrammar | DeclaredGrammar


@dataclass(frozen=True)
class FetchFailure:
    """外の言語の文法一件の、取得または照合の失敗。組み込みの失敗は例外にするので、対象は宣言の側だけ。"""

    target: DeclaredGrammar
    reason: GrammarError


def external_target(plugin: "LanguagePlugin") -> GrammarTarget:
    grammar = plugin.adapter.grammar
    # 組み込みと同じ文法名を指す宣言は、宣言のハッシュを使わず組み込みの記録で照合する。
    # この決定は対象を作るここだけで行う
    if grammar in GRAMMARS:
        return BuiltinGrammar(grammar)
    return DeclaredGrammar(grammar, plugin.adapter.name, plugin.grammar)


def targets(external: "Iterable[LanguagePlugin]" = ()) -> tuple[GrammarTarget, ...]:
    """取得と照合の対象の一覧。組み込みの文法に、外の言語（Ready のもの）の文法を続ける。"""
    declared = (
        target for target in map(external_target, external) if isinstance(target, DeclaredGrammar)
    )
    return (*(BuiltinGrammar(grammar) for grammar in GRAMMARS), *declared)


# 照合の済んだ対象の記録。宣言の中身（版とハッシュ）を鍵に含め、宣言が変われば引き直させる
_checked: set[tuple[object, ...]] = set()


def _check_key(root: Path, target: GrammarTarget) -> tuple[object, ...]:
    match target:
        case BuiltinGrammar():
            return (root, target.name)
        case DeclaredGrammar():
            spec = target.spec
            return (root, target.name, spec.pack_version, tuple(sorted(spec.hashes.items())))
        case _ as unreachable:
            assert_never(unreachable)


def _prepare(target: GrammarTarget) -> None:
    root = cache_root()
    # 実行時に暗黙の取得をさせないため、読み込みの前に存在と記録を確かめる
    key = _check_key(root, target)
    if key not in _checked:
        target.verify(root)
        _checked.add(key)
    os.environ[CACHE_ENV] = str(root)
    # 照合した置き場と、パックが読む置き場が同じであることを、パックの答えで確かめる
    if Path(pack.cache_dir()) != library_dir(root):
        raise GrammarError(
            f"パックが読む置き場 {pack.cache_dir()} が、"
            f"照合した置き場 {library_dir(root)} と違います",
            _SETUP_ACTION,
        )


def get_parser(target: GrammarTarget) -> Parser:
    _prepare(target)
    return pack.get_parser(target.name)


def get_language(target: GrammarTarget) -> Language:
    _prepare(target)
    return pack.get_language(target.name)


def fetch(external: "Iterable[LanguagePlugin]" = ()) -> list[FetchFailure]:
    """組み込みと外の言語の文法を固定の置き場へ取得し、取得の直後に照合する。

    外の言語の取得と照合の誤りは、その言語の分だけを返して他へ進む。
    組み込みの文法の誤りは、これまで通り例外にする。
    """
    root = cache_root()
    os.environ[CACHE_ENV] = str(root)
    _checked.clear()
    pack.download(list(GRAMMARS))
    failures: list[FetchFailure] = []
    fetched: dict[str, GrammarError | None] = {}
    for target in targets(external):
        match target:
            case BuiltinGrammar():
                target.verify(root)
            case DeclaredGrammar():
                if target.name not in fetched:
                    fetched[target.name] = _download_external(target)
                error = fetched[target.name]
                if error is None:
                    try:
                        target.verify(root)
                    except GrammarError as verify_error:
                        error = verify_error
                if error is not None:
                    failures.append(FetchFailure(target, error))
            case _ as unreachable:
                assert_never(unreachable)
    return failures


def _download_external(target: DeclaredGrammar) -> GrammarError | None:
    if _declaration_error(target.name, target.spec) is not None:
        # 誤った宣言の文法は取得しない。誤りは照合が示す
        return None
    try:
        pack.download([target.name])
    except Exception as error:  # noqa: BLE001 取得先の失敗の型は多様で絞れない
        # 取得先とパックの失敗の型は多様で、1 つの言語の失敗で他の言語を止めない
        return GrammarError(
            f"文法 {target.name} を取得できません: {type(error).__name__}: {error}"
        )
    return None
