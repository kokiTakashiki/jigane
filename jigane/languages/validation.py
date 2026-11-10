from importlib.metadata import EntryPoint

from jigane.languages.base import LanguageAdapter
from jigane.languages.data_places import overlap_error
from jigane.languages.plugin import PLUGIN_API_VERSION, ApiVersion, Distribution, LanguagePlugin


def _api_version_error(version: ApiVersion) -> str | None:
    if version.major != PLUGIN_API_VERSION.major:
        return (
            f"契約の major が違います（プラグイン {version.major}、"
            f"jigane {PLUGIN_API_VERSION.major}）"
        )
    if version.minor > PLUGIN_API_VERSION.minor:
        return (
            f"プラグインの契約の minor（{version.minor}）が jigane の"
            f"（{PLUGIN_API_VERSION.minor}）より新しい"
        )
    return None


def _adapter_shape_error(adapter: LanguageAdapter) -> str | None:
    # プラグインのコードの値は型注釈を信用できない。集合の演算や比較に使う前に型を確かめる
    if not isinstance(adapter.name, str):
        return "アダプタの name が文字列ではありません"
    if not isinstance(adapter.grammar, str):
        return "アダプタの grammar が文字列ではありません"
    suffixes: object = adapter.suffixes
    if not isinstance(suffixes, frozenset):
        return "アダプタの suffixes が frozenset ではありません"
    if not all(isinstance(s, str) and s.startswith(".") for s in suffixes):
        return "アダプタの suffixes の要素は . で始まる文字列でなければなりません"
    return None


def _origin_error(entry_point: EntryPoint, plugin: LanguagePlugin) -> str | None:
    """出どころの誤りの説明。entry point の配布と plugin.origin の突き合わせ。無ければ None。"""
    dist = entry_point.dist
    if dist is None:
        return "entry point の配布を特定できません"
    if plugin.origin != Distribution(name=dist.name, version=dist.version):
        return f"出どころが配布 {dist.name} {dist.version} と一致しません: {plugin.origin}"
    return None


def _adapter_error(entry_point: EntryPoint, plugin: LanguagePlugin) -> str | None:
    """アダプタの誤りの説明。型、形、名前、資料の重なりの順に見る。無ければ None。"""
    if not isinstance(plugin.adapter, LanguageAdapter):
        return "アダプタが LanguageAdapter を満たしません"
    shape_error = _adapter_shape_error(plugin.adapter)
    if shape_error is not None:
        return shape_error
    if plugin.adapter.name != entry_point.name:
        return f"アダプタの名前 {plugin.adapter.name} が entry point の名前と違います"
    if plugin.data is not None:
        return overlap_error(plugin.data)
    # data が無い言語は資料を足さないので、名前の重なりは起こらない
    return None


def validate_plugin(entry_point: EntryPoint, plugin: object) -> str | None:
    """単独で判定できる誤りの説明。無ければ None。"""
    if not isinstance(plugin, LanguagePlugin):
        return f"LanguagePlugin ではなく {type(plugin).__name__} を返しました"
    origin_error = _origin_error(entry_point, plugin)
    if origin_error is not None:
        return origin_error
    version_error = _api_version_error(plugin.api_version)
    if version_error is not None:
        return version_error
    return _adapter_error(entry_point, plugin)
