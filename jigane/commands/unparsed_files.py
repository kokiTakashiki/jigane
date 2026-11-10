"""解析しない言語のファイルの分類と案内。"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from jigane.commands.consent import ConsentDeclinedError, is_yes
from jigane.git.process import CONSENT_PROMPT_END
from jigane.judgment_map.map_kind import CodeMap
from jigane.languages import registry
from jigane.languages.grammars import FETCH_COMMAND
from jigane.languages.states import LanguageGrammarAbsent
from jigane.reviews.lifecycle import Review
from jigane.text.document_files import DOCUMENT_SUFFIXES
from jigane.text.terminal import one_line_text

# 言語のソースでない文書とデータの形式。アダプタが無くても、宣言を読めない言語としては数えない
NON_SOURCE_SUFFIXES = DOCUMENT_SUFFIXES | frozenset(
    {".markdown", ".json", ".yaml", ".yml", ".toml", ".csv", ".lock"}
)
# 拡張子ごとに示すファイルの数。残りは件数だけにする
_SHOWN_FILES_PER_SUFFIX = 5


@dataclass(frozen=True)
class UnparsedFiles:
    """範囲のうち、解析しない言語のソースのファイル。ファイルは入った順に重複なく並ぶ。"""

    # 言語の名前 -> ファイル。文法を取得していないために解析できない、選べる言語
    grammar_absent: dict[str, tuple[str, ...]]
    # 拡張子 -> ファイル。どの選べる言語の拡張子でもない
    no_adapter: dict[str, tuple[str, ...]]

    @property
    def is_empty(self) -> bool:
        return not self.grammar_absent and not self.no_adapter


def unparsed_files(review: Review) -> UnparsedFiles:
    """解析しない言語のソースのファイルを、文法の無い言語と、アダプタの無い拡張子に分ける。"""
    absent: dict[str, list[str]] = {}
    no_adapter: dict[str, list[str]] = {}
    # 設計書の地図の区間は参考のコードで、設計書のレビューは宣言を読む対象ではない
    if not isinstance(review.map_kind, CodeMap):
        return UnparsedFiles({}, {})
    for segment in review.map_kind.segments:
        path = segment.new_path or segment.old_path
        if path is None:
            # 新旧どちらの経路も無い区間は、数える対象のファイルを持たない
            continue
        suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
        if not suffix or suffix in NON_SOURCE_SUFFIXES:
            # 拡張子が無いと言語を決められず、文書とデータの形式は言語ではない
            continue
        if registry.adapter_for(path) is not None or registry.is_build_path(
            path, registry.adapters()
        ):
            # アダプタがあるか、ビルドのファイルで「重要」になるので、確認の対象にしない
            continue
        language = registry.language_for_suffix(path)
        if isinstance(language, LanguageGrammarAbsent):
            files = absent.setdefault(language.name, [])
        elif language is None:
            files = no_adapter.setdefault(suffix, [])
        else:
            # 文法がある言語は解析するので、案内の対象にしない
            continue
        if path not in files:
            files.append(path)
    return UnparsedFiles(_settled(absent), _settled(no_adapter))


def _settled(grouped: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
    return {key: tuple(files) for key, files in grouped.items()}


def _files_text(files: tuple[str, ...]) -> str:
    shown = ", ".join(one_line_text(name) for name in files[:_SHOWN_FILES_PER_SUFFIX])
    rest = len(files) - _SHOWN_FILES_PER_SUFFIX
    return f"（{len(files)} 件）: {shown}" + (f" ほか {rest} 件" if rest > 0 else "")


def unparsed_text(found: UnparsedFiles) -> list[str]:
    """解析しない言語の案内の文面。どちらの分けも空なら空の並び。"""
    if found.is_empty:
        return []
    lines = ["解析しない言語のファイルが、判断の地図の区間に含まれています。"]
    if found.grammar_absent:
        lines.append("文法を取得していない言語:")
        names = sorted(found.grammar_absent)
        lines.extend(
            f"  {one_line_text(name)}{_files_text(found.grammar_absent[name])}" for name in names
        )
        targets = " ".join(one_line_text(name) for name in names)
        lines.append(f"`{FETCH_COMMAND} {targets}` で取得すると解析できます。")
    if found.no_adapter:
        lines.append("アダプタの無い言語:")
        lines.extend(
            f"  {one_line_text(suffix)}{_files_text(found.no_adapter[suffix])}"
            for suffix in sorted(found.no_adapter)
        )
        lines.append(
            "jigane の docs/adding-languages.md に従ってアダプタを書くか、"
            "入れたプラグインを `jigane languages enable` で有効にすると解析できます。"
        )
    lines.append("これらの区間は宣言を読まずに「確認」になります。")
    return lines


def print_unparsed_notice(review: Review) -> None:
    """解析しない言語の案内を標準エラーへ出す。止めず、尋ねず、何も無ければ何も出さない。"""
    for line in unparsed_text(unparsed_files(review)):
        print(line, file=sys.stderr)


def require_unparsed_consent(review: Review, *, read_answer: Callable[[str], str]) -> None:
    """解析しない言語のファイルがあるとき、y か yes のときだけ返る。

    端末かどうかは費用の同意（require_consent）が先に確かめているので、ここでは確かめない。
    """
    lines = unparsed_text(unparsed_files(review))
    if not lines:
        return
    for line in lines:
        print(line, file=sys.stderr)
    if not is_yes(read_answer(f"それでもレビューを実施しますか{CONSENT_PROMPT_END}")):
        raise ConsentDeclinedError("同意が得られなかったので起動しませんでした")
