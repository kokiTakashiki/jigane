"""`jigane languages`: 組み込みと追加の言語を一覧し、追加の言語を有効にし、外す。"""

import argparse
import json
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import ClassVar, assert_never

from jigane.commands.consent import ConsentDeclinedError, ConsentUnavailableError, is_yes
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.commands.output import print_lines
from jigane.commands.terms_consent import ACCEPT_TERMS_OPTION, require_terms_consent
from jigane.git.process import CONSENT_PROMPT_END
from jigane.languages import fetch_main, registry
from jigane.languages.approval import ApprovedContent, RecordError, read_content, reapproval_reason
from jigane.languages.enabled import (
    ENABLED_FILE,
    EnabledFileError,
    EnabledLanguage,
    load_enabled,
    save_enabled,
)
from jigane.languages.grammars import FETCH_COMMAND
from jigane.languages.plugin import Builtin, Distribution, LanguagePlugin
from jigane.languages.plugins import ENTRY_POINT_GROUP
from jigane.languages.states import (
    LanguageGrammarAbsent,
    LanguageNotEnabled,
    LanguageReady,
    LanguageState,
    LanguageUnavailable,
)
from jigane.languages.validation import validate_plugin
from jigane.skills.language_support import LanguageSupport, support_of
from jigane.skills.policy_figures import REGISTRY
from jigane.storage.store import data_home, ensure_data_home
from jigane.text.distribution_names import normalize_distribution
from jigane.text.terminal import sanitize_text

TRUST_NOTICE = (
    "追加の言語のコードと文法は、jigane と同じ権限で動きます。"
    "出どころを信頼できる配布だけを有効にしてください。"
)
CAPABILITY_NOTICE = "口は試験的です。jigane の版で変わることがあります"
GRAMMAR_NOTICE = f"文法を取得してください: {FETCH_COMMAND}"


class LanguageStatus(StrEnum):
    """一覧の行の状態。--json の state の値と表示の言葉をここで一か所に定める。"""

    READY = "ready"
    NOT_ENABLED = "not_enabled"
    GRAMMAR_ABSENT = "grammar_absent"
    UNAVAILABLE = "unavailable"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    LanguageStatus.READY: "使える",
    LanguageStatus.NOT_ENABLED: "有効でない",
    LanguageStatus.GRAMMAR_ABSENT: "文法を取得していない",
    LanguageStatus.UNAVAILABLE: "使えない",
}


@dataclass(frozen=True)
class _RowVariable:
    """状態ごとに変わる値。既定は、拡張子も理由も対応も持たない行の値。"""

    suffixes: tuple[str, ...] = ()
    reason_kind: str | None = None
    detail: str | None = None
    extras: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _RowBase(ABC):
    """四つの状態の行に共通の欄と出力。--json の鍵の集合はここだけで組む。

    子の型は自分の状態と、状態ごとに変わる値を返す `_variable` だけを持つ。
    """

    status: ClassVar[LanguageStatus]

    name: str
    distribution: str | None
    version: str | None

    @property
    def is_builtin(self) -> bool:
        return self.distribution is None

    @abstractmethod
    def _variable(self) -> _RowVariable:
        """この状態の行で変わる値。"""

    def to_json(self) -> dict[str, object]:
        variable = self._variable()
        return {
            "name": self.name,
            "origin": "builtin" if self.is_builtin else "distribution",
            "distribution": self.distribution,
            "version": self.version,
            "state": self.status.value,
            "suffixes": list(variable.suffixes),
            "reason_kind": variable.reason_kind,
            "detail": variable.detail,
            **variable.extras,
        }

    def _head_line(self) -> str:
        if self.is_builtin:
            origin = "組み込み"
        else:
            origin = f"{self.distribution} {self.version or ''}".strip()
        suffixes = ",".join(self._variable().suffixes) or "-"
        return f"{self.name}\t{origin}\t{self.status.label}\t{suffixes}"


@dataclass(frozen=True)
class ReadyRow(_RowBase):
    """使える言語の行。対応の水準を持つのはこの型だけ。"""

    status: ClassVar[LanguageStatus] = LanguageStatus.READY

    suffixes: tuple[str, ...]
    support: LanguageSupport

    def _variable(self) -> _RowVariable:
        return _RowVariable(suffixes=self.suffixes, extras=self.support.to_json())

    def to_lines(self, figure_titles: dict[str, str]) -> list[str]:
        supported = "、".join(figure_titles[key] for key in self.support.supported_figures())
        unsupported = "、".join(figure_titles[key] for key in self.support.unsupported_figures())
        return [
            self._head_line(),
            f"    対応する図: {supported or '-'}",
            f"    対応外の図: {unsupported or '-'}",
        ]


@dataclass(frozen=True)
class NotEnabledRow(_RowBase):
    """有効でない言語の行。有効にするまで import しないので、拡張子も対応も分からない。"""

    status: ClassVar[LanguageStatus] = LanguageStatus.NOT_ENABLED

    def _variable(self) -> _RowVariable:
        return _RowVariable()

    def to_lines(self, figure_titles: dict[str, str]) -> list[str]:
        del figure_titles
        return [self._head_line()]


@dataclass(frozen=True)
class UnavailableRow(_RowBase):
    """使えない言語の行。読み込めなかったので、拡張子も対応も分からない。"""

    status: ClassVar[LanguageStatus] = LanguageStatus.UNAVAILABLE

    reason_kind: str
    detail: str

    def _variable(self) -> _RowVariable:
        return _RowVariable(reason_kind=self.reason_kind, detail=self.detail)

    def to_lines(self, figure_titles: dict[str, str]) -> list[str]:
        del figure_titles
        return [
            self._head_line(),
            f"    理由（{self.reason_kind}）: {self.detail}",
        ]


@dataclass(frozen=True)
class GrammarAbsentRow(_RowBase):
    """文法を取得していない言語の行。取得すれば使えるので、理由は持たず取得の案内を出す。

    使える行だけが拡張子を持つ約束に合わせ、拡張子は空にする。
    """

    status: ClassVar[LanguageStatus] = LanguageStatus.GRAMMAR_ABSENT

    @property
    def _fetch_command(self) -> str:
        return f"{FETCH_COMMAND} {self.name}"

    def _variable(self) -> _RowVariable:
        return _RowVariable(detail=f"{self._fetch_command} で文法を取得すると使えます")

    def to_lines(self, figure_titles: dict[str, str]) -> list[str]:
        del figure_titles
        return [self._head_line(), f"    取得: {self._fetch_command}"]


LanguageRow = ReadyRow | NotEnabledRow | GrammarAbsentRow | UnavailableRow


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    languages_parser = subparsers.add_parser(
        "languages", help="言語を一覧し、追加の言語を有効にし、外し、文法を取得します"
    )
    actions = languages_parser.add_subparsers(dest="action", required=True, metavar="<action>")

    list_parser = actions.add_parser("list", help="組み込みと追加の言語を一覧します")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=run_list)

    enable_parser = actions.add_parser("enable", help="追加の言語を有効にします")
    enable_parser.add_argument("name")
    enable_parser.add_argument(
        "--distribution",
        help="同じ名前の entry point が複数の配布にあるときに、使う配布を選びます",
    )
    enable_parser.add_argument("--json", action="store_true")
    enable_parser.set_defaults(handler=run_enable)

    fetch_parser = actions.add_parser(
        "fetch", help="組み込みと有効な追加の言語の文法を取得して照合します"
    )
    fetch_parser.add_argument(
        ACCEPT_TERMS_OPTION,
        dest="accept_terms",
        action="store_true",
        help="利用規約とプライバシーポリシーを読んで同意したものとして、尋ねずに取得します",
    )
    fetch_parser.add_argument(
        "languages",
        nargs="*",
        metavar="<言語>",
        help="名前を並べると、その言語の文法だけを取得します。何も書かなければすべてを取得します",
    )
    fetch_parser.set_defaults(handler=run_fetch)

    disable_parser = actions.add_parser("disable", help="追加の言語を外します")
    disable_parser.add_argument("name")
    disable_parser.add_argument(
        "--distribution", help="同じ名前が複数の配布で有効なときに、外す配布を選びます"
    )
    disable_parser.add_argument("--json", action="store_true")
    disable_parser.set_defaults(handler=run_disable)


def _external_entry_points() -> list[EntryPoint]:
    return list(entry_points(group=ENTRY_POINT_GROUP))


def _version_of(candidates: list[EntryPoint], name: str, distribution: str) -> str | None:
    """entry point の配布の版。メタデータだけを読み、モジュールは import しない。"""
    for candidate in candidates:
        if candidate.name != name or candidate.dist is None:
            continue
        if normalize_distribution(candidate.dist.name) == normalize_distribution(distribution):
            return candidate.dist.version
    return None


def _external_origin(plugin: LanguagePlugin) -> Distribution | None:
    """組み込みなら None。組み込みかの判定はここだけで行う。"""
    origin = plugin.origin
    return None if isinstance(origin, Builtin) else origin


def _is_builtin_name(name: str) -> bool:
    return any(
        isinstance(state, LanguageReady)
        and _external_origin(state.plugin) is None
        and state.plugin.adapter.name == name
        for state in registry.states()
    )


def _row_of(state: LanguageState, not_enabled_version: str | None) -> LanguageRow:
    """状態の型から行の型への写し。状態の型を足すと、最後の腕で型検査が落ちる。"""
    clean = sanitize_text
    match state:
        case LanguageReady(plugin=plugin):
            origin = _external_origin(plugin)
            return ReadyRow(
                name=clean(plugin.adapter.name),
                distribution=None if origin is None else clean(origin.name),
                version=None if origin is None else clean(origin.version),
                suffixes=tuple(sorted(plugin.adapter.suffixes)),
                support=support_of(plugin),
            )
        case LanguageNotEnabled():
            return NotEnabledRow(
                name=clean(state.name),
                distribution=clean(state.distribution),
                version=None if not_enabled_version is None else clean(not_enabled_version),
            )
        case LanguageGrammarAbsent():
            return GrammarAbsentRow(
                name=clean(state.name),
                distribution=None if state.distribution is None else clean(state.distribution),
                version=None if state.version is None else clean(state.version),
            )
        case LanguageUnavailable():
            return UnavailableRow(
                name=clean(state.name),
                distribution=None if state.distribution is None else clean(state.distribution),
                version=None if state.version is None else clean(state.version),
                reason_kind=str(state.reason_kind),
                detail=clean(state.detail),
            )
        case _ as unreachable:
            assert_never(unreachable)


def _rows() -> list[LanguageRow]:
    candidates = _external_entry_points()
    rows: list[LanguageRow] = []
    for state in registry.states():
        version = (
            _version_of(candidates, state.name, state.distribution)
            if isinstance(state, LanguageNotEnabled)
            else None
        )
        rows.append(_row_of(state, version))
    return rows


def run_list(args: argparse.Namespace, context: Context) -> int:
    del context
    rows = _rows()
    if args.json:
        body = {"languages": [row.to_json() for row in rows]}
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return EXIT_OK
    titles = {figure.key: figure.title for figure in REGISTRY}
    lines = [line for row in rows for line in row.to_lines(titles)]
    if any(isinstance(row, ReadyRow) for row in rows):
        lines.append(CAPABILITY_NOTICE)
    print_lines(lines)
    return EXIT_OK


def run_enable(args: argparse.Namespace, context: Context) -> int:
    name: str = args.name
    if _is_builtin_name(name):
        raise ValueError(f"{name!r} は組み込みの言語と同じ名前で、有効にできません")
    found = [(c, c.dist) for c in _external_entry_points() if c.name == name and c.dist]
    if not found:
        raise ValueError(f"entry point の群 {ENTRY_POINT_GROUP} に {name!r} がありません")
    if args.distribution is not None:
        wanted = normalize_distribution(args.distribution)
        found = [(c, d) for c, d in found if normalize_distribution(d.name) == wanted]
        if not found:
            raise ValueError(f"配布 {args.distribution!r} に {name!r} の entry point がありません")
    elif len(found) > 1:
        names = "、".join(sorted(d.name for _, d in found))
        raise ValueError(
            f"{name!r} は複数の配布にあります（{names}）。--distribution で選んでください"
        )
    entry_point, dist = found[0]
    path = data_home() / ENABLED_FILE
    try:
        content = read_content(dist)
    except RecordError as error:
        raise ValueError(f"{name!r} を有効にできません（{dist.name}）: {error}") from error
    entry = EnabledLanguage(name, dist.name, dist.version, content)
    current = _load_for_edit(path)
    if entry in current:
        # 承認済みなので、プラグインのコードを読み込む確認も書き直しも要らない
        return _report_enabled(entry, already=True, as_json=args.json)
    _confirm(context, entry, dist.version, _reapproval_reason(current, entry, content))
    problem = _verify(entry_point)
    if problem is not None:
        raise ValueError(f"{name!r} を有効にできません（{dist.name}）: {sanitize_text(problem)}")
    # 同じ言語と配布で古い版の承認が残っていれば、承認し直した版に置き換える
    kept = [
        item
        for item in current
        if not (item.name == entry.name and item.distribution == entry.distribution)
    ]
    ensure_data_home()
    save_enabled(path, [*kept, entry])
    return _report_enabled(entry, already=False, as_json=args.json)


def _reapproval_reason(
    current: Sequence[EnabledLanguage], entry: EnabledLanguage, content: ApprovedContent
) -> str | None:
    """同じ名前と配布と版の承認が既にあり、中身だけが違うときの理由。無ければ None。"""
    same = [
        item.content
        for item in current
        if (item.name, item.distribution, item.version)
        == (entry.name, entry.distribution, entry.version)
    ]
    if not same:
        # 同じ組の承認が無ければ初めての承認で、理由は要らない
        return None
    return reapproval_reason(same, {file.path: file.sha256 for file in content.files})


def _confirm(
    context: Context, entry: EnabledLanguage, version: str, reason: str | None = None
) -> None:
    """プラグインのコードを import する前に、信頼の注意と配布を示して y か yes を得る。

    reason があれば、確認を取り直す理由として配布の次に示す。
    """
    if not context.is_terminal():
        raise ConsentUnavailableError(
            "確認を得られないので追加の言語を読み込みません。端末から実行してください"
        )
    print(TRUST_NOTICE, file=sys.stderr)
    print(
        f"読み込む配布: {sanitize_text(entry.distribution)} {sanitize_text(version)}",
        file=sys.stderr,
    )
    if reason is not None:
        print(sanitize_text(reason), file=sys.stderr)
    if not is_yes(context.read_answer(f"続けますか{CONSENT_PROMPT_END}")):
        raise ConsentDeclinedError("確認が得られなかったので有効にしませんでした")


def _report_enabled(entry: EnabledLanguage, *, already: bool, as_json: bool) -> int:
    if as_json:
        # 出力の形は中身を記録する前から変えない。files は languages.json にだけ書く
        shown = {"name": entry.name, "distribution": entry.distribution, "version": entry.version}
        print(json.dumps({"enabled": shown}, ensure_ascii=False))
        return EXIT_OK
    prefix = "既に有効です" if already else "有効にしました"
    lines = [f"{prefix}: {entry.name}（{entry.distribution}）"]
    if not already:
        lines.append(GRAMMAR_NOTICE)
    print_lines(lines)
    return EXIT_OK


def _verify(entry_point: EntryPoint) -> str | None:
    """entry point を一度だけ読み込んで検証する。通れば None、通らなければ理由。"""
    try:
        plugin: object = entry_point.load()()
    except Exception as error:  # noqa: BLE001 プラグインが投げる型は絞れない
        # プラグインのコードが何を投げるかは分からない
        return f"読み込みに失敗しました: {type(error).__name__}: {error}"
    return validate_plugin(entry_point, plugin)


def _load_for_edit(path: Path) -> tuple[EnabledLanguage, ...]:
    try:
        return load_enabled(path)
    except EnabledFileError as error:
        raise registry.LanguagePluginUnavailableError(
            f"{error}\n{ENABLED_FILE} を直すか、消してからもう一度実行してください"
        ) from error


def run_fetch(args: argparse.Namespace, context: Context) -> int:
    # 知らない名前は、規約への同意を尋ねる前に退ける
    try:
        names = fetch_main.validated_names(args.languages) if args.languages else None
    except registry.LanguagePluginUnavailableError as error:
        # 名前の照合が有効化のファイルを読むため、ここでも取得と同じ形で知らせる
        sys.stderr.write(f"jigane: {sanitize_text(str(error))}\n")
        return EXIT_PROCESSING_FAILURE
    require_terms_consent(
        accepted=args.accept_terms,
        is_terminal=context.is_terminal,
        read_answer=context.read_answer,
    )
    try:
        outcome = (
            fetch_main.fetch_and_report() if names is None else fetch_main.fetch_and_report(names)
        )
    finally:
        # 取得の前に求めた状態は、文法の有無が古い
        registry.set_states(None)
    # 入口が違うので終了コードも違う。ここは CLI の終了コード表に従う
    match outcome:
        case fetch_main.FetchAborted():
            return EXIT_PROCESSING_FAILURE
        case fetch_main.Fetched(
            fetched_external=fetched_external,
            failed_languages=failed,
            fetched_builtin=fetched_builtin,
        ):
            print(f"取得して照合しました: {', '.join([*fetched_builtin, *fetched_external])}")
            return EXIT_PROCESSING_FAILURE if failed else EXIT_OK
        case _ as unreachable:
            assert_never(unreachable)


def run_disable(args: argparse.Namespace, context: Context) -> int:
    del context
    path = data_home() / ENABLED_FILE
    current = _load_for_edit(path)
    named = [item for item in current if item.name == args.name]
    if args.distribution is not None:
        wanted = normalize_distribution(args.distribution)
        named = [item for item in named if item.distribution == wanted]
    elif len(named) > 1:
        names = "、".join(sorted(item.distribution for item in named))
        raise ValueError(
            f"{args.name!r} は複数の配布で有効です（{names}）。--distribution で選んでください"
        )
    changed = bool(named)
    if changed:
        ensure_data_home()
        save_enabled(path, [item for item in current if item not in named])
    if args.json:
        print(json.dumps({"name": args.name, "removed": changed}, ensure_ascii=False))
        return EXIT_OK
    print(f"外しました: {args.name}" if changed else f"有効にしていません: {args.name}")
    return EXIT_OK
