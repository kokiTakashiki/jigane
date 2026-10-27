from collections.abc import Sequence
from dataclasses import replace
from fnmatch import fnmatchcase
from importlib.metadata import entry_points
from importlib.resources.abc import Traversable
from pathlib import PurePosixPath

from jigane.languages import grammars
from jigane.languages.base import LanguageAdapter, collect_patterns, grammar_target
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.data_places import (
    BUILTIN_FLAKY_QUERIES_DIR,
    BUILTIN_QUERIES_DIR,
    PLUGIN_FLAKY_QUERIES_PARTS,
    PLUGIN_QUERIES_PARTS,
    descend,
)
from jigane.languages.java import JavaAdapter
from jigane.languages.plugin import (
    PLUGIN_API_VERSION,
    Builtin,
    Distribution,
    GrammarSpec,
    LanguagePlugin,
)
from jigane.languages.plugins import (
    ENABLED_FILE,
    ENTRY_POINT_GROUP,
    EnabledFileError,
    LanguageReady,
    LanguageState,
    LanguageUnavailable,
    ReasonKind,
    discover,
    load_enabled,
    sanitize_text,
)
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter
from jigane.storage.store import data_home


def _builtin(adapter: LanguageAdapter, label: str) -> LanguagePlugin:
    grammar = adapter.grammar
    recorded = grammars.recorded_hashes()
    hashes = {platform: names[grammar] for platform, names in recorded.items() if grammar in names}
    return LanguagePlugin(
        origin=Builtin(),
        api_version=PLUGIN_API_VERSION,
        adapter=adapter,
        label=label,
        grammar=GrammarSpec(pack_version=grammars.pack_version(), hashes=hashes),
        data=None,
    )


_plugins: tuple[LanguagePlugin, ...] = (
    _builtin(PythonAdapter(), "Python"),
    _builtin(JavaAdapter(), "Java"),
    _builtin(SwiftAdapter(), "Swift"),
    _builtin(CSharpAdapter(), "C#"),
)


class LanguagePluginUnavailableError(Exception):
    """有効にした外の言語を使えない。languages.json が壊れている場合と、言語が使えない場合。"""


_states: tuple[LanguageState, ...] | None = None


class _GrammarBound:
    """外の言語のアダプタを包み、照合に通った文法の対象を結ぶ。他の口は包んだアダプタへ委ねる。"""

    def __init__(self, inner: LanguageAdapter, target: grammars.GrammarTarget) -> None:
        self._inner = inner
        self.grammar_target = target

    def __getattr__(self, name: str) -> object:
        # 写しや再構成の途中は _inner がまだ無い。引くと __getattr__ が自分を呼び続ける
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)


def _discovered_states() -> tuple[LanguageState, ...]:
    try:
        enabled = load_enabled(data_home() / ENABLED_FILE)
    except EnabledFileError as error:
        raise LanguagePluginUnavailableError(
            f"{error}\n外す手順: {ENABLED_FILE} を直すか、消してからもう一度実行してください"
        ) from error
    return discover(entry_points(group=ENTRY_POINT_GROUP), enabled, _plugins)


def _external_plugins(states: tuple[LanguageState, ...]) -> list[LanguagePlugin]:
    builtin_ids = {id(plugin) for plugin in _plugins}
    return [
        state.plugin
        for state in states
        if isinstance(state, LanguageReady) and id(state.plugin) not in builtin_ids
    ]


def discovered_plugins() -> list[LanguagePlugin]:
    """文法の照合の前の、有効な外の言語。文法の取得は、照合に通らない言語の文法も対象にするために使う。"""
    return _external_plugins(_discovered_states())


def _check_grammars(states: tuple[LanguageState, ...]) -> tuple[LanguageState, ...]:
    """外の言語の文法を、その言語の宣言と照合する。誤りは言語の状態に載せる。"""
    external = {id(plugin): plugin for plugin in _external_plugins(states)}
    root = grammars.cache_root()
    checked: list[LanguageState] = []
    for state in states:
        plugin = external.get(id(state.plugin)) if isinstance(state, LanguageReady) else None
        if plugin is None:
            # 組み込みの言語は、読み込みのときに組み込みの記録で照合される
            checked.append(state)
            continue
        target = grammars.external_target(plugin)
        try:
            target.verify(root)
        except grammars.GrammarError as error:
            origin = plugin.origin
            owner = origin if isinstance(origin, Distribution) else None
            checked.append(
                LanguageUnavailable(
                    plugin.adapter.name,
                    ReasonKind.GRAMMAR_ERROR,
                    str(error),
                    None if owner is None else owner.name,
                    None if owner is None else owner.version,
                )
            )
        else:
            bound = replace(plugin, adapter=_GrammarBound(plugin.adapter, target))  # type: ignore[arg-type]
            checked.append(LanguageReady(bound))
    return tuple(checked)


def states() -> tuple[LanguageState, ...]:
    """言語の状態。プロセスの中で一度だけ求め、以後は同じ結果を返す。"""
    global _states  # noqa: PLW0603 -- set_states が同じ値を差し替える。テストが偽の状態を差し込む口を残す
    if _states is None:
        _states = _check_grammars(_discovered_states())
    return _states


def set_states(value: tuple[LanguageState, ...] | None) -> None:
    """状態を差し替える。None で次の `states()` が求め直す。テストが偽の状態を差し込む。"""
    global _states  # noqa: PLW0603 -- 一度だけ求める値を、テストが偽の状態で差し替える口
    _states = value


def require_ready() -> None:
    """組み込みの文法が無い、または有効にした外の言語に使えないものがあれば止める。起動のときに副コマンドの前で呼ぶ。"""
    root = grammars.cache_root()
    for plugin in _plugins:
        grammars.require_present(root, plugin.adapter.grammar)
    failed = [state for state in states() if isinstance(state, LanguageUnavailable)]
    if not failed:
        return
    lines = [
        f"言語 {state.name} を使えません（{state.reason_kind}）: {state.detail}\n"
        f"外す手順: jigane languages disable {state.name}"
        for state in failed
    ]
    # 名前も詳細も外のコードが決めた文字列で、端末へ出る前に制御文字を除く
    raise LanguagePluginUnavailableError(sanitize_text("\n".join(lines)))


def _all_plugins() -> list[LanguagePlugin]:
    # 組み込みが先頭に並ぶ。拡張子の重なりは先に並んだ組み込みが勝つ
    ready = [state.plugin for state in states() if isinstance(state, LanguageReady)]
    builtin_ids = {id(plugin) for plugin in _plugins}
    return [*_plugins, *(plugin for plugin in ready if id(plugin) not in builtin_ids)]


def builtin_plugins() -> list[LanguagePlugin]:
    return list(_plugins)


def adapters() -> list[LanguageAdapter]:
    return [plugin.adapter for plugin in _all_plugins()]


def label_for(adapter_name: str) -> str:
    """アダプタの name から利用者に見せる言語名。登録に無い name はそのまま返す。"""
    for plugin in _all_plugins():
        if plugin.adapter.name == adapter_name:
            return plugin.label
    return adapter_name


def is_builtin(adapter_name: str) -> bool:
    """アダプタの name の言語が同梱されたものか。

    外から入れたプラグインと、
    登録に無い name は偽。
    """
    return any(
        isinstance(plugin.origin, Builtin) and plugin.adapter.name == adapter_name
        for plugin in _all_plugins()
    )


def _external_data(parts: tuple[str, ...]) -> list[Traversable]:
    return [
        descend(plugin.data, parts)
        for plugin in _all_plugins()
        if plugin.data is not None and plugin not in _plugins
    ]


def query_places() -> tuple[Traversable, ...]:
    """照会の置き場の一覧。本体が先頭で、有効な外の言語が続く。"""
    return (BUILTIN_QUERIES_DIR, *_external_data(PLUGIN_QUERIES_PARTS))


def flaky_places() -> tuple[Traversable, ...]:
    """flaky の型の置き場の一覧。本体が先頭で、有効な外の言語が続く。"""
    return (BUILTIN_FLAKY_QUERIES_DIR, *_external_data(PLUGIN_FLAKY_QUERIES_PARTS))


def grammar_target_named(grammar: str) -> grammars.GrammarTarget:
    """文法名から、照合の対象を引く。照合に通った外の言語の文法名はその宣言の対象になる。"""
    for plugin in _all_plugins():
        if plugin.adapter.grammar == grammar:
            return grammar_target(plugin.adapter)
    # どの言語も名乗らない文法名は組み込みの記録で照合され、未知の文法として止まる
    return grammars.BuiltinGrammar(grammar)


def adapter_for(path: str) -> LanguageAdapter | None:
    suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
    for plugin in _all_plugins():
        if suffix in plugin.adapter.suffixes:
            return plugin.adapter
    return None


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(name, pattern) for pattern in patterns)


# どの言語のアダプタにも属さない、ビルドと CI の定義
COMMON_BUILD_FILE_PATTERNS = ("Makefile", "*.mk", "justfile", "Dockerfile")
COMMON_BUILD_PATH_PATTERNS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "*/.github/workflows/*.yml",
    "*/.github/workflows/*.yaml",
)


def is_test_path(path: str, adapters: Sequence[LanguageAdapter]) -> bool:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    directories = collect_patterns(adapters, "test_directory_names")
    files = collect_patterns(adapters, "test_file_patterns")
    return any(_matches(part, directories) for part in parts[:-1]) or _matches(parts[-1], files)


def is_build_path(path: str, adapters: Sequence[LanguageAdapter]) -> bool:
    # pyproject.toml など、ビルドの定義は言語の拡張子を持たない。
    # そのため言語を問わず全アダプタのパターンで判定する
    posix = path.replace("\\", "/")
    name = PurePosixPath(posix).name
    return (
        _matches(name, collect_patterns(adapters, "build_file_patterns"))
        or _matches(name, COMMON_BUILD_FILE_PATTERNS)
        or _matches(posix, COMMON_BUILD_PATH_PATTERNS)
    )
